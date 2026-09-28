#!/usr/bin/env python3
"""Run the R2 archive with a compact bot fingerprint index.

Why this exists:
- the R2 crawler used to GET a full per-bot JSON object for nearly every
  Typesense listing/discovery hit;
- once the archive grows, those serial reads are much slower and consume
  unnecessary Class B operations;
- a compact fingerprint index lets unchanged public bots be recognized in
  memory before touching their full R2 record.

The first optimized run bootstraps fingerprints from existing archived records
using bounded concurrent GETs. Later runs normally need one small fingerprint
index read plus full bot reads only for new/changed/restored bots.
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

import archive as legacy
import archive_cloud as cloud
from storage_r2 import R2ArchiveStore


FINGERPRINT_SCHEMA = 1
BOOTSTRAP_WORKERS = 16
BOOTSTRAP_MAX_READS = 25_000

_original_configure_cloud = cloud.configure_cloud
_active_fingerprints = None


def _clean_typesense_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Canonical non-volatile Typesense metadata used for change detection."""
    cleaned = legacy.clean_for_archive(doc)
    return {
        key: value
        for key, value in cleaned.items()
        if key not in legacy.VOLATILE_FIELDS
    }


def _fingerprint_doc(doc: dict[str, Any]) -> str:
    canonical = _clean_typesense_doc(doc)
    raw = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    # 128 bits is plenty for archive change detection and keeps the index small.
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


def _record_typesense_doc(record: dict[str, Any]) -> dict[str, Any] | None:
    current = record.get("current")
    if not isinstance(current, dict):
        return None

    direct = current.get("typesense")
    if isinstance(direct, dict):
        return direct

    # Compatibility with older pre-collapse records.
    merged: dict[str, Any] = {}
    found = False
    for key, value in current.items():
        if key.startswith("typesense:") and isinstance(value, dict):
            merged = legacy.merge_last_known(merged, value)
            found = True
    return merged if found else None


class FingerprintIndex:
    def __init__(self, store: R2ArchiveStore):
        self.store = store
        self.key = store.key(store.meta_prefix, "bot-fingerprints.json")
        self.values: dict[str, str | None] = {}
        self.dirty = False
        self.stats = {
            "avoidedReads": 0,
            "fullReads": 0,
            "newBots": 0,
            "changedBots": 0,
            "bootstrapReads": 0,
            "bootstrapErrors": 0,
        }

        data = store.get_json(self.key, None)
        if (
            isinstance(data, dict)
            and int(data.get("schemaVersion") or 0) == FINGERPRINT_SCHEMA
            and isinstance(data.get("fingerprints"), dict)
        ):
            for raw_id, raw_value in data["fingerprints"].items():
                bot_id = str(raw_id).lower()
                if isinstance(raw_value, str):
                    self.values[bot_id] = raw_value
                elif raw_value is None:
                    # None means the archived record was inspected but did not yet
                    # contain a Typesense snapshot. If that bot later appears in
                    # Typesense, it will be loaded once and gain a real fingerprint.
                    self.values[bot_id] = None

    def get(self, bot_id: str) -> str | None:
        return self.values.get(bot_id.lower())

    def contains(self, bot_id: str) -> bool:
        return bot_id.lower() in self.values

    def set(self, bot_id: str, value: str | None) -> None:
        bot_id = bot_id.lower()
        if bot_id not in self.values or self.values[bot_id] != value:
            self.values[bot_id] = value
            self.dirty = True

    def matches(self, bot_id: str, value: str) -> bool:
        bot_id = bot_id.lower()
        return bot_id in self.values and self.values[bot_id] == value

    def _bootstrap_one(self, bot_id: str) -> tuple[str, str | None, str | None]:
        try:
            # Read directly instead of store.load_bot(): bootstrap is concurrent
            # and should not mutate the shared LRU record cache.
            record = self.store.get_json(self.store.bot_key(bot_id), None)
            if not isinstance(record, dict):
                return bot_id, None, None
            doc = _record_typesense_doc(record)
            return bot_id, (_fingerprint_doc(doc) if isinstance(doc, dict) else None), None
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
                f"R2 fingerprint index ready: {len(self.values):,} archived IDs covered.",
                flush=True,
            )
            return

        batch = missing[:BOOTSTRAP_MAX_READS]
        print(
            f"R2 fingerprint bootstrap: {len(batch):,} existing bot records "
            f"({BOOTSTRAP_WORKERS} concurrent readers).",
            flush=True,
        )

        completed = 0
        with ThreadPoolExecutor(max_workers=BOOTSTRAP_WORKERS) as pool:
            futures = {
                pool.submit(self._bootstrap_one, bot_id): bot_id
                for bot_id in batch
            }
            for future in as_completed(futures):
                bot_id, value, error = future.result()
                completed += 1
                self.stats["bootstrapReads"] += 1

                if error:
                    self.stats["bootstrapErrors"] += 1
                    # Do not mark failed reads as covered; retry them next run.
                else:
                    self.set(bot_id, value)

                if completed % 1000 == 0 or completed == len(batch):
                    print(
                        f"  fingerprint bootstrap {completed:,}/{len(batch):,}",
                        flush=True,
                    )

        # Persist immediately so a later crawler/API failure cannot throw away a
        # successful one-time bootstrap.
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
            "R2 fingerprints: "
            f"{self.stats['avoidedReads']:,} full bot GETs avoided; "
            f"{self.stats['fullReads']:,} full bot GETs needed; "
            f"{self.stats['newBots']:,} new; "
            f"{self.stats['changedBots']:,} changed; "
            f"{len(self.values):,} IDs indexed.",
            flush=True,
        )


def optimized_configure_cloud(
    config: dict[str, Any],
    store: R2ArchiveStore,
):
    global _active_fingerprints

    # Let archive_cloud install all of its normal R2-backed storage, deletion,
    # enrichment, image, ranking and website hooks first.
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
        new_count = 0
        changed_count = 0
        seen: set[str] = set()
        observe_source = "typesense" if source.startswith("typesense:") else source

        for doc in docs:
            bot_id = legacy.normalize_id(doc)
            if not bot_id:
                continue
            bot_id = bot_id.lower()
            seen.add(bot_id)

            incoming_fp = _fingerprint_doc(doc)
            known = bot_id in store.known_ids

            # A deleted bot that reappears must be loaded even when its metadata
            # fingerprint is unchanged, so observe_bot() can restore public status.
            needs_restore_check = bot_id in store.deleted_index

            if (
                known
                and not needs_restore_check
                and fingerprints.matches(bot_id, incoming_fp)
            ):
                fingerprints.stats["avoidedReads"] += 1
                continue

            existing = None
            if known:
                fingerprints.stats["fullReads"] += 1
                existing = legacy.load_bot(bot_id)

            old_known = (existing or {}).get("lastKnown") or {}
            old_updated = old_known.get("updatedAt")
            old_definition_visible = old_known.get("definition_visible")
            old_avatar = legacy.normalize_avatar_url(
                old_known.get("avatar_url")
                or old_known.get("avatar")
                or old_known.get("image")
            )

            observed_doc = doc
            # Ranking metrics are archived in compact ranking snapshots, so an
            # existing per-bot object is not rewritten just because counters moved.
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
                new_count += 1
                fingerprints.stats["newBots"] += 1
            else:
                if (
                    legacy.meaningful(doc.get("updatedAt"))
                    and doc.get("updatedAt") != old_updated
                ):
                    cloud._priority_add(state, "priorityEnrichment", bot_id)
                if (
                    "definition_visible" in doc
                    and doc.get("definition_visible") != old_definition_visible
                ):
                    cloud._priority_add(state, "priorityEnrichment", bot_id)

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

            # Even if normalization meant the full record did not need a write,
            # this exact Typesense document is now known for future comparisons.
            fingerprints.set(bot_id, incoming_fp)

        return new_count, changed_count, seen

    def fast_scan_listing(
        client,
        config,
        name: str,
        sort_by: str,
        at: str,
        state: dict[str, Any],
    ):
        page_size = min(
            250,
            int(config["crawler"].get("listing_page_size", 250)),
        )
        max_hits = int(config["crawler"].get("listing_max_hits", 2500))

        docs: list[dict[str, Any]] = []
        found = None
        page = 1

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

    def fast_explore_more(
        client,
        config,
        at: str,
        state: dict[str, Any],
    ):
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
                exploration["pass"] = (
                    int(exploration.get("pass") or 0) + 1
                )
                exploration["page"] = 1
                exploration["mode"] = mode = "page"
                exploration["cursorCreatedAt"] = None
                exploration["lastCreatedAt"] = None
                break

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
                    or last_created
                    == exploration.get("cursorCreatedAt")
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
                f"{len(hits):,} hits, {new:,} new, "
                f"{changed:,} changed; "
                f"R2 bot GETs avoided "
                f"{fingerprints.stats['avoidedReads']:,}",
                flush=True,
            )

            if len(hits) < page_size:
                missing_queued += finish_exploration_pass(at)
                exploration["pass"] = (
                    int(exploration.get("pass") or 0) + 1
                )
                exploration["page"] = 1
                exploration["mode"] = mode = "page"
                exploration["cursorCreatedAt"] = None
                exploration["lastCreatedAt"] = None
                break

        # Persist once after the discovery/listing phase instead of rewriting the
        # fingerprint object for every bot.
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
        # If the run fails after discovery, keep any successfully learned
        # fingerprints so the next run does not repeat expensive reads.
        if _active_fingerprints is not None:
            try:
                _active_fingerprints.save_if_dirty()
            except Exception as exc:
                print(
                    f"WARNING: could not persist R2 fingerprint index: {exc}",
                    flush=True,
                )


if __name__ == "__main__":
    raise SystemExit(main())
