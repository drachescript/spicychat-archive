#!/usr/bin/env python3
"""R2 archive runner with a compact, tolerant fingerprint index.

v2 fixes the first fingerprint version's main weakness: it compared the entire
non-volatile Typesense document including updatedAt. If updatedAt or another
bookkeeping field moved for many bots, the crawler could fall back to thousands
of serial R2 GETs.

This version:
- fingerprints visible content separately from updatedAt;
- treats updatedAt-only changes as an enrichment hint, not a reason to GET the
  full archived bot record immediately;
- batches any genuinely-needed R2 bot reads with 16 concurrent readers;
- prints listing + ingest progress so Actions never appears frozen;
- keeps the existing deletion, enrichment, image, ranking and archive logic.
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

import archive as legacy
import archive_cloud as cloud
from storage_r2 import R2ArchiveStore


FINGERPRINT_SCHEMA = 2
READ_WORKERS = 16
BOOTSTRAP_MAX_READS = 25_000

# These are useful change hints but should not make every listing hit download the
# full archived JSON. If only one of these moves, queue normal enrichment instead.
UPDATE_HINT_FIELDS = {
    "updatedAt",
    "updated_at",
    "lastUpdatedAt",
    "last_updated_at",
}

_original_configure_cloud = cloud.configure_cloud
_active_fingerprints = None


def _clean_typesense_doc(doc: dict[str, Any]) -> dict[str, Any]:
    cleaned = legacy.clean_for_archive(doc)
    return {
        key: value
        for key, value in cleaned.items()
        if key not in legacy.VOLATILE_FIELDS
        and key not in UPDATE_HINT_FIELDS
    }


def _updated_hint(doc: dict[str, Any]) -> Any:
    for key in ("updatedAt", "updated_at", "lastUpdatedAt", "last_updated_at"):
        if key in doc and legacy.meaningful(doc.get(key)):
            return doc.get(key)
    return None


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
    return {
        "contentHash": _content_hash(doc),
        "updatedAt": _updated_hint(doc),
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
            "updatedOnly": 0,
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
                    "updatedAt": raw_entry.get("updatedAt"),
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
            "updatedAt": entry.get("updatedAt"),
        }
        if self.values.get(bot_id) != normalized:
            self.values[bot_id] = normalized
            self.dirty = True

    def update_hint_only(self, bot_id: str, updated_at: Any) -> None:
        bot_id = bot_id.lower()
        current = self.values.get(bot_id)
        if not isinstance(current, dict):
            return
        if current.get("updatedAt") != updated_at:
            current = dict(current)
            current["updatedAt"] = updated_at
            self.values[bot_id] = current
            self.dirty = True

    def _bootstrap_one(self, bot_id: str):
        try:
            record = self.store.get_json(self.store.bot_key(bot_id), None)
            if not isinstance(record, dict):
                return bot_id, None, None
            doc = _record_typesense_doc(record)
            if not isinstance(doc, dict):
                return bot_id, {"contentHash": None, "updatedAt": None}, None
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
                f"R2 fingerprint v2 ready: {len(self.values):,} archived IDs covered.",
                flush=True,
            )
            return

        batch = missing[:BOOTSTRAP_MAX_READS]
        print(
            f"R2 fingerprint v2 bootstrap: {len(batch):,} existing bot records "
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
                        f"  fingerprint v2 bootstrap {completed:,}/{len(batch):,}",
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
            "R2 fingerprint v2: "
            f"{self.stats['avoidedReads']:,} full bot GETs avoided; "
            f"{self.stats['fullReads']:,} full bot GETs needed; "
            f"{self.stats['updatedOnly']:,} updatedAt-only hints; "
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
    global _active_fingerprints

    state, bloom = _original_configure_cloud(config, store)

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
                fingerprints.stats["avoidedReads"] += 1

                old_updated = prior.get("updatedAt") if prior else None
                new_updated = entry.get("updatedAt")
                if (
                    legacy.meaningful(new_updated)
                    and old_updated != new_updated
                ):
                    # updatedAt can be noisy. Use it as a signal for normal
                    # character-API enrichment without downloading this bot JSON.
                    cloud._priority_add(
                        state, "priorityEnrichment", bot_id
                    )
                    fingerprints.stats["updatedOnly"] += 1
                    fingerprints.update_hint_only(bot_id, new_updated)
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
            old_updated = old_known.get("updatedAt")
            old_definition_visible = old_known.get("definition_visible")
            old_avatar = legacy.normalize_avatar_url(
                old_known.get("avatar_url")
                or old_known.get("avatar")
                or old_known.get("image")
            )

            observed_doc = doc
            if existing is not None and source.startswith("typesense:"):
                observed_doc = {
                    key: value
                    for key, value in doc.items()
                    if key not in legacy.VOLATILE_FIELDS
                }

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
            else:
                if (
                    legacy.meaningful(doc.get("updatedAt"))
                    and doc.get("updatedAt") != old_updated
                ):
                    cloud._priority_add(
                        state, "priorityEnrichment", bot_id
                    )
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
        pages_budget = int(
            config["crawler"].get("explore_pages_per_run", 20)
        )

        total_new = 0
        total_changed = 0
        total_hits = 0
        errors: list[str] = []
        missing_queued = 0

        discovery_write_soft_limit = int(
            (config.get("storage") or {})
            .get("r2", {})
            .get("discovery_write_soft_limit")
            or 700000
        )

        print(
            f"explore: up to {pages_budget} pages × {page_size} hits",
            flush=True,
        )

        for page_index in range(pages_budget):
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

            last_created = hits[-1].get("createdAt")
            if last_created is not None:
                exploration["lastCreatedAt"] = last_created

            if mode == "cursor":
                if (
                    last_created is None
                    or last_created == exploration.get("cursorCreatedAt")
                ):
                    errors.append(
                        "cursor discovery could not advance createdAt"
                    )
                    break
                exploration["cursorCreatedAt"] = last_created
            else:
                exploration["page"] = (
                    int(exploration.get("page") or 1) + 1
                )

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

        fingerprints.save_if_dirty()
        fingerprints.print_stats()

        return {
            "hits": total_hits,
            "new": total_new,
            "changed": total_changed,
            "mode": mode,
            "missingQueued": missing_queued,
            "errors": errors,
            "r2BotReadsAvoided": fingerprints.stats["avoidedReads"],
            "r2BotReadsNeeded": fingerprints.stats["fullReads"],
        }

    legacy.scan_listing = fast_scan_listing
    legacy.explore_more = fast_explore_more

    return state, bloom


def main() -> int:
    cloud.configure_cloud = optimized_configure_cloud
    try:
        return cloud.run()
    finally:
        if _active_fingerprints is not None:
            try:
                _active_fingerprints.save_if_dirty()
            except Exception as exc:
                print(
                    f"WARNING: could not persist R2 fingerprint v2 index: {exc}",
                    flush=True,
                )


if __name__ == "__main__":
    raise SystemExit(main())
