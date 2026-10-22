#!/usr/bin/env python3
"""Read-only per-ID comparison of discovery order vs materialized bot indexes.

A recorded ID with no R2 bot object can be flagged as a phantom discovery
entry, but must not be silently deleted from audit/history state. Public
SpicyChat availability is a separate question requiring live per-ID checks.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Iterable

import archive as legacy
from publish_name_trends import _read_shards
from storage_r2 import R2ArchiveStore

INDEX_SHARDS = 64
SCHEMA_VERSION = 1
MAX_HEAD_CHECKS = 10000


def compare_sets(discovered: Iterable[str], materialized: Iterable[str]) -> tuple[list[str], list[str], int]:
    observed = {str(x).lower() for x in discovered if str(x).strip()}
    indexed = {str(x).lower() for x in materialized if str(x).strip()}
    return sorted(observed - indexed), sorted(indexed - observed), len(observed & indexed)


def classify(
    *,
    observed_count: int, indexed_count: int, discovery_only: list[str],
    index_only: list[str], overlap: int, head_results: list[tuple[str, bool]],
    at: str, max_checks: int,
) -> dict[str, Any]:
    missing = [bot_id for bot_id, exists in head_results if not exists]
    exists = [bot_id for bot_id, present in head_results if present]
    untouched = len(discovery_only) - len(head_results)
    if untouched < 0:
        raise ValueError("More checked IDs than discovery-only candidates")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": at,
        "observedDiscoveryIds": observed_count,
        "materializedIndexIds": indexed_count,
        "presentInBothIndexes": overlap,
        "discoveryOnlyIds": len(discovery_only),
        "indexOnlyIds": len(index_only),
        "botFileHeadChecks": len(head_results),
        "discoveryOnlyWithStoredBotFile": len(exists),
        "discoveryOnlyWithNoBotFile": len(missing),
        "discoveryOnlyUnchecked": untouched,
        "allDiscoveryOnlyIdsChecked": untouched == 0,
        "headCheckLimit": max_checks,
        "discoveryOnlyWithoutBotFileIds": missing,
        "discoveryOnlyWithFileExamples": exists[:100],
        "materializedButNotDiscoveredExamples": index_only[:100],
        "basis": "Exact membership difference between saved discovery IDs and materialized R2 bot text-search index; HEAD confirms existence of the original bot JSON object.",
        "doesNotVerifyPresentDaySpicyChat": True,
        "notes": [
            "This audit is read-only: it never deletes preserved records or changes their statuses.",
            "An unindexed record can still have a stored bot file; only missing-file HEAD checks identify entries without a saved bot object.",
            "No R2 file does not prove the character is gone from SpicyChat; it means this Archive does not currently have a materialized bot record for that ID.",
            "These lists do not equal the arithmetic public-index count gap.",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-head-checks", type=int, default=MAX_HEAD_CHECKS)
    args = ap.parse_args()
    cap = max(0, min(MAX_HEAD_CHECKS, args.max_head_checks))
    if not R2ArchiveStore.credentials_present():
        raise RuntimeError("R2 credentials not present")
    store = R2ArchiveStore(legacy.load_config())
    if not store.is_migrated():
        raise RuntimeError("R2 migration incomplete")

    # Read archived ID sets only: no expensive per-bot JSON retrieval.
    store.load_discovery_order()
    discovery = store.discovery_order
    index_ids: set[str] = set()
    for shard in _read_shards(store, "text-search", INDEX_SHARDS):
        index_ids.update(str(bot_id).lower() for bot_id in shard)
    discovery_only, index_only, overlap = compare_sets(discovery, index_ids)

    # Small bounded Class B HEAD phase; false=proven no object, errors abort
    # rather than being mislabeled as missing.
    checks = discovery_only[:cap]
    with ThreadPoolExecutor(max_workers=8) as pool:
        flags = list(pool.map(lambda bot_id: store.exists(store.bot_key(bot_id)), checks))
    report = classify(
        observed_count=len(set(discovery)),
        indexed_count=len(index_ids),
        discovery_only=discovery_only,
        index_only=index_only,
        overlap=overlap,
        head_results=list(zip(checks, flags)),
        at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        max_checks=cap,
    )
    print(
        "Per-ID archive reconciliation: "
        f"{report['discoveryOnlyIds']:,} observed IDs absent from index, "
        f"{report['discoveryOnlyWithNoBotFile']:,} confirmed without a stored bot object, "
        f"{report['discoveryOnlyWithStoredBotFile']:,} with a stored bot object, "
        f"{report['discoveryOnlyUnchecked']:,} not HEAD checked; "
        "no deletions and no inferred SpicyChat unavailability.",
        flush=True,
    )
    if not args.dry_run:
        store.put_json(store.key("indexes", "discovery-id-audit.json"), report, public=True)
        store.flush_usage(force=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
