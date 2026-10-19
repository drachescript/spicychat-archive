#!/usr/bin/env python3
"""Publish archived tag-field usage, comparisons and forward-only trends.

Tags are observed last-known tags arrays in public creator-index shards.
Not an official taxonomy, sitewide live totals or past tag assignments.
"""
from __future__ import annotations

import argparse
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Iterable

import archive as legacy
from publish_name_trends import _read_shards
from site_indexes import _fnv1a32
from storage_r2 import R2ArchiveStore

SCHEMA_VERSION = 1
CREATOR_SHARDS = 256
TAG_SHARDS = 16
HISTORY_KEEP = 90


def norm_tag(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).split()).strip().lower()


def bucket_for(tag: str) -> str:
    return f"{_fnv1a32(tag) & (TAG_SHARDS - 1):02x}"


def count_rows(counts: Counter[str], labels: dict[str, str], limit: int, *, least: bool = False) -> list[dict[str, Any]]:
    order = sorted(counts.items(), key=lambda row: (row[1] if least else -row[1], row[0]))
    return [{"tag": labels.get(tag, tag), "key": tag, "count": count} for tag, count in order[:limit]]


def aggregate(shards: Iterable[dict[str, Any]], generated_at: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    pairs: Counter[tuple[str, str]] = Counter()
    by_creator: dict[str, Counter[str]] = defaultdict(Counter)
    creator_labels: dict[str, str] = {}
    counted_ids: set[str] = set()
    indexed = tagged = assignments = 0
    creators: set[str] = set()

    for shard in shards:
        for creator_key, row in shard.items():
            if not isinstance(row, dict):
                continue
            username = str(creator_key or "").strip().lower()
            display = str(row.get("display") or creator_key or "").strip()
            if username:
                creators.add(username)
                creator_labels.setdefault(username, display)
            bots = row.get("bots") or {}
            if not isinstance(bots, dict):
                continue
            for bot_id, meta in bots.items():
                # A bot moving creators may briefly appear twice; count ID once.
                bot_key = str(bot_id or "").strip().lower()
                if not bot_key or bot_key in counted_ids or not isinstance(meta, dict):
                    continue
                counted_ids.add(bot_key)
                indexed += 1
                raw_tags = meta.get("tags")
                if not isinstance(raw_tags, list):
                    continue
                tags: set[str] = set()
                for value in raw_tags:
                    key = norm_tag(value)
                    if key:
                        tags.add(key)
                        labels.setdefault(key, " ".join(unicodedata.normalize("NFKC", value).split()).strip())
                if not tags:
                    continue
                tagged += 1
                assignments += len(tags)
                for key in tags:
                    counts[key] += 1
                    if username:
                        by_creator[key][username] += 1
                for a, b in combinations(sorted(tags), 2):
                    pairs[a, b] += 1

    all_pairs: dict[str, dict[str, int]] = defaultdict(dict)
    for (a, b), count in pairs.items():
        all_pairs[a][b] = count
        all_pairs[b][a] = count

    lookup: dict[str, dict[str, Any]] = {f"{i:02x}": {} for i in range(TAG_SHARDS)}
    for key in sorted(counts):
        creator_count = by_creator.get(key, Counter())
        lookup[bucket_for(key)][key] = {
            "tag": labels[key],
            "count": counts[key],
            "creatorCount": len(creator_count),
            "topCreators": [
                {"name": creator_labels.get(creator, creator), "count": amount}
                for creator, amount in sorted(creator_count.items(), key=lambda x: (-x[1], x[0]))[:15]
            ],
            "withTags": all_pairs.get(key, {}),
        }

    best_pairs = sorted(pairs.items(), key=lambda row: (-row[1], row[0]))[:75]
    overview = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "coverage": {
            "indexedBots": indexed,
            "taggedBots": tagged,
            "tagAssignments": assignments,
            "indexedCreators": len(creators),
            "creatorShards": CREATOR_SHARDS,
            "tagShards": TAG_SHARDS,
            "source": "Last-known tag fields on creator-indexed Archive bots",
        },
        "distinctTags": len(counts),
        "oneUseTags": sum(n == 1 for n in counts.values()),
        "allTagLabels": [labels[key] for key in sorted(counts)],
        "topTags": count_rows(counts, labels, 80),
        "rareTags": count_rows(counts, labels, 80, least=True),
        "topPairs": [{"a": labels[a], "b": labels[b], "count": count} for (a, b), count in best_pairs],
    }
    return overview, lookup


def updated_history(existing: Any, overview: dict[str, Any], all_counts: dict[str, int]) -> dict[str, Any]:
    """Actual daily publications, no fabricated historical changes."""
    if existing is None:
        existing = {}
    if not isinstance(existing, dict) or (existing and int(existing.get("schemaVersion") or 0) != SCHEMA_VERSION):
        raise RuntimeError("Existing tag stats history has an incompatible schema")
    rows = existing.get("snapshots") or []
    if not isinstance(rows, list):
        raise RuntimeError("Malformed tag-statistics history")
    date = overview["generatedAt"][:10]
    entry = {"date": date, "indexedBots": overview["coverage"]["indexedBots"], "counts": all_counts}
    replacement = [r for r in rows if isinstance(r, dict) and r.get("date") != date]
    replacement.append(entry)
    replacement.sort(key=lambda r: str(r.get("date") or ""))
    return {"schemaVersion": SCHEMA_VERSION, "snapshots": replacement[-HISTORY_KEEP:]}


def publish(store: R2ArchiveStore, overview: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> None:
    history_key = store.key("indexes", "tag-stats-history.json")
    old_history = store.get_json(history_key, None)
    all_counts = {key: entry["count"] for group in lookup.values() for key, entry in group.items()}
    history = updated_history(old_history, overview, all_counts)
    for shard, entries in sorted(lookup.items()):
        store.put_json(
            store.key("indexes", "tag-stats", shard + ".json"),
            {"schemaVersion": SCHEMA_VERSION, "tags": entries},
            public=True,
        )
    store.put_json(history_key, history, public=True)
    store.put_json(store.key("indexes", "tag-stats.json"), overview, public=True)
    store.flush_usage(force=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    config = legacy.load_config()
    if not R2ArchiveStore.credentials_present():
        raise RuntimeError("R2 credentials missing")
    store = R2ArchiveStore(config)
    if not store.is_migrated():
        raise RuntimeError("R2 migration is not complete")
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    overview, lookup = aggregate(_read_shards(store, "creators", CREATOR_SHARDS), timestamp)
    print(
        f"Tag stats: {overview['coverage']['indexedBots']:,} bots indexed, "
        f"{overview['coverage']['taggedBots']:,} tagged, "
        f"{overview['distinctTags']:,} distinct tags.",
        flush=True,
    )
    if not args.dry_run:
        publish(store, overview, lookup)
        print("Published tag statistics and updated daily snapshot history.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
