#!/usr/bin/env python3
"""Archive SpicyChat's public Lorebook catalog.

The bot archive predates public Lorebooks and uses a bot-specific storage/index
pipeline. Lorebooks deliberately live beside it in their own R2 namespace so
their discovery/history cannot corrupt or inflate the character crawler state.

Discovery source:
- Typesense collection: lorebooks_public

Public entry/detail source:
- Typesense collection: lorebook_entries_public (separate scoped public key)

The authenticated /lorebooks/<id> endpoint is deliberately not used by this
crawler. Public archive runs must remain anonymous and rely only on data that
SpicyChat itself exposes through its public search collections.

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
        # The first capture is the baseline, not a synthetic "changed from
        # nothing" event. Subsequent snapshots produce readable field history.
        if existing is not None:
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
    lorebook_id = normalize_lorebook_id(record) or ""
    # Typesense is the freshest public-listing source. Prefer it for catalog
    # metadata so a transient detail refresh failure cannot leave stale names,
    # descriptions, tags, or cover information on the public browser.
    tags = listing.get("tags") if isinstance(listing.get("tags"), list) else detail.get("tags")
    entries = detail.get("entries") if isinstance(detail.get("entries"), list) else []
    return {
        "id": lorebook_id,
        "name": listing.get("name") or detail.get("name") or lorebook_id,
        "description": listing.get("description") or detail.get("description") or "",
        "creator": listing.get("creator_username") or detail.get("creator_username") or "",
        "creatorId": listing.get("creator_user_id") or detail.get("creator_user_id"),
        "tags": deepcopy(tags if isinstance(tags, list) else []),
        "avatar": listing.get("avatar_url") or detail.get("avatar_url"),
        "avatarIsNsfw": bool(listing.get("avatar_is_nsfw") if "avatar_is_nsfw" in listing else detail.get("avatar_is_nsfw")),
        "isNsfw": bool(listing.get("is_nsfw") if "is_nsfw" in listing else detail.get("is_nsfw")),
        "numEntries": int(listing.get("num_entries") or detail.get("num_entries") or len(entries) or 0),
        "numAttachedCharacters": int(listing.get("numAttachedCharacters") or detail.get("numAttachedCharacters") or 0),
        "version": listing.get("version") or detail.get("version"),
        "createdAt": listing.get("createdAt") or detail.get("createdAt"),
        "updatedAt": listing.get("updatedAt") or detail.get("updatedAt"),
        "firstSeenAt": first_seen or record.get("firstSeenAt"),
        "lastSeenAt": last_seen or record.get("lastObservedAt"),
        "lastChangeAt": record.get("lastChangeAt"),
        "status": (record.get("status") or {}).get("current") or "unknown",
        "statusSince": (record.get("status") or {}).get("since"),
    }


def configure_lorebook_typesense(
    client: archive.Client,
    config: dict[str, Any],
) -> tuple[str, str, str, str]:
    """Load the current public Lorebook and Lorebook-entry Typesense scopes."""
    lore = config.setdefault("lorebooks", {})
    app_url = str(
        lore.get("application_config_url")
        or "https://prod.nd-api.com/v2/applications/spicychat"
    )
    key = ""
    entry_key = ""
    collection = str(lore.get("typesense_collection") or "lorebooks_public")
    entry_collection = str(
        lore.get("typesense_entries_collection") or "lorebook_entries_public"
    )
    try:
        client._sleep()
        response = client.session.get(
            app_url,
            headers={
                "Accept": "application/json",
                "X-App-Id": "spicychat",
                "X-Guest-UserId": client.guest_user_id,
                "X-Country": str((config.get("character_api") or {}).get("country") or "US"),
            },
            timeout=client.timeout,
        )
        if response.ok:
            payload = response.json() if response.content else {}
            ts = payload.get("typesenseConfig") if isinstance(payload, dict) else {}
            if isinstance(ts, dict):
                key = str(ts.get("apiKeyLorebook") or "").strip()
                entry_key = str(ts.get("apiKeyLorebookEntries") or "").strip()
                collection = str(ts.get("collectionNameLorebook") or collection).strip()
                entry_collection = str(
                    ts.get("collectionNameLorebookEntries") or entry_collection
                ).strip()
    except Exception as exc:
        print(f"Lorebooks: application config lookup failed: {exc}", flush=True)

    # Optional checked-in overrides/fallbacks for local tests or emergency use.
    if not key:
        key = str(lore.get("typesense_key") or "").strip()
    if not entry_key:
        entry_key = str(lore.get("typesense_entries_key") or "").strip()
    if not key:
        raise RuntimeError(
            "SpicyChat application config did not provide apiKeyLorebook; "
            "refusing to query Lorebooks with the character search key."
        )
    if not entry_key:
        raise RuntimeError(
            "SpicyChat application config did not provide apiKeyLorebookEntries; "
            "refusing to use the authenticated Lorebook detail endpoint."
        )

    client.typesense_key = key
    lore["typesense_collection"] = collection or "lorebooks_public"
    lore["typesense_entries_collection"] = (
        entry_collection or "lorebook_entries_public"
    )
    # Keep the runtime-only scoped key in memory. It is never written to archive
    # state or the checked-in config.
    lore["typesense_entries_key"] = entry_key
    return (
        client.typesense_key,
        lore["typesense_collection"],
        entry_key,
        lore["typesense_entries_collection"],
    )

def lorebook_search(config: dict[str, Any], *, page: int, per_page: int, cursor_created_at: Any = None) -> dict[str, Any]:
    lore = config.get("lorebooks") or {}
    request: dict[str, Any] = {
        "collection": lore.get("typesense_collection") or "lorebooks_public",
        "q": "*",
        "query_by": lore.get("query_by") or "name,tags,lorebook_id",
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


def _multi_search_with_key(
    client: archive.Client,
    searches: list[dict[str, Any]],
    key: str,
) -> archive.HTTPResult:
    """Use a scoped Typesense key for one request and always restore the prior key."""
    previous = client.typesense_key
    try:
        client.typesense_key = key
        return client.multi_search(searches)
    finally:
        client.typesense_key = previous


def lorebook_entry_search(
    config: dict[str, Any],
    *,
    lorebook_id: str,
    page: int,
    per_page: int,
    filter_field: str = "lorebook_id",
    query_by: str | None = None,
) -> dict[str, Any]:
    lore = config.get("lorebooks") or {}
    return {
        "collection": (
            lore.get("typesense_entries_collection")
            or "lorebook_entries_public"
        ),
        "q": "*",
        # The entry editor's public search surface searches names/keywords.
        # q='*' means this is only used to satisfy the collection search schema.
        "query_by": query_by or lore.get("entry_query_by") or "name,keywords",
        "page": page,
        "per_page": per_page,
        "filter_by": f"{filter_field}:={lorebook_id}",
    }


def _stable_entry_sort(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(entry: dict[str, Any]) -> tuple[float, str, str]:
        raw_priority = entry.get("sortPriority")
        if raw_priority is None:
            raw_priority = entry.get("priority")
        try:
            priority = float(raw_priority)
        except (TypeError, ValueError):
            priority = 0.0
        return (
            -priority,
            str(entry.get("createdAt") or ""),
            str(entry.get("id") or entry.get("entry_id") or ""),
        )

    return sorted(entries, key=key)


def fetch_public_detail(
    client: archive.Client,
    config: dict[str, Any],
    listing: dict[str, Any],
) -> archive.HTTPResult:
    """Build Lorebook detail from the public Lorebook-entry Typesense collection."""
    lorebook_id = normalize_lorebook_id(listing)
    if not lorebook_id:
        return archive.HTTPResult(False, 0, error="Lorebook listing has no id")

    lore = config.get("lorebooks") or {}
    entry_key = str(lore.get("typesense_entries_key") or "").strip()
    if not entry_key:
        return archive.HTTPResult(
            False,
            0,
            error="Missing public Lorebook-entry Typesense key",
        )

    page_size = max(1, min(250, int(lore.get("entry_page_size") or 250)))
    max_pages = max(1, min(100, int(lore.get("entry_max_pages") or 20)))
    filter_fields = lore.get("entry_filter_fields") or ("lorebook_id", "lorebookId")
    query_fields = []
    for candidate in (
        str(lore.get("entry_query_by") or "name,keywords"),
        "name",
    ):
        if candidate and candidate not in query_fields:
            query_fields.append(candidate)

    last_failure: archive.HTTPResult | None = None
    empty_success: archive.HTTPResult | None = None

    # lorebook_id is the expected public schema. Keep one compatibility
    # fallback for camelCase so a frontend schema rename does not force us back
    # onto the authenticated detail endpoint.
    for filter_field in filter_fields:
        for query_by in query_fields:
            entries: list[dict[str, Any]] = []
            page = 1
            final_response: archive.HTTPResult | None = None
            failed = False

            while page <= max_pages:
                request = lorebook_entry_search(
                    config,
                    lorebook_id=lorebook_id,
                    page=page,
                    per_page=page_size,
                    filter_field=str(filter_field),
                    query_by=query_by,
                )
                response = _multi_search_with_key(client, [request], entry_key)
                final_response = response
                if not response.ok:
                    last_failure = response
                    failed = True
                    break

                result = (response.data.get("results") or [{}])[0]
                if result.get("error"):
                    try:
                        result_status = int(result.get("code") or response.status or 400)
                    except (TypeError, ValueError):
                        result_status = 400
                    last_failure = archive.HTTPResult(
                        False,
                        result_status,
                        error=str(result.get("error")),
                        url=response.url,
                    )
                    failed = True
                    break

                docs, found = archive.extract_hits(result)
                entries.extend(docs)

                if len(docs) < page_size:
                    break
                if found is not None and len(entries) >= found:
                    break
                page += 1

            if failed or final_response is None:
                continue

            detail = deepcopy(listing)
            detail["entries"] = _stable_entry_sort(entries)
            # Preserve the public listing count when present; otherwise make the
            # archived detail self-describing.
            if "num_entries" not in detail:
                detail["num_entries"] = len(entries)

            result = archive.HTTPResult(
                True,
                final_response.status,
                data=detail,
                url=final_response.url,
            )
            if entries:
                return result
            if empty_success is None:
                empty_success = result

    if empty_success is not None:
        return empty_success
    if last_failure is not None:
        return last_failure
    return archive.HTTPResult(
        False,
        0,
        error="Public Lorebook-entry Typesense query produced no usable result",
    )

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
    try:
        _key, lorebook_collection, _entry_key, entry_collection = (
            configure_lorebook_typesense(client, config)
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", flush=True)
        return 2
    print(
        f"Lorebooks: using current SpicyChat Typesense collection "
        f"{lorebook_collection}; entries {entry_collection}.",
        flush=True,
    )
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
                or bool(old_meta.get("detailPending"))
            )

            detail = None
            detail_pending = False
            if needs_detail:
                detail_response = fetch_public_detail(client, config, doc)
                if detail_response.ok:
                    detail = unwrap_lorebook_payload(detail_response.data)
                if detail is None:
                    detail_pending = True
                    detail_errors += 1
                    print(
                        f"Lorebook public entries {lorebook_id}: HTTP {detail_response.status} "
                        f"{detail_response.error or 'empty/invalid detail payload'}".rstrip(),
                        flush=True,
                    )

                existing = store.get_json(lorebook_key(store, lorebook_id), None)
                if not isinstance(existing, dict):
                    existing = None

                if detail is None and existing is not None:
                    # Do not manufacture content-removal history from a transient
                    # detail failure. Keep the previous rich payload, update only
                    # the fresh public listing, and retry detail next run.
                    record = deepcopy(existing)
                    record["listing"] = deepcopy(doc)
                    record["lastObservedAt"] = at
                    changed = False
                else:
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
                "detailPending": detail_pending,
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
