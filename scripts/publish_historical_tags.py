#!/usr/bin/env python3
"""Index bot IDs whose tags were previously observed but have since changed.

Backfill from archived change-history indexes rather than reading >1 million
individual R2 records. Also accept exact historicalTags saved on creator records.
This is the archive's observed history, not all changes before archiving began.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import archive as legacy
from publish_name_trends import _read_shards
from publish_tag_stats import bucket_for, norm_tag
from site_indexes import SCHEMA_VERSION as SITE_SCHEMA
from storage_r2 import R2ArchiveStore

SCHEMA_VERSION = 1
CREATOR_SHARDS = 256
TAG_SHARDS = 16


def tags_from_change_value(raw: Any) -> tuple[list[str], bool]:
    """Read archived array, never guess tags from truncated display snippets."""
    if isinstance(raw, list):
        arr = raw
    elif isinstance(raw, str):
        if not raw.strip():
            return [], True
        try:
            arr = json.loads(raw)
        except (ValueError, TypeError):
            return [], False
    else:
        return [], raw is None
    if not isinstance(arr, list):
        return [], False
    return [str(t).strip() for t in arr if isinstance(t, str) and t.strip()], True


def from_changes(changes: list[dict[str, Any]]) -> tuple[dict[str, dict[str, dict[str, Any]]], int]:
    seen: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    incomplete = 0
    for row in changes:
        if not isinstance(row, dict) or str(row.get("path") or "").split(".")[-1] != "tags":
            continue
        bot_id = str(row.get("id") or "").lower()
        if not bot_id:
            continue
        for field in ("from", "to"):
            values, parsed = tags_from_change_value(row.get(field))
            if not parsed:
                incomplete += 1
            for tag in values:
                key = norm_tag(tag)
                seen[key].setdefault(bot_id, {
                    "id": bot_id,
                    "name": str(row.get("name") or ""),
                    "creator": str(row.get("creator") or ""),
                    "title": "",
                    "status": "unknown",
                })
    return seen, incomplete


def collect(
    changes: list[dict[str, Any]], creator_shards: Any, now: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    membership, skipped = from_changes(changes)
    needed_ids = {bot_id for rows in membership.values() for bot_id in rows}
    by_id: dict[str, dict[str, Any]] = {}
    # Only records that have evidence of a former tag need compact card metadata.
    # Read the creator-index shards once and never hold the full index in memory.
    for shard in creator_shards:
        for creator_key, row in shard.items():
            if not isinstance(row, dict) or not isinstance(row.get("bots"), dict):
                continue
            for bot_id, meta in row["bots"].items():
                if not isinstance(meta, dict):
                    continue
                id_key = str(bot_id).lower()
                previously = [
                    value for value in (meta.get("historicalTags") or [])
                    if isinstance(value, str) and norm_tag(value)
                ]
                for label in previously:
                    key = norm_tag(label)
                    membership[key].setdefault(id_key, {"id": id_key})
                    needed_ids.add(id_key)
                if id_key in needed_ids:
                    by_id[id_key] = {
                        "id": id_key,
                        "name": str(meta.get("name") or ""),
                        "title": str(meta.get("title") or ""),
                        "creator": str(row.get("display") or creator_key or ""),
                        "status": str(meta.get("status") or "unknown"),
                        "lastSeenAt": meta.get("lastSeenAt") or None,
                        "_current": {norm_tag(t) for t in (meta.get("tags") or []) if isinstance(t, str)},
                    }

    shards = {f"{i:02x}": {} for i in range(TAG_SHARDS)}
    total = 0
    for key, members in membership.items():
        if not key:
            continue
        rows = []
        for bot_id, existing in members.items():
            source = by_id.get(bot_id)
            if source and key in source["_current"]:
                # Still tagged now; normal live/last-known filters cover this bot.
                continue
            data = {k: v for k, v in (source or existing).items() if k != "_current"}
            rows.append(data)
        if rows:
            rows.sort(key=lambda item: (str(item.get("name") or "").casefold(), item["id"]))
            shards[bucket_for(key)][key] = {"count": len(rows), "bots": rows}
            total += len(rows)

    overview = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now,
        "distinctFormerTags": sum(len(s) for s in shards.values()),
        "formerTagMemberships": total,
        "unparsedHistoricTagValues": skipped,
        "shards": TAG_SHARDS,
        "source": "Archive-observed bot tag changes and precise per-bot historical tag metadata",
        "limitations": "Does not infer tags from before the Archive's first observation; malformed or truncated older change snippets are skipped.",
    }
    return overview, shards


def load_changes(store: R2ArchiveStore) -> list[dict[str, Any]]:
    manifest = store.get_json(store.key("indexes", "changes-history.json"), None)
    if not isinstance(manifest, dict) or int(manifest.get("schemaVersion") or 0) != SITE_SCHEMA:
        raise RuntimeError("Archive field-change history missing or has incompatible schema")
    months = manifest.get("months")
    if not isinstance(months, dict):
        raise RuntimeError("Archive change-history month index is incomplete")
    rows: list[dict[str, Any]] = []
    for month in sorted(months):
        payload = store.get_json(store.key("indexes", f"changes-history/{month}.json"), None)
        if not isinstance(payload, dict) or int(payload.get("schemaVersion") or 0) != SITE_SCHEMA or not isinstance(payload.get("changes"), list):
            raise RuntimeError(f"Missing Archive changes for {month}; won't publish a partial tag index")
        # Only tag changes matter; release the rest of the monthly payload quickly.
        rows.extend(item for item in payload["changes"] if isinstance(item, dict) and str(item.get("path") or "").split(".")[-1] == "tags")
    return rows


def publish(store: R2ArchiveStore, overview: dict[str, Any], shards: dict[str, dict[str, Any]]) -> None:
    for shard, tags in sorted(shards.items()):
        store.put_json(store.key("indexes", f"historical-tags/{shard}.json"), {
            "schemaVersion": SCHEMA_VERSION,
            "tags": tags,
        }, public=True)
    store.put_json(store.key("indexes", "historical-tags.json"), overview, public=True)
    store.flush_usage(force=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not R2ArchiveStore.credentials_present():
        raise RuntimeError("Missing R2 credentials")
    store = R2ArchiveStore(legacy.load_config())
    if not store.is_migrated():
        raise RuntimeError("R2 migration incomplete")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    changes = load_changes(store)
    overview, shards = collect(changes, _read_shards(store, "creators", CREATOR_SHARDS), now)
    print(
        f"Former-tag index: {overview['distinctFormerTags']:,} former tags, "
        f"{overview['formerTagMemberships']:,} archived associations, "
        f"{overview['unparsedHistoricTagValues']:,} unparsed old snippets.",
        flush=True,
    )
    if not args.dry_run:
        publish(store, overview, shards)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
