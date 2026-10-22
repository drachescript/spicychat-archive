#!/usr/bin/env python3
"""Audit *recorded* bot statuses; never claim a live SpicyChat reconciliation.

Uses compact materialized indexes, not 1.4M per-bot object requests.
Last-observed 'public' is NOT proof a character is currently searchable.
The difference between totals is NOT an identified set of missing bot IDs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable

import archive as legacy
from publish_name_trends import _read_shards
from site_indexes import SCHEMA_VERSION as INDEX_SCHEMA
from storage_r2 import R2ArchiveStore

SCHEMA_VERSION = 1
CREATOR_SHARDS = 256
KNOWN_STATUSES = {"public", "deleted", "missing", "restricted", "unknown"}


def _datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def audit(
    shards: Iterable[dict[str, Any]], *, observed_ids: int,
    indexed_records: int | None, now: str, index_generated_at: str | None,
    pending_checks: int | None = None,
) -> dict[str, Any]:
    stamp = _datetime(now)
    if stamp is None:
        raise ValueError("Timestamp must be ISO8601")
    counts: Counter[str] = Counter()
    samples: dict[str, list[dict[str, str]]] = {
        "unknown": [], "missing": [], "restricted": [], "olderThan90Days": []
    }
    seen: set[str] = set()
    duplicates = 0
    invalid_rows = 0
    old_30 = old_90 = missing_last_seen = 0
    for shard in shards:
        for creator, entry in shard.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("bots"), dict):
                continue
            for raw_id, meta in entry["bots"].items():
                bot_id = str(raw_id or "").strip().lower()
                if not bot_id or not isinstance(meta, dict):
                    invalid_rows += 1
                    continue
                if bot_id in seen:
                    duplicates += 1
                    continue
                seen.add(bot_id)
                status = str(meta.get("status") or "unknown").strip().lower()
                category = status if status in KNOWN_STATUSES else "other"
                counts[category] += 1
                last_seen = _datetime(meta.get("lastSeenAt"))
                if not last_seen:
                    missing_last_seen += 1
                else:
                    age = stamp - last_seen
                    if age >= timedelta(days=30):
                        old_30 += 1
                    if age >= timedelta(days=90):
                        old_90 += 1
                sample = {"id": bot_id, "name": str(meta.get("name") or ""),
                          "lastSeenAt": str(meta.get("lastSeenAt") or "")}
                if category in samples and len(samples[category]) < 20:
                    samples[category].append(sample)
                if last_seen and stamp - last_seen >= timedelta(days=90) and len(samples["olderThan90Days"]) < 20:
                    samples["olderThan90Days"].append(sample)

    indexed = indexed_records if isinstance(indexed_records, int) and indexed_records >= 0 else None
    observed = max(0, int(observed_ids))
    index_freshness = _datetime(index_generated_at)
    stale_index = index_freshness is None or (stamp - index_freshness > timedelta(days=7))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now,
        "basis": "Distinct IDs in saved creator metadata and the materialized text-search index; statuses are last-observed only",
        "observedDiscoveryIds": observed,
        "materializedBotIndexCount": indexed,
        "materializedIndexGeneratedAt": index_generated_at,
        "materializedIndexOlderThan7Days": stale_index,
        "discoveryMinusIndexedCount": observed - indexed if indexed is not None else None,
        "discoveryMinusIndexedIsIdentifiedIds": False,
        "creatorIndexedIds": len(seen),
        "indexedWithoutCreatorMetadataCount": max(0, indexed - len(seen)) if indexed is not None else None,
        "creatorIndexDuplicatedIds": duplicates,
        "invalidCreatorIndexRows": invalid_rows,
        "lastObservedStatus": {k: int(counts.get(k, 0)) for k in
                               ("public", "deleted", "missing", "restricted", "unknown", "other")},
        "lastSeenMoreThan30DaysAgo": old_30,
        "lastSeenMoreThan90DaysAgo": old_90,
        "lastSeenUnavailable": missing_last_seen,
        "pendingApiMissingChecks": pending_checks,
        "samples": samples,
        "notes": [
            "An observed ID is not necessarily a materialized bot record.",
            "Last-observed public is not confirmation the bot is still live.",
            "The live public count and historical Archive count cannot be subtracted to identify missing IDs.",
            "Stale observations are not evidence of deletion.",
            "Discovery-minus-indexed is only a count comparison; it can reflect asynchronous indexing. Check individual IDs before cleanup.",
        ],
    }


def publish(store: R2ArchiveStore, report: dict[str, Any]) -> None:
    store.put_json(store.key("indexes", "bot-status-audit.json"), report, public=True)
    store.flush_usage(force=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not R2ArchiveStore.credentials_present():
        raise RuntimeError("Missing R2 credentials")
    store = R2ArchiveStore(legacy.load_config())
    if not store.is_migrated():
        raise RuntimeError("Archive R2 migration incomplete")

    manifest = legacy.read_json(legacy.SITE_DATA_DIR / "manifest.json", {}) or {}
    text_index = store.get_json(store.key("indexes", "text-search.json"), None)
    if not isinstance(text_index, dict) or int(text_index.get("schemaVersion") or 0) != INDEX_SCHEMA:
        raise RuntimeError("No complete materialized text-search index; refusing partial status audit")
    bot_count = text_index.get("botCount")
    if not isinstance(bot_count, int) or bot_count < 0:
        raise RuntimeError("Materialized text index does not report a valid botCount")
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report = audit(
        _read_shards(store, "creators", CREATOR_SHARDS),
        observed_ids=int(manifest.get("totalBots") or 0),
        indexed_records=bot_count,
        index_generated_at=text_index.get("generatedAt"),
        pending_checks=int((manifest.get("reconciliation") or {}).get("pendingVerificationBots")
                           or manifest.get("missingBots") or 0),
        now=stamp,
    )
    print(
        "Archive status audit: "
        f"{report['observedDiscoveryIds']:,} observed IDs, "
        f"{report['materializedBotIndexCount']:,} indexed records, "
        f"{report['creatorIndexedIds']:,} indexed IDs with creators; "
        f"status breakdown {report['lastObservedStatus']}; "
        "NO bot deletion, no inferred 404 statuses.",
        flush=True,
    )
    if not args.dry_run:
        publish(store, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
