#!/usr/bin/env python3
"""R2-backed entry point for SpicyChat Archive.

If Cloudflare R2 credentials are absent, or the one-time migration has not been
completed, this falls back to the existing local/Git storage implementation in
archive.py. After migration, full bot JSON, images, crawler state and ranking
snapshots live in R2 instead of Git.
"""
from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

import archive as legacy
from storage_r2 import R2ArchiveStore, StorageQuotaExceeded
from rich_field_index import SCHEMA_VERSION as RICH_FIELDS_VERSION, field_flags, flush_index as flush_rich_field_index, note_record as note_rich_field_record
from site_indexes import flush_indexes as flush_site_indexes, note_record as note_site_record


ROOT = Path(__file__).resolve().parents[1]


def _priority_add(state: dict[str, Any], key: str, bot_id: str, limit: int = 20000) -> None:
    q = state.setdefault(key, [])
    if bot_id not in q:
        q.append(bot_id)
    # These are only for re-checks after edits/transient failures. New bots use
    # sequential cursors, so the priority list should never become a million-ID queue.
    if len(q) > limit:
        del q[: len(q) - limit]


def _cloud_summary(record: dict[str, Any]) -> dict[str, Any]:
    lk = record.get("lastKnown") or {}
    status = record.get("status") or {}
    metrics = (record.get("metrics") or {}).get("latest") or {}
    avatar = record.get("avatarArchive") or {}
    return {
        "id": record["id"],
        "name": lk.get("name") or lk.get("title") or "Unknown bot",
        "title": lk.get("title") or "",
        "creator": lk.get("creator_username") or lk.get("creator") or "",
        "tags": lk.get("tags") if isinstance(lk.get("tags"), list) else [],
        "status": status.get("current") or "unknown",
        "statusSince": status.get("since"),
        "firstSeenAt": record.get("firstSeenAt"),
        "lastSeenAt": record.get("lastSeenAt"),
        "isNsfw": bool(lk.get("is_nsfw") or lk.get("avatar_is_nsfw")),
        "avatar": avatar.get("publicUrl") or legacy.normalize_avatar_url(lk.get("avatar_url") or lk.get("avatar") or lk.get("image")),
        "avatarArchived": bool(avatar.get("publicUrl") or avatar.get("r2Key")),
        "messages": metrics.get("num_messages"),
        "messages24h": metrics.get("num_messages_24h"),
        "rating": metrics.get("rating_score"),
        "createdAt": lk.get("createdAt"),
        "updatedAt": lk.get("updatedAt"),
        "summarySchemaVersion": 2,
        "savedFields": field_flags(record),
        "richFieldsVersion": RICH_FIELDS_VERSION,
        "definitionVisible": lk.get("definition_visible") if isinstance(lk.get("definition_visible"), bool) else None,
        "definitionSize": str(lk.get("definition_size_category") or "").lower(),
        "hasLorebooks": lk.get("has_lorebooks") if isinstance(lk.get("has_lorebooks"), bool) else None,
        "language": str(lk.get("language") or "").lower(),
    }


def _refresh_deleted_summary_metadata(store: R2ArchiveStore) -> int:
    """One-time refresh for older deleted summaries after public filter metadata expands."""
    pending = [
        bot_id for bot_id, summary in store.deleted_index.items()
        if (
            int((summary or {}).get("summarySchemaVersion") or 0) < 2
            or int((summary or {}).get("richFieldsVersion") or 0) < RICH_FIELDS_VERSION
        )
    ]
    if not pending:
        return 0

    print(f"deleted summary metadata: refreshing {len(pending):,} older archived rows...", flush=True)
    updated = 0

    def load_one(bot_id: str):
        try:
            return bot_id, store.get_json(store.bot_key(bot_id), None)
        except Exception:
            return bot_id, None

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(load_one, bot_id) for bot_id in pending]
        for future in as_completed(futures):
            bot_id, record = future.result()
            if not isinstance(record, dict):
                continue
            if (record.get("status") or {}).get("current") != "deleted":
                continue
            store.set_deleted_summary(bot_id, _cloud_summary(record))
            updated += 1

    print(f"deleted summary metadata: refreshed {updated:,}/{len(pending):,}.", flush=True)
    return updated


def _collapse_current_sources(record: dict[str, Any]) -> dict[str, Any]:
    """Remove old duplicated per-listing Typesense snapshots without losing lastKnown/history."""
    current = record.get("current")
    if not isinstance(current, dict):
        return record
    ts = []
    for key, value in current.items():
        if key == "typesense" or key.startswith("typesense:"):
            if isinstance(value, dict):
                ts.append(value)
    if not ts:
        return record
    merged: dict[str, Any] = {}
    for value in ts:
        merged = legacy.merge_last_known(merged, value)
    next_current = {k: v for k, v in current.items() if not (k == "typesense" or k.startswith("typesense:"))}
    next_current["typesense"] = merged
    record["current"] = next_current
    return record


def configure_cloud(config: dict[str, Any], store: R2ArchiveStore):
    state = store.load_state()
    store.load_discovery_order()
    store.load_deleted_index()

    bloom_bytes = int((config.get("storage") or {}).get("r2", {}).get("bloom_bytes") or 8 * 1024 * 1024)
    bloom_hashes = int((config.get("storage") or {}).get("r2", {}).get("bloom_hashes") or 7)
    bloom = store.load_bloom(size_bytes=bloom_bytes, hashes=bloom_hashes)

    original_read_json = legacy.read_json
    original_write_json = legacy.write_json_if_changed
    state_path = legacy.STATE_PATH

    # Keep R2 state out of Git entirely. archive.py can keep its normal run flow,
    # but reads/writes to STATE_PATH are redirected to this in-memory object.
    def cloud_read_json(path: Path, default: Any):
        if Path(path) == Path(state_path):
            return state
        return original_read_json(path, default)

    def cloud_write_json_if_changed(path: Path, data: Any, *, indent: int | None = 2) -> bool:
        if Path(path) == Path(state_path):
            if data is not state and isinstance(data, dict):
                state.clear()
                state.update(deepcopy(data))
            return True
        return original_write_json(path, data, indent=indent)

    legacy.read_json = cloud_read_json
    legacy.write_json_if_changed = cloud_write_json_if_changed

    def load_bot(bot_id: str):
        record = store.load_bot(bot_id)
        return _collapse_current_sources(record) if record else None

    def save_bot(record: dict[str, Any]) -> bool:
        record = _collapse_current_sources(record)
        result = store.save_bot(record)
        note_rich_field_record(store, record)
        note_site_record(store, record)
        status = (record.get("status") or {}).get("current")
        if status == "deleted":
            store.set_deleted_summary(record["id"], _cloud_summary(record))
        elif status == "public":
            store.clear_deleted_summary(record["id"])
        return result

    legacy.load_bot = load_bot
    legacy.save_bot = save_bot

    def cloud_ingest_documents(
        docs: Iterable[dict[str, Any]], *, source: str, at: str, state: dict[str, Any]
    ) -> tuple[int, int, set[str]]:
        new_count = 0
        changed_count = 0
        seen: set[str] = set()
        observe_source = "typesense" if source.startswith("typesense:") else source

        for doc in docs:
            bot_id = legacy.normalize_id(doc)
            if not bot_id:
                continue
            seen.add(bot_id)
            existing = load_bot(bot_id)
            old_known = (existing or {}).get("lastKnown") or {}
            old_updated = old_known.get("updatedAt")
            old_definition_visible = old_known.get("definition_visible")
            old_avatar = legacy.normalize_avatar_url(old_known.get("avatar_url") or old_known.get("avatar") or old_known.get("image"))

            observed_doc = doc
            # Do not rewrite thousands of per-bot objects every three hours just
            # because counters moved. Ranking snapshots below preserve compact
            # listing metrics; each bot keeps its initial/enriched metrics.
            if existing is not None and source.startswith("typesense:"):
                observed_doc = {k: v for k, v in doc.items() if k not in legacy.VOLATILE_FIELDS}

            record, changed = legacy.observe_bot(existing, observed_doc, source=observe_source, at=at)
            # Preserve which public surface observed it without duplicating the raw
            # Typesense document four or five times in current{}.
            record.setdefault("sources", {})[source] = {"lastSeenAt": at}

            if existing is None:
                new_count += 1
            else:
                if legacy.meaningful(doc.get("updatedAt")) and doc.get("updatedAt") != old_updated:
                    _priority_add(state, "priorityEnrichment", bot_id)
                if "definition_visible" in doc and doc.get("definition_visible") != old_definition_visible:
                    _priority_add(state, "priorityEnrichment", bot_id)

            avatar = legacy.normalize_avatar_url(doc.get("avatar_url") or doc.get("avatar") or doc.get("image"))
            if avatar and (existing is None or avatar != old_avatar):
                # Images remain lower priority than metadata, but newly discovered
                # bots are still eligible for gradual permanent avatar archiving.
                # The queue is bounded and the R2 media/storage guards stop it well
                # before the free-tier storage ceiling.
                _priority_add(state, "priorityImages", bot_id)

            if changed and save_bot(record):
                changed_count += 1

        return new_count, changed_count, seen

    def cloud_scan_listing(client, config, name: str, sort_by: str, at: str, state: dict[str, Any]):
        page_size = min(250, int(config["crawler"].get("listing_page_size", 250)))
        max_hits = int(config["crawler"].get("listing_max_hits", 2500))
        docs: list[dict[str, Any]] = []
        found = None
        page = 1
        while len(docs) < max_hits:
            req = legacy.typesense_search(config, page=page, per_page=min(page_size, max_hits - len(docs)), sort_by=sort_by)
            response = client.multi_search([req])
            if not response.ok:
                return {"ok": False, "error": response.error, "status": response.status, "count": len(docs), "ids": []}
            result = (response.data.get("results") or [{}])[0]
            hits, found_now = legacy.extract_hits(result)
            if found is None:
                found = found_now
            if not hits:
                break
            docs.extend(hits)
            if len(hits) < req["per_page"]:
                break
            page += 1

        new, changed, _ = cloud_ingest_documents(docs, source=f"typesense:{name}", at=at, state=state)
        store.flush_discovery_journal()
        ids = [legacy.normalize_id(d) for d in docs]
        ids = [x for x in ids if x]
        # Current ranks and volatile counters are archived compactly in one ranking
        # snapshot object, instead of forcing one R2 write per bot every three hours.
        metric_keys = ("num_messages", "num_messages_24h", "rating_score", "rating_count", "token_count")
        compact_metrics = {}
        for doc in docs:
            bot_id = legacy.normalize_id(doc)
            if not bot_id:
                continue
            row = {k: doc.get(k) for k in metric_keys if k in doc and legacy.meaningful(doc.get(k))}
            if row:
                compact_metrics[bot_id] = row
        return {
            "ok": True,
            "found": found,
            "count": len(ids),
            "new": new,
            "changed": changed,
            "ids": ids,
            "metrics": compact_metrics,
        }

    legacy.scan_listing = cloud_scan_listing

    def finish_exploration_pass(at: str) -> int:
        checks = state.setdefault("missingChecks", {})
        queued = 0
        deleted_ids = set(store.deleted_index)
        for bot_id in store.discovery_order:
            if bot_id in deleted_ids:
                continue
            if bot_id not in bloom:
                if bot_id not in checks:
                    checks[bot_id] = {
                        "count": 0,
                        "lastAt": None,
                        "lastStatus": None,
                        "reason": "absent-from-complete-typesense-pass",
                        "queuedAt": at,
                    }
                    queued += 1
        bloom.clear()
        return queued

    def cloud_explore_more(client, config, at: str, state: dict[str, Any]):
        exploration = state.setdefault("exploration", {})
        mode = exploration.get("mode") or "page"
        page_size = 250
        pages_budget = int(config["crawler"].get("explore_pages_per_run", 20))
        total_new = total_changed = total_hits = 0
        errors: list[str] = []
        missing_queued = 0

        discovery_write_soft_limit = int(
            (config.get("storage") or {}).get("r2", {}).get("discovery_write_soft_limit") or 700000
        )

        for _ in range(pages_budget):
            usage = store.storage_usage()
            if int(usage.get("writesThisMonth") or 0) >= discovery_write_soft_limit:
                errors.append(
                    f"discovery paused for free-tier operation safety at "
                    f"{int(usage.get('writesThisMonth') or 0):,} R2 writes this month"
                )
                break
            if mode == "cursor" and exploration.get("cursorCreatedAt"):
                cursor = exploration["cursorCreatedAt"]
                base_filter = config["typesense"]["application_filter"]
                filter_by = f"{base_filter} && createdAt:<{cursor}"
                req = legacy.typesense_search(config, page=1, per_page=page_size, sort_by="createdAt:desc", filter_by=filter_by)
            else:
                page_no = int(exploration.get("page") or 1)
                req = legacy.typesense_search(config, page=page_no, per_page=page_size, sort_by="createdAt:desc")

            response = client.multi_search([req])
            if not response.ok:
                errors.append(response.error or f"HTTP {response.status}")
                break
            result = (response.data.get("results") or [{}])[0]
            hits, found = legacy.extract_hits(result)
            exploration["lastFound"] = found

            if not hits:
                if mode == "page" and found and (int(exploration.get("page") or 1) - 1) * page_size < found:
                    exploration["blockedAtResultCap"] = True
                    cursor = exploration.get("lastCreatedAt")
                    if cursor is not None:
                        mode = exploration["mode"] = "cursor"
                        exploration["cursorCreatedAt"] = cursor
                        continue
                missing_queued += finish_exploration_pass(at)
                exploration["pass"] = int(exploration.get("pass") or 0) + 1
                exploration["page"] = 1
                exploration["mode"] = mode = "page"
                exploration["cursorCreatedAt"] = None
                exploration["lastCreatedAt"] = None
                break

            new, changed, seen_ids = cloud_ingest_documents(hits, source="typesense:explore", at=at, state=state)
            store.flush_discovery_journal()
            for bot_id in seen_ids:
                bloom.add(bot_id)
            total_new += new
            total_changed += changed
            total_hits += len(hits)

            last_created = hits[-1].get("createdAt")
            if last_created is not None:
                exploration["lastCreatedAt"] = last_created

            if mode == "cursor":
                if last_created is None or last_created == exploration.get("cursorCreatedAt"):
                    errors.append("cursor discovery could not advance createdAt")
                    break
                exploration["cursorCreatedAt"] = last_created
            else:
                exploration["page"] = int(exploration.get("page") or 1) + 1

            if len(hits) < page_size:
                missing_queued += finish_exploration_pass(at)
                exploration["pass"] = int(exploration.get("pass") or 0) + 1
                exploration["page"] = 1
                exploration["mode"] = mode = "page"
                exploration["cursorCreatedAt"] = None
                exploration["lastCreatedAt"] = None
                break

        return {
            "hits": total_hits,
            "new": total_new,
            "changed": total_changed,
            "mode": mode,
            "missingQueued": missing_queued,
            "errors": errors,
        }

    legacy.explore_more = cloud_explore_more

    def cloud_enrich_queue(client, config, at: str, state: dict[str, Any]):
        budget = int(config["crawler"].get("enrichment_budget", 200))
        processed = enriched = missing = restricted = 0
        priority = list(dict.fromkeys(state.setdefault("priorityEnrichment", [])))
        cursor = max(0, int(state.get("enrichmentCursor") or 0))

        selected: list[tuple[str, bool]] = []
        for bot_id in priority[:budget]:
            selected.append((bot_id, False))
        remaining_priority = priority[budget:]
        selected_ids = {bot_id for bot_id, _ in selected}
        while len(selected) < budget and cursor < len(store.discovery_order):
            bot_id = store.discovery_order[cursor]
            cursor += 1
            if bot_id in selected_ids:
                continue
            selected_ids.add(bot_id)
            selected.append((bot_id, True))

        retry: list[str] = []
        for bot_id, _sequential in selected:
            processed += 1
            response = client.character(bot_id)
            if response.ok:
                payload = legacy.unwrap_character_payload(response.data)
                if payload:
                    payload.setdefault("character_id", bot_id)
                    record = load_bot(bot_id)
                    record, changed = legacy.observe_bot(record, payload, source="character-api", at=at)
                    if changed:
                        save_bot(record)
                    enriched += 1
                continue
            if response.status == 404:
                missing += 1
                state.setdefault("missingChecks", {}).setdefault(bot_id, {"count": 0, "lastAt": None, "lastStatus": None})
                continue
            if response.status in {401, 403}:
                restricted += 1
                record = load_bot(bot_id)
                if record and (record.get("status") or {}).get("current") == "public":
                    record.setdefault("sources", {})["character-api"] = {"lastAttemptAt": at, "httpStatus": response.status}
                    save_bot(record)
                continue
            retry.append(bot_id)

        state["enrichmentCursor"] = cursor
        state["priorityEnrichment"] = []
        for bot_id in [*remaining_priority, *retry]:
            _priority_add(state, "priorityEnrichment", bot_id)
        return {"processed": processed, "enriched": enriched, "missing": missing, "restricted": restricted}

    legacy.enrich_queue = cloud_enrich_queue

    def cloud_archive_images(client, config, at: str, state: dict[str, Any]):
        if not config["crawler"].get("image_archive_enabled", True):
            return {"processed": 0, "saved": 0, "failed": 0, "quotaPaused": False}
        budget = int(config["crawler"].get("image_budget", 100))
        policy = str(config["crawler"].get("image_archive_policy") or "priority-only").lower()
        processed = saved = failed = 0
        quota_paused = False
        priority = list(dict.fromkeys(state.setdefault("priorityImages", [])))
        cursor = max(0, int(state.get("imageCursor") or 0))

        selected: list[tuple[str, bool]] = []
        for bot_id in priority[:budget]:
            selected.append((bot_id, False))
        remaining_priority = priority[budget:]
        selected_ids = {bot_id for bot_id, _ in selected}

        # "priority-only" is the free-tier-safe default: active bots keep their
        # durable CDN URL, while changed/deleted avatars are copied to R2.
        if policy not in {"priority-only", "priority"}:
            while len(selected) < budget and cursor < len(store.discovery_order):
                bot_id = store.discovery_order[cursor]
                cursor += 1
                if bot_id in selected_ids:
                    continue
                selected_ids.add(bot_id)
                selected.append((bot_id, True))

        retry: list[str] = []
        for index, (bot_id, _sequential) in enumerate(selected):
            processed += 1
            record = load_bot(bot_id)
            if not record:
                continue
            last = record.get("lastKnown") or {}
            url = legacy.normalize_avatar_url(last.get("avatar_url") or last.get("avatar") or last.get("image"))
            if not url:
                continue
            existing = record.get("avatarArchive") or {}
            if existing.get("originalUrl") == url and (existing.get("publicUrl") or existing.get("r2Key")):
                continue
            result = client.image(url)
            if not result:
                failed += 1
                retry.append(bot_id)
                continue
            content, ctype = result
            try:
                info = store.save_image(content, ctype, url)
            except StorageQuotaExceeded as exc:
                print(f"Image archiving paused by R2 quota: {exc}")
                quota_paused = True
                # Keep this and every unprocessed priority item for a later run in
                # case the quota/config changes; do not keep downloading images now.
                retry.extend([bot_id, *[x for x, _ in selected[index + 1 :]]])
                break
            record["avatarArchive"] = {
                "originalUrl": url,
                "sha256": info["sha256"],
                "contentType": ctype,
                "bytes": len(content),
                "r2Key": info["key"],
                "publicUrl": info.get("publicUrl"),
                "archivedAt": at,
            }
            save_bot(record)
            saved += 1

        state["imageCursor"] = cursor
        state["priorityImages"] = []
        for bot_id in [*remaining_priority, *retry]:
            _priority_add(state, "priorityImages", bot_id)
        return {"processed": processed, "saved": saved, "failed": failed, "quotaPaused": quota_paused}

    legacy.archive_images = cloud_archive_images

    def cloud_verify_missing(client, config, at: str, state: dict[str, Any], seen_public: set[str]):
        checks = state.setdefault("missingChecks", {})
        required = max(2, int(config["crawler"].get("deleted_confirmations_required", 2)))
        budget = int(config["crawler"].get("missing_verification_budget", 100))
        verified = deleted = restored = 0

        for bot_id in list(checks)[:budget]:
            info = checks[bot_id]
            if bot_id in seen_public:
                record = load_bot(bot_id)
                if not record or (record.get("status") or {}).get("current") != "deleted":
                    checks.pop(bot_id, None)
                    if record and (record.get("status") or {}).get("current") == "public":
                        store.clear_deleted_summary(bot_id)
                    continue
                # A Typesense/listing hit is only a restoration candidate.
                # Keep the deleted state until the Character API confirms 200.
            response = client.character(bot_id)
            verified += 1
            if response.ok:
                payload = legacy.unwrap_character_payload(response.data)
                record = load_bot(bot_id)
                was_deleted = bool(record and (record.get("status") or {}).get("current") == "deleted")
                if payload:
                    payload.setdefault("character_id", bot_id)
                    record, _ = legacy.observe_bot(record, payload, source="character-api", at=at)
                    save_bot(record)
                checks.pop(bot_id, None)
                if was_deleted and record and (record.get("status") or {}).get("current") == "public":
                    store.clear_deleted_summary(bot_id)
                    restored += 1
                continue
            if response.status == 404:
                info["count"] = int(info.get("count") or 0) + 1
                info["lastAt"] = at
                info["lastStatus"] = 404
                if info["count"] >= required:
                    record = load_bot(bot_id)
                    if record and (record.get("status") or {}).get("current") != "deleted":
                        record.setdefault("availabilityHistory", []).append({"status": "deleted", "from": at, "source": "character-api:repeated-404"})
                        record["status"] = {
                            "current": "deleted",
                            "since": at,
                            "lastVerifiedAt": at,
                            "evidence": f"{info['count']} repeated public character API 404s",
                        }
                        save_bot(record)
                        # Images are not copied for every active bot. Once a bot is
                        # confirmed deleted, immediately prioritize its last-known avatar.
                        _priority_add(state, "priorityImages", bot_id)
                        store.set_deleted_summary(bot_id, _cloud_summary(record))
                        deleted += 1
                    checks.pop(bot_id, None)
                continue
            info["lastAt"] = at
            info["lastStatus"] = response.status

        return {"verified": verified, "deleted": deleted, "restored": restored}

    legacy.verify_missing = cloud_verify_missing

    def cloud_save_ranking_snapshot(listings: dict[str, Any], at: str) -> bool:
        compact = {
            "schemaVersion": 2,
            "capturedAt": at,
            "listings": {name: info.get("ids", []) for name, info in listings.items() if info.get("ok")},
            "metrics": {name: info.get("metrics", {}) for name, info in listings.items() if info.get("ok")},
        }
        prior = store.load_latest_ranking()
        if prior.get("listings") == compact["listings"] and prior.get("metrics") == compact["metrics"]:
            return False
        safe = at.replace(":", "").replace("-", "")
        store.save_ranking_snapshot(compact, safe)
        return True

    legacy.save_ranking_snapshot = cloud_save_ranking_snapshot

    def cloud_build_site_data(at: str, listings: dict[str, Any] | None = None):
        legacy.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Old local-mode generated data can be enormous. R2 mode browses active
        # characters directly through the public Typesense endpoint and fetches
        # archived detail records from R2.
        for stale in (legacy.SITE_DATA_DIR / "catalog", legacy.SITE_DATA_DIR / "bots", ROOT / "media"):
            if stale.exists():
                shutil.rmtree(stale)

        prior = original_read_json(legacy.SITE_DATA_DIR / "manifest.json", {})
        public_counts = [int(v.get("found") or 0) for v in (listings or {}).values() if v.get("ok") and v.get("found") is not None]
        active = max(public_counts) if public_counts else int((state.get("exploration") or {}).get("lastFound") or prior.get("activeBots") or 0)
        _refresh_deleted_summary_metadata(store)
        deleted_url = store.publish_deleted_index()
        usage = store.storage_usage()
        guard_status = original_read_json(ROOT / "data" / "r2-usage.json", {})
        manifest = {
            "schemaVersion": 3,
            "generatedAt": at,
            "totalBots": len(store.discovery_order),
            "activeBots": active,
            "deletedBots": len(store.deleted_index),
            "restrictedBots": 0,
            "missingBots": len(state.get("missingChecks") or {}),
            "tagCount": prior.get("tagCount") or 0,
            "topTags": prior.get("topTags") or [],
            "listings": {k: v.get("ids", []) for k, v in (listings or {}).items() if v.get("ok")},
            "lastScan": at,
            "storageMode": "r2",
            "quotaGuard": {
                "selectedMode": guard_status.get("selectedMode"),
                "reasonCodes": guard_status.get("reasonCodes") or [],
                "r2ReadAllowed": guard_status.get("r2ReadAllowed", True),
                "checkedAt": guard_status.get("checkedAt"),
            },
            "storage": {
                "usedBytes": usage.get("totalBytes", 0),
                "mediaBytes": usage.get("mediaBytes", 0),
                "hardLimitBytes": store.hard_limit_bytes,
                "warningBytes": store.warning_bytes,
                "mediaLimitBytes": store.media_limit_bytes,
                "writesThisMonth": usage.get("writesThisMonth", 0),
            },
            "exploration": {
                "mode": (state.get("exploration") or {}).get("mode") or "page",
                "page": (state.get("exploration") or {}).get("page") or 1,
                "pass": (state.get("exploration") or {}).get("pass") or 0,
                "found": (state.get("exploration") or {}).get("lastFound"),
                "cursorCreatedAt": (state.get("exploration") or {}).get("cursorCreatedAt"),
                "lastCreatedAt": (state.get("exploration") or {}).get("lastCreatedAt"),
            },
        }
        runtime = {
            "schemaVersion": 1,
            "storageMode": "r2",
            "r2ReadAllowed": guard_status.get("r2ReadAllowed", True),
            "publicDataBaseUrl": store.public_base_url,
            "deletedIndexUrl": deleted_url,
            "typesense": {
                "url": config["typesense"]["primary_url"],
                "fallbackUrls": config["typesense"].get("fallback_urls") or [],
                "apiKey": config["typesense"]["public_search_key"],
                "collection": config["typesense"]["collection"],
                "queryBy": config["typesense"]["query_by"],
                "baseFilter": config["typesense"]["application_filter"],
            },
            "sorts": config.get("listings") or {},
        }
        original_write_json(legacy.SITE_DATA_DIR / "manifest.json", manifest)
        original_write_json(legacy.SITE_DATA_DIR / "runtime.json", runtime)
        return manifest

    legacy.build_site_data = cloud_build_site_data

    return state, bloom


def run() -> int:
    config = legacy.load_config()
    # r2_guard.py can throttle a run without editing config.json. This lets the
    # workflow react immediately to monthly R2 usage.
    env_pages = os.environ.get("SPICYCHAT_ARCHIVE_EXPLORE_PAGES", "").strip()
    if env_pages:
        try:
            config.setdefault("crawler", {})["explore_pages_per_run"] = max(0, int(env_pages))
        except ValueError:
            pass
    if os.environ.get("SPICYCHAT_ARCHIVE_DISABLE_IMAGES", "").strip().lower() in {"1", "true", "yes"}:
        config.setdefault("crawler", {})["image_archive_enabled"] = False
    storage = config.get("storage") or {}
    mode = str(storage.get("mode") or "auto").lower()

    if mode == "local":
        print("SpicyChat Archive storage: local/Git mode")
        return legacy.run()

    if not R2ArchiveStore.credentials_present():
        if mode == "r2":
            print("ERROR: config.json requires R2 storage, but CLOUDFLARE_R2_* credentials are missing.")
            return 2
        print("SpicyChat Archive storage: local/Git mode (R2 not configured yet)")
        return legacy.run()

    store = R2ArchiveStore(config)
    if not store.is_migrated():
        if mode == "r2":
            print("ERROR: R2 mode is active but the migration marker is missing. Refusing to start a new local archive.")
            return 2
        print("R2 credentials found, but the archive has not been migrated yet. Using local/Git mode until the migration workflow succeeds.")
        return legacy.run()

    print(f"SpicyChat Archive storage: Cloudflare R2 bucket {store.bucket}")
    state, bloom = configure_cloud(config, store)
    rc = legacy.run()

    # Persist only compact state objects to R2. The Git repo no longer receives
    # millions of bot JSON/image files or an ever-growing UUID queue.
    store.save_state(state)
    store.save_discovery_order()
    store.save_deleted_index()
    store.save_bloom(bloom)
    flush_rich_field_index(store)
    flush_site_indexes(store)
    store.flush_usage(force=True)
    usage = store.storage_usage()
    print(
        f"R2 usage: {usage.get('totalBytes', 0):,}/{store.hard_limit_bytes:,} bytes "
        f"(media {usage.get('mediaBytes', 0):,}/{store.media_limit_bytes:,}; "
        f"writes this month {usage.get('writesThisMonth', 0):,})"
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(run())
