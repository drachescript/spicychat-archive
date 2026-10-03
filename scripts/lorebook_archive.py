#!/usr/bin/env python3
"""Archive SpicyChat's public Lorebook catalog.

The bot archive predates public Lorebooks and uses a bot-specific storage/index
pipeline. Lorebooks deliberately live beside it in their own R2 namespace so
their discovery/history cannot corrupt or inflate the character crawler state.

Discovery source:
- Typesense collection: lorebooks_public

Detail source:
- GET https://prod.nd-api.com/lorebooks/<id>?sortBy=priority&lastSortPriority=0&view=live

Only public data is archived. A Lorebook disappearing from a *complete*
Typesense pass is recorded as "not-public"; that is intentionally not called a
deletion because private/unlisted/moderation transitions can look identical in
the public index.
"""
from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import archive
from storage_r2 import R2ArchiveStore


SCHEMA_VERSION = 1
INDEX_SCHEMA_VERSION = 1
HISTORY_FIELDS = (
    "name",
    "description",
    "tags",
    "avatar_url",
    "avatar_is_nsfw",
    "is_nsfw",
    "num_entries",
    "numAttachedCharacters",
    "status",
    "visibility",
    "version",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_lorebook_id(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value.lower() if value else None
    if not isinstance(value, dict):
        return None
    for key in ("id", "lorebook_id", "lorebookId"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    return None


def unwrap_lorebook_payload(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    for key in ("lorebook", "data", "result"):
        value = data.get(key)
        if isinstance(value, dict) and normalize_lorebook_id(value):
            return value
    return data if normalize_lorebook_id(data) else None


def lorebook_key(store: R2ArchiveStore, lorebook_id: str) -> str:
    compact = lorebook_id.replace("-", "").lower()
    prefix = compact[:2] if len(compact) >= 2 else "__"
    return store.key("lorebooks", prefix, f"{lorebook_id}.json")


def state_key(store: R2ArchiveStore) -> str:
    return store.key(store.meta_prefix, "lorebooks-state.json")


def index_key(store: R2ArchiveStore) -> str:
    return store.key("indexes", "lorebooks.json")


def stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


def public_snapshot(listing: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    source = detail or listing
    snap: dict[str, Any] = {}
    for field in HISTORY_FIELDS:
        if field in source:
            snap[field] = deepcopy(source.get(field))
        elif field in listing:
            snap[field] = deepcopy(listing.get(field))

    entries = source.get("entries") if isinstance(source, dict) else None
    if isinstance(entries, list):
        clean_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            clean_entries.append(
                {
                    key: deepcopy(entry.get(key))
                    for key in (
                        "id",
                        "name",
                        "keywords",
                        "content",
                        "version",
                        "is_nsfw",
                        "status",
                        "sortPriority",
                        "createdAt",
                        "updatedAt",
                    )
                    if key in entry
                }
            )
        snap["entries"] = clean_entries
    return snap


def diff_snapshot(old: dict[str, Any], new: dict[str, Any], at: str) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    keys = list(HISTORY_FIELDS)
    if "entries" in old or "entries" in new:
        keys.append("entries")

    for key in keys:
        before = old.get(key)
        after = new.get(key)
        if before == after:
            continue
        if key == "entries":
            old_entries = before if isinstance(before, list) else []
            new_entries = after if isinstance(after, list) else []
            changes.append(
                {
                    "at": at,
                    "field": "Entries",
                    "fromCount": len(old_entries),
                    "toCount": len(new_entries),
                    "fromHash": stable_hash(old_entries),
                    "toHash": stable_hash(new_entries),
                }
            )
        else:
            changes.append(
                {
                    "at": at,
                    "field": key,
                    "from": deepcopy(before),
                    "to": deepcopy(after),
                }
            )
    return changes


def build_or_update_record(
    existing: dict[str, Any] | None,
    *,
    listing: dict[str, Any],
    detail: dict[str, Any] | None,
    at: str,
    source: str = "typesense:lorebooks_public",
) -> tuple[dict[str, Any], bool]:
    lorebook_id = normalize_lorebook_id(detail) or normalize_lorebook_id(listing)
    if not lorebook_id:
        raise ValueError("Lorebook payload has no id")

    record = deepcopy(existing) if isinstance(existing, dict) else {
        "schemaVersion": SCHEMA_VERSION,
        "id": lorebook_id,
        "firstSeenAt": at,
        "status": {"current": "public", "since": at},
        "statusHistory": [{"status": "public", "from": at, "source": source}],
        "history": [],
        "versions": [],
    }
    record["schemaVersion"] = SCHEMA_VERSION
    record["id"] = lorebook_id

    old_status = str((record.get("status") or {}).get("current") or "")
    if old_status != "public":
        record.setdefault("statusHistory", []).append(
            {"status": "public", "from": at, "source": source}
        )
        record["status"] = {"current": "public", "since": at}
    else:
        record.setdefault("status", {})["current"] = "public"

    old_snapshot = public_snapshot(
        record.get("listing") if isinstance(record.get("listing"), dict) else {},
        record.get("detail") if isinstance(record.get("detail"), dict) else None,
    )
    new_snapshot = public_snapshot(listing, detail)

    changed = existing is None or old_snapshot != new_snapshot or old_status != "public"
    if changed:
        record.setdefault("history", []).extend(diff_snapshot(old_snapshot, new_snapshot, at))
        record.setdefault("versions", []).append(
            {
                "at": at,
                "source": source,
                "snapshot": deepcopy(new_snapshot),
            }
        )
        record["lastChangeAt"] = at

    record["listing"] = deepcopy(listing)
    if detail is not None:
        record["detail"] = deepcopy(detail)
    record["lastObservedAt"] = at
    return record, changed


def mark_not_public(
    record: dict[str, Any],
    *,
    at: str,
    source: str = "typesense:lorebooks_public-complete-pass",
) -> tuple[dict[str, Any], bool]:
    record = deepcopy(record)
    status = record.setdefault("status", {})
    if status.get("current") == "not-public":
        return record, False
    record.setdefault("statusHistory", []).append(
        {"status": "not-public", "from": at, "source": source}
    )
    record["status"] = {"current": "not-public", "since": at}
    record["lastChangeAt"] = at
    return record, True


def summary_from_record(
    record: dict[str, Any],
    *,
    first_seen: str | None = None,
    last_seen: str | None = None,
) -> dict[str, Any]:
    listing = record.get("listing") if isinstance(record.get("listing"), dict) else {}
    detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
    src = detail or listing
    lorebook_id = normalize_lorebook_id(record) or ""
    return {
        "id": lorebook_id,
        "name": src.get("name") or listing.get("name") or lorebook_id,
        "description": src.get("description") or listing.get("description") or "",
        "creator": src.get("creator_username") or listing.get("creator_username") or "",
        "creatorId": src.get("creator_user_id") or listing.get("creator_user_id"),
        "tags": deepcopy(src.get("tags") if isinstance(src.get("tags"), list) else listing.get("tags") or []),
        "avatar": src.get("avatar_url") or listing.get("avatar_url"),
        "avatarIsNsfw": bool(src.get("avatar_is_nsfw") or listing.get("avatar_is_nsfw")),
        "isNsfw": bool(src.get("is_nsfw") or listing.get("is_nsfw")),
        "numEntries": int(src.get("num_entries") or listing.get("num_entries") or len(src.get("entries") or []) if isinstance(src, dict) else 0),
        "numAttachedCharacters": int(src.get("numAttachedCharacters") or listing.get("numAttachedCharacters") or 0),
        "version": src.get("version") or listing.get("version"),
        "createdAt": src.get("createdAt") or listing.get("createdAt"),
        "updatedAt": src.get("updatedAt") or listing.get("updatedAt"),
        "firstSeenAt": first_seen or record.get("firstSeenAt"),
        "lastSeenAt": last_seen or record.get("lastObservedAt"),
        "lastChangeAt": record.get("lastChangeAt"),
        "status": (record.get("status") or {}).get("current") or "unknown",
        "statusSince": (record.get("status") or {}).get("since"),
    }


def lorebook_search(config: dict[str, Any], *, page: int, per_page: int, cursor_created_at: Any = None) -> dict[str, Any]:
    lore = config.get("lorebooks") or {}
    request: dict[str, Any] = {
        "collection": lore.get("typesense_collection") or "lorebooks_public",
        "q": "*",
        "query_by": lore.get("query_by") or "name,description,tags,creator_username",
        "page": page,
        "per_page": per_page,
        "sort_by": lore.get("sort_by") or "createdAt:desc",
    }
    base_filter = str(lore.get("filter_by") or "").strip()
    if cursor_created_at is not None:
        cursor_filter = f"createdAt:<{cursor_created_at}"
        request["filter_by"] = f"{base_filter} && {cursor_filter}" if base_filter else cursor_filter
        request["page"] = 1
    elif base_filter:
        request["filter_by"] = base_filter
    return request


def fetch_detail(client: archive.Client, config: dict[str, Any], lorebook_id: str) -> archive.HTTPResult:
    lore = config.get("lorebooks") or {}
    base_url = str(lore.get("api_base_url") or "https://prod.nd-api.com/lorebooks").rstrip("/")
    url = (
        f"{base_url}/{quote(lorebook_id)}"
        "?sortBy=priority&lastSortPriority=0&view=live"
    )
    try:
        client._sleep()
        response = client.session.get(
            url,
            headers={
                "X-App-Id": "spicychat",
                "X-Guest-UserId": client.guest_user_id,
                "X-Country": str((config.get("character_api") or {}).get("country") or "US"),
            },
            timeout=client.timeout,
        )
        data = None
        try:
            data = response.json()
        except Exception:
            pass
        return archive.HTTPResult(
            response.ok,
            response.status_code,
            data=data,
            error=None if response.ok else response.text[:300],
            url=url,
        )
    except Exception as exc:
        return archive.HTTPResult(False, 0, error=str(exc), url=url)


def _typesense_has_more(found: int | None, *, page: int, per_page: int, received: int) -> bool:
    if not found:
        return False
    consumed_before = max(0, page - 1) * per_page
    return consumed_before + received < int(found)


def run() -> int:
    config = archive.load_config()
    lore = config.get("lorebooks") or {}
    if lore.get("enabled") is False:
        print("Lorebook archive is disabled.")
        return 0
    if not R2ArchiveStore.credentials_present():
        print("ERROR: Lorebook archive requires configured R2 credentials.")
        return 2

    store = R2ArchiveStore(config)
    if not store.is_migrated():
        print("ERROR: R2 migration marker is missing; refusing Lorebook writes.")
        return 2

    client = archive.Client(config)
    at = utc_now()
    state = store.get_json(
        state_key(store),
        {"schemaVersion": SCHEMA_VERSION, "known": {}, "lastRun": None},
    )
    if not isinstance(state, dict):
        state = {"schemaVersion": SCHEMA_VERSION, "known": {}, "lastRun": None}
    known = state.setdefault("known", {})
    if not isinstance(known, dict):
        known = state["known"] = {}

    page_size = max(1, min(250, int(lore.get("page_size") or 250)))
    max_pages = max(1, min(1000, int(lore.get("max_pages_per_run") or 100)))
    page = 1
    mode = "page"
    cursor = None
    pages_completed = 0
    found_total: int | None = None
    seen: set[str] = set()
    new_count = changed_count = detail_errors = 0
    natural_end = False
    errors: list[str] = []
    started = time.monotonic()

    while pages_completed < max_pages:
        request = lorebook_search(
            config,
            page=page,
            per_page=page_size,
            cursor_created_at=cursor if mode == "cursor" else None,
        )
        response = client.multi_search([request])
        if not response.ok:
            errors.append(response.error or f"HTTP {response.status}")
            break

        result = (response.data.get("results") or [{}])[0]
        docs, found = archive.extract_hits(result)
        if found_total is None and found is not None:
            found_total = found

        if not docs:
            if mode == "page" and _typesense_has_more(
                found,
                page=page,
                per_page=page_size,
                received=0,
            ):
                # Same result-cap escape used by the bot crawler.
                if cursor is None:
                    errors.append("Lorebook Typesense result cap reached without a createdAt cursor")
                    break
                mode = "cursor"
                print("Lorebooks: numbered result cap reached; switching to createdAt cursor.")
                continue
            natural_end = True
            break

        pages_completed += 1
        print(
            f"Lorebooks page {pages_completed}/{max_pages}: {len(docs):,} hits "
            f"({mode} mode)",
            flush=True,
        )

        for doc in docs:
            lorebook_id = normalize_lorebook_id(doc)
            if not lorebook_id or lorebook_id in seen:
                continue
            seen.add(lorebook_id)
            old_meta = known.get(lorebook_id) if isinstance(known.get(lorebook_id), dict) else {}
            listing_fp = stable_hash(doc)
            needs_detail = (
                not old_meta
                or old_meta.get("listingFingerprint") != listing_fp
                or old_meta.get("status") != "public"
            )

            detail = None
            if needs_detail:
                detail_response = fetch_detail(client, config, lorebook_id)
                if detail_response.ok:
                    detail = unwrap_lorebook_payload(detail_response.data)
                else:
                    detail_errors += 1
                    print(
                        f"Lorebook detail {lorebook_id}: HTTP {detail_response.status} "
                        f"{detail_response.error or ''}".rstrip(),
                        flush=True,
                    )

                existing = store.get_json(lorebook_key(store, lorebook_id), None)
                if not isinstance(existing, dict):
                    existing = None
                record, changed = build_or_update_record(
                    existing,
                    listing=doc,
                    detail=detail,
                    at=at,
                )
                if existing is None:
                    new_count += 1
                if changed:
                    changed_count += 1
                # A listing change remains useful even when the detail endpoint
                # transiently fails; existing richer detail is retained.
                store.put_json(
                    lorebook_key(store, lorebook_id),
                    record,
                    public=True,
                    category="data",
                    known_new=existing is None,
                )
                summary = summary_from_record(
                    record,
                    first_seen=(old_meta.get("firstSeenAt") if old_meta else at),
                    last_seen=at,
                )
            else:
                summary = dict(old_meta.get("summary") or {})
                summary["lastSeenAt"] = at
                summary["status"] = "public"

            known[lorebook_id] = {
                "firstSeenAt": old_meta.get("firstSeenAt") or at,
                "lastSeenAt": at,
                "listingFingerprint": listing_fp,
                "status": "public",
                "summary": summary,
            }

        last_created = docs[-1].get("createdAt")
        if mode == "cursor":
            if last_created is None or last_created == cursor:
                errors.append("Lorebook createdAt cursor could not advance")
                break
            cursor = last_created
        else:
            page += 1
            if last_created is not None:
                cursor = last_created

        if len(docs) < page_size:
            current_page = int(request.get("page") or 1)
            if mode == "page" and _typesense_has_more(
                found,
                page=current_page,
                per_page=page_size,
                received=len(docs),
            ):
                if last_created is None:
                    errors.append("Partial Lorebook result-cap page had no createdAt cursor")
                    break
                mode = "cursor"
                cursor = last_created
                print(
                    "Lorebooks: partial numbered page still has more results; "
                    "switching to createdAt cursor.",
                    flush=True,
                )
                continue
            natural_end = True
            break

    # Only a complete public-index pass is evidence that a previously archived
    # Lorebook is currently absent. Never infer status from a capped/error run.
    if natural_end and not errors:
        for lorebook_id, meta in list(known.items()):
            if lorebook_id in seen or not isinstance(meta, dict):
                continue
            if meta.get("status") == "not-public":
                continue
            record = store.get_json(lorebook_key(store, lorebook_id), None)
            if not isinstance(record, dict):
                continue
            record, changed = mark_not_public(record, at=at)
            if changed:
                store.put_json(lorebook_key(store, lorebook_id), record, public=True)
            summary = summary_from_record(
                record,
                first_seen=meta.get("firstSeenAt"),
                last_seen=meta.get("lastSeenAt"),
            )
            known[lorebook_id] = {
                **meta,
                "status": "not-public",
                "summary": summary,
            }

    rows = []
    for lorebook_id, meta in known.items():
        if not isinstance(meta, dict):
            continue
        summary = dict(meta.get("summary") or {})
        if not summary:
            continue
        summary.setdefault("id", lorebook_id)
        summary["firstSeenAt"] = meta.get("firstSeenAt") or summary.get("firstSeenAt")
        summary["lastSeenAt"] = meta.get("lastSeenAt") or summary.get("lastSeenAt")
        summary["status"] = meta.get("status") or summary.get("status") or "unknown"
        rows.append(summary)

    rows.sort(
        key=lambda row: str(row.get("updatedAt") or row.get("createdAt") or row.get("lastSeenAt") or ""),
        reverse=True,
    )
    index = {
        "schemaVersion": INDEX_SCHEMA_VERSION,
        "generatedAt": at,
        "collection": lore.get("typesense_collection") or "lorebooks_public",
        "totalArchived": len(rows),
        "publicNow": sum(1 for row in rows if row.get("status") == "public"),
        "notPublic": sum(1 for row in rows if row.get("status") == "not-public"),
        "lorebooks": rows,
    }
    store.put_json(index_key(store), index, public=True)

    elapsed = int(time.monotonic() - started)
    state["schemaVersion"] = SCHEMA_VERSION
    state["lastRun"] = {
        "at": at,
        "found": found_total,
        "seen": len(seen),
        "pagesCompleted": pages_completed,
        "naturalEnd": natural_end,
        "new": new_count,
        "changed": changed_count,
        "detailErrors": detail_errors,
        "errors": errors,
        "durationSeconds": elapsed,
    }
    store.put_json(state_key(store), state)
    store.flush_usage(force=True)

    print(
        f"Lorebooks: {len(seen):,} seen, {new_count:,} new, "
        f"{changed_count:,} changed, {detail_errors:,} detail errors; "
        f"{pages_completed:,} pages in {elapsed/60:.1f}m; "
        f"archive index {len(rows):,}.",
        flush=True,
    )
    if errors:
        for error in errors:
            print(f"Lorebooks warning: {error}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
