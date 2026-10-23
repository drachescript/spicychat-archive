#!/usr/bin/env python3
"""Repair only mismatched Archive search entries, never bot files or live statuses.

Uses a complete R2 object listing and all 64 text-search shards. Dry-run by
default. A fresh inventory, not a prior report's sample IDs, defines the work.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import archive as legacy
from audit_discovery_ids import inventory_bot_file_ids
from site_indexes import SCHEMA_VERSION, TEXT_SEARCH_SHARDS, text_search_bucket, text_search_meta
from storage_r2 import R2ArchiveStore


def plan(indexed: set[str], stored: set[str]) -> tuple[set[str], set[str]]:
    return stored - indexed, indexed - stored


def repair(store: R2ArchiveStore, *, apply: bool = False, max_changes: int = 500,
           max_list_pages: int = 2000) -> dict:
    manifest_key = store.key("indexes", "text-search.json")
    meta = store.get_json(manifest_key, None)
    if not isinstance(meta, dict) or meta.get("schemaVersion") != SCHEMA_VERSION:
        raise RuntimeError("Complete text-search manifest required")
    expected = [f"{i:02x}" for i in range(TEXT_SEARCH_SHARDS)]
    if meta.get("shards") != expected:
        raise RuntimeError("Unexpected or incomplete text-search shard list")
    shards = {}
    indexed = set()
    for bucket in expected:
        data = store.get_json(store.key("indexes", "text-search", f"{bucket}.json"), None)
        if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION or not isinstance(data.get("bots"), dict):
            raise RuntimeError(f"Missing/invalid search shard {bucket}; no changes made")
        shards[bucket] = data
        for bot_id in data["bots"]:
            if bot_id in indexed or text_search_bucket(bot_id) != bucket:
                raise RuntimeError("Duplicate or incorrectly bucketed index ID; no changes made")
            indexed.add(bot_id)
    stored, listing = inventory_bot_file_ids(store, max_pages=max_list_pages)
    add, remove = plan(indexed, stored)
    if len(add) + len(remove) > max_changes:
        raise RuntimeError(f"{len(add)+len(remove)} mismatches exceed safety cap {max_changes}; no changes made")
    result = {"generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "dryRun": not apply, "stored": len(stored), "indexedBefore": len(indexed),
              "missingFromSearch": len(add), "staleSearchEntries": len(remove),
              "listing": listing}
    if not apply:
        return result
    # Read and validate every new record BEFORE touching any shard.
    additions = {}
    for bot_id in sorted(add):
        record = store.get_json(store.bot_key(bot_id), None)
        if not isinstance(record, dict) or str(record.get("id") or "").lower() != bot_id:
            raise RuntimeError(f"Cannot safely index unreadable/mismatched bot record {bot_id}")
        additions[bot_id] = text_search_meta(record)
    dirty = set()
    for bot_id in add:
        bucket = text_search_bucket(bot_id)
        shards[bucket]["bots"][bot_id] = additions[bot_id]
        dirty.add(bucket)
    for bot_id in remove:
        bucket = text_search_bucket(bot_id)
        del shards[bucket]["bots"][bot_id]
        dirty.add(bucket)
    # Full inventory and 64 validated shards form the source of truth.
    for bucket in sorted(dirty):
        store.put_json(store.key("indexes", "text-search", f"{bucket}.json"),
                       {"schemaVersion": SCHEMA_VERSION, "generatedAt": result["generatedAt"],
                        "bots": shards[bucket]["bots"]}, public=True)
    result["indexedAfter"] = sum(len(s["bots"]) for s in shards.values())
    store.put_json(manifest_key, {**meta, "generatedAt": result["generatedAt"],
                                 "botCount": result["indexedAfter"]}, public=True)
    store.put_json(store.key("indexes", "text-search-repair.json"), result, public=True)
    store.flush_usage(force=True)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-changes", type=int, default=500)
    ap.add_argument("--max-list-pages", type=int, default=2000)
    args = ap.parse_args()
    store = R2ArchiveStore(legacy.load_config())
    if not store.is_migrated():
        raise RuntimeError("Archive R2 migration incomplete")
    print(repair(store, apply=args.apply, max_changes=args.max_changes,
                 max_list_pages=args.max_list_pages), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
