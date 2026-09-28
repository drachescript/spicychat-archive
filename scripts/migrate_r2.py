#!/usr/bin/env python3
"""One-time migration of the existing Git archive into Cloudflare R2."""
from __future__ import annotations

import argparse
import json
import mimetypes
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import archive as legacy
from storage_r2 import BloomFilter, R2ArchiveStore, StorageQuotaExceeded

ROOT = Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def collapse_record(record: dict[str, Any], store: R2ArchiveStore) -> dict[str, Any]:
    record = deepcopy(record)
    current = record.get("current")
    if isinstance(current, dict):
        ts_docs = [v for k, v in current.items() if (k == "typesense" or k.startswith("typesense:")) and isinstance(v, dict)]
        if ts_docs:
            merged: dict[str, Any] = {}
            for doc in ts_docs:
                merged = legacy.merge_last_known(merged, doc)
            record["current"] = {k: v for k, v in current.items() if not (k == "typesense" or k.startswith("typesense:"))}
            record["current"]["typesense"] = merged

    avatar = record.get("avatarArchive") or {}
    old_path = avatar.get("path")
    if old_path:
        parts = Path(old_path).parts
        if "media" in parts:
            i = parts.index("media")
            key = store.key(store.media_prefix, *parts[i + 1 :])
            avatar["r2Key"] = key
            avatar["publicUrl"] = store.public_url(key)
            record["avatarArchive"] = avatar
    return record


def upload_media(store: R2ArchiveStore) -> tuple[int, int, int]:
    source = ROOT / "archive" / "media"
    files = [p for p in source.rglob("*") if p.is_file()] if source.exists() else []
    if not files:
        return 0, 0, 0

    def one(path: Path):
        rel = path.relative_to(source)
        key = store.key(store.media_prefix, *rel.parts)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if store.exists(key):
            return "exists"
        try:
            store.put_bytes(
                key,
                path.read_bytes(),
                content_type=ctype,
                cache_control="public,max-age=31536000,immutable",
                category="media",
                known_new=True,
            )
            return "uploaded"
        except StorageQuotaExceeded:
            return "quota"

    uploaded = quota_skipped = 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(one, p) for p in files]
        for f in as_completed(futures):
            result = f.result()
            if result == "uploaded":
                uploaded += 1
            elif result == "quota":
                quota_skipped += 1
    return len(files), uploaded, quota_skipped


def upload_bots(store: R2ArchiveStore) -> tuple[list[str], dict[str, dict[str, Any]], int]:
    paths = list(legacy.iter_bot_paths())
    rows: list[tuple[str, str, dict[str, Any]]] = []
    deleted: dict[str, dict[str, Any]] = {}
    for path in paths:
        record = legacy.read_json(path, {})
        if not record.get("id"):
            continue
        record = collapse_record(record, store)
        bot_id = str(record["id"]).lower()
        rows.append((str(record.get("firstSeenAt") or ""), bot_id, record))
        if (record.get("status") or {}).get("current") == "deleted":
            lk = record.get("lastKnown") or {}
            metrics = (record.get("metrics") or {}).get("latest") or {}
            avatar = record.get("avatarArchive") or {}
            deleted[bot_id] = {
                "id": bot_id,
                "name": lk.get("name") or lk.get("title") or "Unknown bot",
                "title": lk.get("title") or "",
                "creator": lk.get("creator_username") or lk.get("creator") or "",
                "tags": lk.get("tags") if isinstance(lk.get("tags"), list) else [],
                "status": "deleted",
                "statusSince": (record.get("status") or {}).get("since"),
                "firstSeenAt": record.get("firstSeenAt"),
                "lastSeenAt": record.get("lastSeenAt"),
                "isNsfw": bool(lk.get("is_nsfw") or lk.get("avatar_is_nsfw")),
                "avatar": avatar.get("publicUrl") or legacy.normalize_avatar_url(lk.get("avatar_url") or lk.get("avatar") or lk.get("image")),
                "avatarArchived": bool(avatar.get("publicUrl")),
                "messages": metrics.get("num_messages"),
                "messages24h": metrics.get("num_messages_24h"),
                "rating": metrics.get("rating_score"),
                "createdAt": lk.get("createdAt"),
                "updatedAt": lk.get("updatedAt"),
            }

    rows.sort(key=lambda x: (x[0], x[1]))

    def one(row):
        _, bot_id, record = row
        store.put_json(store.bot_key(bot_id), record, public=True)
        return bot_id

    uploaded = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(one, row) for row in rows]
        for f in as_completed(futures):
            f.result()
            uploaded += 1
            if uploaded % 1000 == 0:
                print(f"Uploaded {uploaded:,}/{len(rows):,} bot records")

    return [bot_id for _, bot_id, _ in rows], deleted, uploaded


def upload_rankings(store: R2ArchiveStore) -> int:
    source = ROOT / "archive" / "rankings"
    if not source.exists():
        return 0
    count = 0
    latest = None
    for path in sorted(source.glob("*.json")):
        data = legacy.read_json(path, {})
        if not isinstance(data, dict) or not data:
            continue
        if path.name == "latest.json":
            latest = data
            continue
        store.put_json(store.key(store.rankings_prefix, "legacy", path.name), data)
        count += 1
    if latest:
        store.put_json(store.ranking_latest_key, latest)
    return count


def prune_local() -> None:
    for target in (
        ROOT / "archive" / "bots",
        ROOT / "archive" / "media",
        ROOT / "archive" / "rankings",
        ROOT / "data" / "bots",
        ROOT / "data" / "catalog",
        ROOT / "media",
    ):
        if target.exists():
            shutil.rmtree(target)
    state = ROOT / "archive" / "state.json"
    if state.exists():
        state.unlink()
    (ROOT / "archive").mkdir(parents=True, exist_ok=True)
    (ROOT / "archive" / ".gitkeep").write_text("R2-backed archive; large data is stored outside Git.\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prune-local", action="store_true", help="Delete migrated bot/media/state copies from Git working tree")
    parser.add_argument("--summary-file")
    args = parser.parse_args()

    config = legacy.load_config()
    if not R2ArchiveStore.credentials_present():
        raise SystemExit("R2 credentials are missing. Add the three CLOUDFLARE_R2_* repository secrets first.")
    store = R2ArchiveStore(config)
    started = utc_now()

    print(f"Migrating to R2 bucket: {store.bucket}")
    # Establish an exact starting counter before the migration. On a new bucket
    # this is effectively free; on a resumed migration it prevents under-counting.
    store.recalculate_usage()
    media_total, media_uploaded, media_quota_skipped = upload_media(store)
    order, deleted, bots_uploaded = upload_bots(store)
    ranking_count = upload_rankings(store)

    old_state = legacy.read_json(ROOT / "archive" / "state.json", {"schemaVersion": 2})
    if not isinstance(old_state, dict):
        old_state = {"schemaVersion": 2}
    old_state["schemaVersion"] = 2
    old_state.pop("enrichmentQueue", None)
    old_state.pop("imageQueue", None)
    old_state["priorityEnrichment"] = []
    old_state["priorityImages"] = []
    old_state["enrichmentCursor"] = 0
    old_state["imageCursor"] = 0
    exploration = old_state.setdefault("exploration", {})
    exploration.pop("seenThisPass", None)
    # Start a fresh complete pass because the old gigantic exact UUID set is being
    # replaced with the compact Bloom pass tracker.
    exploration["page"] = 1
    exploration["mode"] = "page"
    exploration["cursorCreatedAt"] = None
    exploration["lastCreatedAt"] = None

    store.discovery_order = list(order)
    store.known_ids = set(order)
    store.discovery_dirty = True
    store.deleted_index = deleted
    store.deleted_dirty = True
    store.save_discovery_order(force=True)
    store.save_deleted_index(force=True)
    store.save_state(old_state)

    r2cfg = (config.get("storage") or {}).get("r2") or {}
    bloom = BloomFilter(
        size_bytes=int(r2cfg.get("bloom_bytes") or 8 * 1024 * 1024),
        hashes=int(r2cfg.get("bloom_hashes") or 7),
    )
    store.save_bloom(bloom)
    deleted_url = store.publish_deleted_index(force=True)

    marker = {
        "schemaVersion": 1,
        "migratedAt": utc_now(),
        "botRecords": len(order),
        "deletedRecords": len(deleted),
        "mediaObjectsSeen": media_total,
        "mediaObjectsUploaded": media_uploaded,
        "mediaObjectsSkippedByQuota": media_quota_skipped,
        "legacyRankingSnapshots": ranking_count,
    }
    # Marker is intentionally last: archive_cloud.py will not switch to R2 until
    # every required piece above has succeeded.
    store.mark_migrated(marker)
    store.flush_usage(force=True)

    config.setdefault("storage", {})["mode"] = "r2"
    legacy.write_json_if_changed(ROOT / "config.json", config)

    runtime = {
        "schemaVersion": 1,
        "storageMode": "r2",
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
    legacy.write_json_if_changed(ROOT / "data" / "runtime.json", runtime)

    if args.prune_local:
        prune_local()

    summary = (
        f"# R2 migration — {started}\n\n"
        f"- Bot records uploaded: **{bots_uploaded:,}**\n"
        f"- Archived media: **{media_uploaded:,}** new / {media_total:,} existing local files"
        f" ({media_quota_skipped:,} skipped by media quota)\n"
        f"- Deleted summaries: **{len(deleted):,}**\n"
        f"- Legacy ranking snapshots copied: **{ranking_count:,}**\n"
        f"- Git working tree pruned: **{'yes' if args.prune_local else 'no'}**\n"
        f"- R2 mode activated in `config.json`: **yes**\n"
    )
    print(summary)
    if args.summary_file:
        Path(args.summary_file).write_text(summary, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
