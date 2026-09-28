#!/usr/bin/env python3
"""Discovery-first R2 archive runner.

v4 keeps the compact fingerprint architecture, but narrows fingerprints to
metadata that actually matters for a bot. SpicyChat's updatedAt, counters and
backend bookkeeping are intentionally ignored.

It also adds:
- adaptive discovery (+1 page after a healthy successful run, capped);
- a discovery wall-clock limit so scheduled runs cannot grow forever;
- newly discovered bots prioritized for richer character-API enrichment;
- persistent full scan/growth history in R2 plus a small public data/stats.json;
- noisy updatedAt values excluded from field-history changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

import archive as legacy
import archive_cloud as cloud
from storage_r2 import R2ArchiveStore


FINGERPRINT_SCHEMA = 4
READ_WORKERS = 16
BOOTSTRAP_MAX_READS = 25_000

UPDATE_HINT_FIELDS = {
    "updatedAt",
    "updated_at",
    "lastUpdatedAt",
    "last_updated_at",
}

# Fields that can change because of ranking, traffic, translation/indexing or
# other backend activity. They must not turn into "creator edited the bot"
# signals.
BACKEND_NOISE_FIELDS = {
    "lora_status",
    "reportsType",
    "translated_languages",
    "recommendation_score",
    "rank",
}

# Stable/public metadata worth comparing. token_count and definition visibility
# are kept because they can reveal a real definition edit even when the actual
# personality text is not exposed in Typesense.
FINGERPRINT_FIELDS = {
    "character_id", "id", "uuid",
    "name", "title", "description", "greeting", "scenario",
    "creator_username", "creator",
    "tags", "language", "type", "visibility",
    "avatar_url", "avatar", "image", "avatar_is_nsfw", "is_nsfw",
    "definition_visible", "definition_size_category", "token_count",
    "has_lorebooks", "lorebooks", "group_size_category", "group_addable",
}

# updatedAt is also noise for field history. This affects observe_bot() globally
# during the R2 run without changing the legacy/local implementation.
legacy.VOLATILE_FIELDS.update(UPDATE_HINT_FIELDS)

_original_configure_cloud = cloud.configure_cloud
_active_fingerprints = None
_active_store = None
_active_state = None
_active_config = None
_run_started_monotonic = 0.0


def _clean_typesense_doc(doc: dict[str, Any]) -> dict[str, Any]:
    cleaned = legacy.clean_for_archive(doc)
    return {
        key: value
        for key, value in cleaned.items()
        if key in FINGERPRINT_FIELDS
        and key not in legacy.VOLATILE_FIELDS
        and key not in UPDATE_HINT_FIELDS
        and key not in BACKEND_NOISE_FIELDS
    }


def _content_hash(doc: dict[str, Any]) -> str:
    raw = json.dumps(
        _clean_typesense_doc(doc),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


def _record_typesense_doc(record: dict[str, Any]) -> dict[str, Any] | None:
    current = record.get("current")
    if not isinstance(current, dict):
        return None

    direct = current.get("typesense")
    if isinstance(direct, dict):
        return direct

    # Compatibility with older per-listing snapshots.
    merged: dict[str, Any] = {}
    found = False
    for key, value in current.items():
        if key.startswith("typesense:") and isinstance(value, dict):
            merged = legacy.merge_last_known(merged, value)
            found = True
    return merged if found else None


def _entry_for_doc(doc: dict[str, Any]) -> dict[str, Any]:
    # Deliberately do not store updatedAt. On SpicyChat it can move when message
    # activity changes and is therefore not a trustworthy authored-content signal.
    return {
        "contentHash": _content_hash(doc),
    }


class FingerprintIndex:
    def __init__(self, store: R2ArchiveStore):
        self.store = store
        self.key = store.key(store.meta_prefix, "bot-fingerprints.json")
        self.values: dict[str, dict[str, Any]] = {}
        self.dirty = False
        self.stats = {
            "avoidedReads": 0,
            "fullReads": 0,
            "newBots": 0,
            "changedBots": 0,
            "readErrors": 0,
            "bootstrapReads": 0,
            "bootstrapErrors": 0,
        }

        data = store.get_json(self.key, None)
        if (
            isinstance(data, dict)
            and int(data.get("schemaVersion") or 0) == FINGERPRINT_SCHEMA
            and isinstance(data.get("fingerprints"), dict)
        ):
            for raw_id, raw_entry in data["fingerprints"].items():
                if not isinstance(raw_entry, dict):
                    continue
                bot_id = str(raw_id).lower()
                self.values[bot_id] = {
                    "contentHash": raw_entry.get("contentHash"),
                }

    def contains(self, bot_id: str) -> bool:
        return bot_id.lower() in self.values

    def entry(self, bot_id: str) -> dict[str, Any] | None:
        value = self.values.get(bot_id.lower())
        return dict(value) if isinstance(value, dict) else None

    def same_content(self, bot_id: str, content_hash: str) -> bool:
        entry = self.values.get(bot_id.lower())
        return bool(
            isinstance(entry, dict)
            and entry.get("contentHash")
            and entry.get("contentHash") == content_hash
        )

    def set_entry(self, bot_id: str, entry: dict[str, Any]) -> None:
        bot_id = bot_id.lower()
        normalized = {
            "contentHash": entry.get("contentHash"),
        }
        if self.values.get(bot_id) != normalized:
            self.values[bot_id] = normalized
            self.dirty = True

    def _bootstrap_one(self, bot_id: str):
        try:
            record = self.store.get_json(self.store.bot_key(bot_id), None)
            if not isinstance(record, dict):
                return bot_id, None, None
            doc = _record_typesense_doc(record)
            if not isinstance(doc, dict):
                return bot_id, {"contentHash": None}, None
            return bot_id, _entry_for_doc(doc), None
        except Exception as exc:
            return bot_id, None, str(exc)

    def bootstrap_missing(self) -> None:
        missing = [
            bot_id
            for bot_id in self.store.discovery_order
            if bot_id not in self.values
        ]
        if not missing:
            print(
                f"R2 fingerprint v4 ready: {len(self.values):,} archived IDs covered.",
                flush=True,
            )
            return

        batch = missing[:BOOTSTRAP_MAX_READS]
        print(
            f"R2 fingerprint v4 bootstrap: {len(batch):,} existing bot records "
            f"({READ_WORKERS} concurrent readers).",
            flush=True,
        )

        completed = 0
        with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
            futures = {
                pool.submit(self._bootstrap_one, bot_id): bot_id
                for bot_id in batch
            }
            for future in as_completed(futures):
                bot_id, entry, error = future.result()
                completed += 1
                self.stats["bootstrapReads"] += 1

                if error:
                    self.stats["bootstrapErrors"] += 1
                elif entry is not None:
                    self.set_entry(bot_id, entry)

                if completed % 1000 == 0 or completed == len(batch):
                    print(
                        f"  fingerprint v4 bootstrap {completed:,}/{len(batch):,}",
                        flush=True,
                    )

        # Save immediately so a later API/crawl problem cannot waste this work.
        self.save_if_dirty()

    def save_if_dirty(self) -> None:
        if not self.dirty:
            return
        payload = {
            "schemaVersion": FINGERPRINT_SCHEMA,
            "generatedAt": legacy.utc_now(),
            "coveredBots": len(self.values),
            "fingerprints": self.values,
        }
        self.store.put_json(self.key, payload)
        self.dirty = False

    def print_stats(self) -> None:
        print(
            "R2 fingerprint v4: "
            f"{self.stats['avoidedReads']:,} full bot GETs avoided; "
            f"{self.stats['fullReads']:,} full bot GETs needed; "
            f"{self.stats['newBots']:,} new; "
            f"{self.stats['changedBots']:,} changed; "
            f"{self.stats['readErrors']:,} read errors; "
            f"{len(self.values):,} IDs indexed.",
            flush=True,
        )


def _parallel_load_records(
    store: R2ArchiveStore,
    bot_ids: list[str],
    *,
    label: str,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    if not bot_ids:
        return {}, set()

    unique_ids = list(dict.fromkeys(bot_ids))
    records: dict[str, dict[str, Any]] = {}
    failed: set[str] = set()

    def load_one(bot_id: str):
        try:
            record = store.get_json(store.bot_key(bot_id), None)
            return bot_id, record, None
        except Exception as exc:
            return bot_id, None, str(exc)

    print(
        f"{label}: loading {len(unique_ids):,} changed/unknown archived records "
        f"with {READ_WORKERS} concurrent R2 readers...",
        flush=True,
    )

    completed = 0
    with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
        futures = {
            pool.submit(load_one, bot_id): bot_id
            for bot_id in unique_ids
        }
        for future in as_completed(futures):
            bot_id, record, error = future.result()
            completed += 1
            if error or not isinstance(record, dict):
                failed.add(bot_id)
            else:
                records[bot_id] = record

            if completed % 500 == 0 or completed == len(unique_ids):
                print(
                    f"  {label} R2 reads {completed:,}/{len(unique_ids):,}",
                    flush=True,
                )

    return records, failed


def optimized_configure_cloud(config: dict[str, Any], store: R2ArchiveStore):
    global _active_fingerprints, _active_store, _active_state, _active_config

    state, bloom = _original_configure_cloud(config, store)
    _active_store = store
    _active_state = state
    _active_config = config

    fingerprints = FingerprintIndex(store)
    _active_fingerprints = fingerprints
    fingerprints.bootstrap_missing()

    def fast_ingest_documents(
        docs: Iterable[dict[str, Any]],
        *,
        source: str,
        at: str,
        state: dict[str, Any],
    ) -> tuple[int, int, set[str]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        label = source.replace("typesense:", "")

        for doc in docs:
            bot_id = legacy.normalize_id(doc)
            if not bot_id:
                continue
            bot_id = bot_id.lower()
            seen.add(bot_id)

            entry = _entry_for_doc(doc)
            known = bot_id in store.known_ids
            restore = bot_id in store.deleted_index
            prior = fingerprints.entry(bot_id)

            if known and not restore and fingerprints.same_content(
                bot_id, entry["contentHash"]
            ):
                # Ignore updatedAt completely here. SpicyChat commonly changes it
                # because message/activity counters moved; that alone says nothing
                # about personality, greeting, scenario, definition, etc.
                fingerprints.stats["avoidedReads"] += 1
                continue

            rows.append(
                {
                    "id": bot_id,
                    "doc": doc,
                    "entry": entry,
                    "known": known,
                }
            )

        known_to_load = [
            row["id"] for row in rows if row["known"]
        ]
        new_count = sum(1 for row in rows if not row["known"])

        print(
            f"{label}: {len(seen) - len(rows):,} unchanged fingerprint matches, "
            f"{len(known_to_load):,} existing records need inspection, "
            f"{new_count:,} new.",
            flush=True,
        )

        loaded, failed = _parallel_load_records(
            store, known_to_load, label=label
        )
        fingerprints.stats["fullReads"] += len(known_to_load)
        fingerprints.stats["readErrors"] += len(failed)

        changed_count = 0
        created_count = 0
        processed = 0
        observe_source = (
            "typesense" if source.startswith("typesense:") else source
        )

        for row in rows:
            bot_id = row["id"]
            doc = row["doc"]
            entry = row["entry"]
            known = row["known"]

            if known and bot_id in failed:
                print(
                    f"WARNING: {label}: skipped {bot_id}; archived R2 record "
                    "could not be read.",
                    flush=True,
                )
                continue

            existing = loaded.get(bot_id) if known else None

            old_known = (existing or {}).get("lastKnown") or {}
            old_definition_visible = old_known.get("definition_visible")
            old_avatar = legacy.normalize_avatar_url(
                old_known.get("avatar_url")
                or old_known.get("avatar")
                or old_known.get("image")
            )

            observed_doc = doc
            if existing is not None and source.startswith("typesense:"):
                observed_doc = _clean_typesense_doc(doc)

            record, changed = legacy.observe_bot(
                existing,
                observed_doc,
                source=observe_source,
                at=at,
            )
            record.setdefault("sources", {})[source] = {"lastSeenAt": at}

            if existing is None:
                created_count += 1
                fingerprints.stats["newBots"] += 1
                # Discovery is the priority while the archive is young. Capture
                # richer data for new bots before spending the rotating budget on
                # old/stale records.
                cloud._priority_add(state, "priorityEnrichment", bot_id)
            else:
                # updatedAt is intentionally ignored as an enrichment signal:
                # SpicyChat can bump it from message/activity changes alone.
                if (
                    "definition_visible" in doc
                    and doc.get("definition_visible")
                    != old_definition_visible
                ):
                    cloud._priority_add(
                        state, "priorityEnrichment", bot_id
                    )

            avatar = legacy.normalize_avatar_url(
                doc.get("avatar_url")
                or doc.get("avatar")
                or doc.get("image")
            )
            if avatar and (existing is None or avatar != old_avatar):
                cloud._priority_add(state, "priorityImages", bot_id)

            if changed and legacy.save_bot(record):
                changed_count += 1
                if existing is not None:
                    fingerprints.stats["changedBots"] += 1

            fingerprints.set_entry(bot_id, entry)

            processed += 1
            if processed % 250 == 0 or processed == len(rows):
                print(
                    f"  {label} ingest {processed:,}/{len(rows):,} "
                    f"(new {created_count:,}, changed {changed_count:,})",
                    flush=True,
                )

        return created_count, changed_count, seen

    def fast_scan_listing(
        client,
        config,
        name: str,
        sort_by: str,
        at: str,
        state: dict[str, Any],
    ):
        page_size = min(
            250, int(config["crawler"].get("listing_page_size", 250))
        )
        max_hits = int(
            config["crawler"].get("listing_max_hits", 2500)
        )
        docs: list[dict[str, Any]] = []
        found = None
        page = 1

        print(
            f"{name}: fetching up to {max_hits:,} ranking hits...",
            flush=True,
        )

        while len(docs) < max_hits:
            request = legacy.typesense_search(
                config,
                page=page,
                per_page=min(page_size, max_hits - len(docs)),
                sort_by=sort_by,
            )
            response = client.multi_search([request])
            if not response.ok:
                return {
                    "ok": False,
                    "error": response.error,
                    "status": response.status,
                    "count": len(docs),
                    "ids": [],
                }

            result = (response.data.get("results") or [{}])[0]
            hits, found_now = legacy.extract_hits(result)
            if found is None:
                found = found_now
            if not hits:
                break

            docs.extend(hits)
            print(
                f"  {name} fetch: {len(docs):,}/{max_hits:,}",
                flush=True,
            )

            if len(hits) < request["per_page"]:
                break
            page += 1

        new, changed, _ = fast_ingest_documents(
            docs,
            source=f"typesense:{name}",
            at=at,
            state=state,
        )
        store.flush_discovery_journal()

        ids = [legacy.normalize_id(doc) for doc in docs]
        ids = [bot_id for bot_id in ids if bot_id]

        metric_keys = (
            "num_messages",
            "num_messages_24h",
            "rating_score",
            "rating_count",
            "token_count",
        )
        compact_metrics: dict[str, dict[str, Any]] = {}
        for doc in docs:
            bot_id = legacy.normalize_id(doc)
            if not bot_id:
                continue
            row = {
                key: doc.get(key)
                for key in metric_keys
                if key in doc and legacy.meaningful(doc.get(key))
            }
            if row:
                compact_metrics[bot_id] = row

        fingerprints.save_if_dirty()

        return {
            "ok": True,
            "found": found,
            "count": len(ids),
            "new": new,
            "changed": changed,
            "ids": ids,
            "metrics": compact_metrics,
        }

    def finish_exploration_pass(at: str) -> int:
        checks = state.setdefault("missingChecks", {})
        queued = 0
        deleted_ids = set(store.deleted_index)

        for bot_id in store.discovery_order:
            if bot_id in deleted_ids:
                continue
            if bot_id not in bloom and bot_id not in checks:
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

    def fast_explore_more(client, config, at: str, state: dict[str, Any]):
        exploration = state.setdefault("exploration", {})
        mode = exploration.get("mode") or "page"
        page_size = 250

        crawler = config.get("crawler") or {}
        base_pages = max(1, int(crawler.get("explore_pages_per_run") or 20))
        max_pages = max(base_pages, int(crawler.get("explore_pages_max") or base_pages))
        growth = max(0, int(crawler.get("explore_pages_growth_per_success") or 1))
        time_limit = max(60, int(crawler.get("explore_time_limit_seconds") or 3600))
        adaptive_pages = max(
            base_pages,
            int(exploration.get("adaptivePages") or base_pages),
        )
        adaptive_pages = min(adaptive_pages, max_pages)

        # The quota guard provides a ceiling. It is deliberately separate from
        # the base/adaptive value so a healthy month can grow above 20 pages.
        try:
            guard_cap = int(os.environ.get("SPICYCHAT_ARCHIVE_EXPLORE_GUARD_CAP", str(max_pages)))
        except ValueError:
            guard_cap = max_pages
        pages_budget = max(0, min(adaptive_pages, guard_cap, max_pages))

        total_new = 0
        total_changed = 0
        total_hits = 0
        errors: list[str] = []
        missing_queued = 0
        completed_pages = 0
        time_limited = False
        discovery_started = time.monotonic()

        discovery_write_soft_limit = int(
            (config.get("storage") or {})
            .get("r2", {})
            .get("discovery_write_soft_limit")
            or 700000
        )

        print(
            "explore: "
            f"{pages_budget} adaptive pages this run "
            f"(base {base_pages}, max {max_pages}, guard {guard_cap}) × {page_size} hits; "
            f"time limit {time_limit // 60}m",
            flush=True,
        )

        for page_index in range(pages_budget):
            elapsed = time.monotonic() - discovery_started
            if elapsed >= time_limit:
                time_limited = True
                print(
                    f"explore: time limit reached after {elapsed/60:.1f}m; "
                    "saving cursor and stopping before the next page.",
                    flush=True,
                )
                break

            usage = store.storage_usage()
            if (
                int(usage.get("writesThisMonth") or 0)
                >= discovery_write_soft_limit
            ):
                errors.append(
                    "discovery paused for free-tier operation safety at "
                    f"{int(usage.get('writesThisMonth') or 0):,} "
                    "R2 writes this month"
                )
                break

            if mode == "cursor" and exploration.get("cursorCreatedAt"):
                cursor = exploration["cursorCreatedAt"]
                base_filter = config["typesense"]["application_filter"]
                filter_by = f"{base_filter} && createdAt:<{cursor}"
                request = legacy.typesense_search(
                    config,
                    page=1,
                    per_page=page_size,
                    sort_by="createdAt:desc",
                    filter_by=filter_by,
                )
            else:
                page_no = int(exploration.get("page") or 1)
                request = legacy.typesense_search(
                    config,
                    page=page_no,
                    per_page=page_size,
                    sort_by="createdAt:desc",
                )

            response = client.multi_search([request])
            if not response.ok:
                errors.append(response.error or f"HTTP {response.status}")
                break

            result = (response.data.get("results") or [{}])[0]
            hits, found = legacy.extract_hits(result)
            exploration["lastFound"] = found

            if not hits:
                if (
                    mode == "page"
                    and found
                    and (int(exploration.get("page") or 1) - 1)
                    * page_size
                    < found
                ):
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

            print(
                f"explore {page_index + 1}/{pages_budget}: "
                f"received {len(hits):,} hits",
                flush=True,
            )

            new, changed, seen_ids = fast_ingest_documents(
                hits,
                source="typesense:explore",
                at=at,
                state=state,
            )
            store.flush_discovery_journal()

            for bot_id in seen_ids:
                bloom.add(bot_id)

            total_new += new
            total_changed += changed
            total_hits += len(hits)
            completed_pages += 1

            last_created = hits[-1].get("createdAt")
            if last_created is not None:
                exploration["lastCreatedAt"] = last_created

            if mode == "cursor":
                if (
                    last_created is None
                    or last_created == exploration.get("cursorCreatedAt")
                ):
                    errors.append("cursor discovery could not advance createdAt")
                    break
                exploration["cursorCreatedAt"] = last_created
            else:
                exploration["page"] = int(exploration.get("page") or 1) + 1

            print(
                f"explore {page_index + 1}/{pages_budget}: "
                f"{new:,} new, {changed:,} changed",
                flush=True,
            )

            if len(hits) < page_size:
                missing_queued += finish_exploration_pass(at)
                exploration["pass"] = int(exploration.get("pass") or 0) + 1
                exploration["page"] = 1
                exploration["mode"] = mode = "page"
                exploration["cursorCreatedAt"] = None
                exploration["lastCreatedAt"] = None
                break

        elapsed_seconds = int(time.monotonic() - discovery_started)

        # Grow only after a healthy run that actually completed its allowed page
        # budget. If time/quota/API limits stopped us, hold steady.
        next_pages = adaptive_pages
        if (
            pages_budget > 0
            and completed_pages >= pages_budget
            and not errors
            and not time_limited
            and adaptive_pages < max_pages
        ):
            next_pages = min(max_pages, adaptive_pages + growth)

        exploration["adaptivePages"] = next_pages
        exploration["lastPageBudget"] = pages_budget
        exploration["lastPagesCompleted"] = completed_pages
        exploration["lastDiscoverySeconds"] = elapsed_seconds
        exploration["lastDiscoveryNew"] = total_new
        exploration["lastTimeLimited"] = time_limited

        print(
            "explore: finished "
            f"{completed_pages}/{pages_budget} pages in {elapsed_seconds/60:.1f}m; "
            f"{total_new:,} new. Next healthy-run budget: {next_pages} pages.",
            flush=True,
        )

        fingerprints.save_if_dirty()
        fingerprints.print_stats()

        return {
            "hits": total_hits,
            "new": total_new,
            "changed": total_changed,
            "mode": mode,
            "missingQueued": missing_queued,
            "errors": errors,
            "pageBudget": pages_budget,
            "pagesCompleted": completed_pages,
            "nextPageBudget": next_pages,
            "timeLimited": time_limited,
            "durationSeconds": elapsed_seconds,
            "r2BotReadsAvoided": fingerprints.stats["avoidedReads"],
            "r2BotReadsNeeded": fingerprints.stats["fullReads"],
        }

    legacy.scan_listing = fast_scan_listing
    legacy.explore_more = fast_explore_more

    return state, bloom



def _iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _delta_since(runs: list[dict[str, Any]], hours: int) -> int | None:
    if len(runs) < 2:
        return None
    latest = runs[-1]
    latest_t = _iso_to_epoch(latest.get("at"))
    if latest_t is None:
        return None
    cutoff = latest_t - hours * 3600
    baseline = None
    for row in runs:
        t = _iso_to_epoch(row.get("at"))
        if t is None:
            continue
        if t <= cutoff:
            baseline = row
        elif baseline is None:
            baseline = row
            break
        else:
            break
    if baseline is None or baseline is latest:
        baseline = runs[0]
    return max(0, int(latest.get("totalBots") or 0) - int(baseline.get("totalBots") or 0))


def _public_stats(history: dict[str, Any], config: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    runs = [r for r in (history.get("runs") or []) if isinstance(r, dict)]
    runs.sort(key=lambda r: str(r.get("at") or ""))
    latest = runs[-1] if runs else {}
    previous = runs[-2] if len(runs) >= 2 else None

    latest_added = (
        max(0, int(latest.get("totalBots") or 0) - int(previous.get("totalBots") or 0))
        if previous else 0
    )

    start_t = _iso_to_epoch(runs[0].get("at")) if runs else None
    latest_t = _iso_to_epoch(latest.get("at")) if latest else None
    observed_hours = max(0.0, ((latest_t - start_t) / 3600)) if start_t and latest_t else 0.0
    observed_growth = (
        max(0, int(latest.get("totalBots") or 0) - int(runs[0].get("totalBots") or 0))
        if runs else 0
    )
    # Use at most a 7-day window for the headline pace; while the archive is new,
    # this is an extrapolated observed pace and naturally stabilizes over time.
    window_runs = runs
    if latest_t:
        cutoff = latest_t - 7 * 86400
        candidates = [r for r in runs if (_iso_to_epoch(r.get("at")) or 0) >= cutoff]
        if len(candidates) >= 2:
            window_runs = candidates
    if len(window_runs) >= 2:
        w0, w1 = window_runs[0], window_runs[-1]
        t0, t1 = _iso_to_epoch(w0.get("at")), _iso_to_epoch(w1.get("at"))
        span_days = max((t1 - t0) / 86400, 1 / 24) if t0 and t1 else 0
        pace = (
            max(0, int(w1.get("totalBots") or 0) - int(w0.get("totalBots") or 0)) / span_days
            if span_days else 0
        )
    else:
        pace = 0

    keep = max(24, int((config.get("site") or {}).get("public_stats_history_points") or 480))
    public_runs = []
    prev_total = None
    for row in runs[-keep:]:
        item = dict(row)
        total = int(item.get("totalBots") or 0)
        item["addedSincePrevious"] = None if prev_total is None else max(0, total - prev_total)
        prev_total = total
        public_runs.append(item)

    site = config.get("site") or {}
    return {
        "schemaVersion": 1,
        "generatedAt": legacy.utc_now(),
        "startedAt": site.get("started_at") or history.get("startedAt") or (runs[0].get("at") if runs else None),
        "totalBots": int(manifest.get("totalBots") or latest.get("totalBots") or 0),
        "publicIndexBots": int(manifest.get("activeBots") or latest.get("publicIndexBots") or 0),
        "deletedBots": int(manifest.get("deletedBots") or latest.get("deletedBots") or 0),
        "latestAdded": latest_added,
        "growth": {
            "added24h": _delta_since(runs, 24),
            "added7d": _delta_since(runs, 24 * 7),
            "added30d": _delta_since(runs, 24 * 30),
            "averagePerDay": round(pace, 1),
            "observedHours": round(observed_hours, 1),
            "observedGrowth": observed_growth,
        },
        "adaptiveDiscovery": {
            "basePages": int((config.get("crawler") or {}).get("explore_pages_per_run") or 20),
            "maxPages": int((config.get("crawler") or {}).get("explore_pages_max") or 50),
            "growthPerSuccess": int((config.get("crawler") or {}).get("explore_pages_growth_per_success") or 1),
            "timeLimitSeconds": int((config.get("crawler") or {}).get("explore_time_limit_seconds") or 3600),
            "nextPageBudget": int(((latest.get("exploration") or {}).get("nextPageBudget")) or 0),
        },
        "runs": public_runs,
    }


def _record_successful_run_stats() -> None:
    if not (_active_store and _active_state is not None and _active_config):
        return

    store = _active_store
    state = _active_state
    config = _active_config
    manifest = legacy.read_json(legacy.SITE_DATA_DIR / "manifest.json", {})
    if not isinstance(manifest, dict):
        manifest = {}

    key = store.key(store.meta_prefix, "stats-history.json")
    history = store.get_json(key, None)
    if not isinstance(history, dict):
        history = {"schemaVersion": 1, "runs": []}

    runs = history.setdefault("runs", [])
    if not runs:
        # Seed the graph with the migration count so the first public stats file
        # can already show real growth instead of starting from a single point.
        marker = store.get_json(store.marker_key, {})
        if isinstance(marker, dict) and marker.get("migratedAt") and marker.get("botRecords"):
            runs.append({
                "at": marker["migratedAt"],
                "kind": "migration-baseline",
                "totalBots": int(marker.get("botRecords") or 0),
                "publicIndexBots": None,
                "deletedBots": int(marker.get("deletedRecords") or 0),
            })

    summary = state.get("lastRunSummary") or {}
    exploration = summary.get("exploration") or {}
    enrichment = summary.get("enrichment") or {}
    images = summary.get("images") or {}
    missing = summary.get("missingVerification") or {}
    usage = store.storage_usage()
    event = {
        "at": state.get("lastRunAt") or legacy.utc_now(),
        "kind": "archive-run",
        "totalBots": len(store.discovery_order),
        "publicIndexBots": int(manifest.get("activeBots") or 0),
        "deletedBots": len(store.deleted_index),
        "missingBots": len(state.get("missingChecks") or {}),
        "runDurationSeconds": max(0, int(time.monotonic() - _run_started_monotonic)),
        "exploration": {
            "hits": int(exploration.get("hits") or 0),
            "new": int(exploration.get("new") or 0),
            "changed": int(exploration.get("changed") or 0),
            "pageBudget": int(exploration.get("pageBudget") or 0),
            "pagesCompleted": int(exploration.get("pagesCompleted") or 0),
            "nextPageBudget": int(exploration.get("nextPageBudget") or 0),
            "timeLimited": bool(exploration.get("timeLimited")),
            "durationSeconds": int(exploration.get("durationSeconds") or 0),
        },
        "enrichment": {
            "attempted": int(enrichment.get("processed") or 0),
            "enriched": int(enrichment.get("enriched") or 0),
        },
        "images": {
            "attempted": int(images.get("processed") or 0),
            "saved": int(images.get("saved") or 0),
        },
        "deletedConfirmed": int(missing.get("deleted") or 0),
        "storage": {
            "usedBytes": int(usage.get("totalBytes") or 0),
            "mediaBytes": int(usage.get("mediaBytes") or 0),
            "objects": int(usage.get("objects") or 0),
            "writesThisMonth": int(usage.get("writesThisMonth") or 0),
        },
    }

    # Exact-at timestamp dedupe makes reruns/resumes harmless.
    runs[:] = [r for r in runs if str(r.get("at") or "") != str(event["at"])]
    runs.append(event)
    runs.sort(key=lambda r: str(r.get("at") or ""))

    history["schemaVersion"] = 1
    history["startedAt"] = (config.get("site") or {}).get("started_at") or history.get("startedAt") or runs[0].get("at")
    store.put_json(key, history)
    store.flush_usage(force=True)

    public_stats = _public_stats(history, config, manifest)
    legacy.write_json_if_changed(legacy.SITE_DATA_DIR / "stats.json", public_stats)
    print(
        "Stats: "
        f"{event['totalBots']:,} archived; "
        f"+{public_stats.get('latestAdded', 0):,} since previous snapshot; "
        f"~{public_stats.get('growth', {}).get('averagePerDay', 0):,.0f}/day observed pace.",
        flush=True,
    )

def main() -> int:
    global _run_started_monotonic
    _run_started_monotonic = time.monotonic()
    cloud.configure_cloud = optimized_configure_cloud
    rc = 1
    try:
        rc = cloud.run()
        if rc == 0:
            _record_successful_run_stats()
        return rc
    finally:
        if _active_fingerprints is not None:
            try:
                _active_fingerprints.save_if_dirty()
            except Exception as exc:
                print(
                    f"WARNING: could not persist R2 fingerprint v4 index: {exc}",
                    flush=True,
                )


if __name__ == "__main__":
    raise SystemExit(main())
