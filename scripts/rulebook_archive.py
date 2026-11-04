#!/usr/bin/env python3
"""Save public SpicyChat Rulebook listings without accessing private rules.

The public Rulebooks catalog uses a separate Typesense collection and scoped
public key. This crawler only stores fields exposed in that public listing.
No authenticated Rulebook API, private rule bodies, hidden values or account
credentials are read or published. Missing from a *complete* catalog scan means
'no longer public', not 'deleted'.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import archive
from storage_r2 import R2ArchiveStore

SCHEMA_VERSION = 1
RULEBOOK_ID = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")
PUBLIC_FIELDS = (
    "name", "description", "title", "tags", "creator_username",
    "avatar_url", "image_url", "avatar", "image", "thumbnail_url",
    "cover", "cover_url", "coverUrl", "cover_image", "cover_image_url",
    "coverImage", "coverImageUrl", "cover_path", "coverPath",
    "is_nsfw", "isNsfw", "createdAt", "updatedAt",
    "num_rules", "numRules", "rule_count", "numAttachedCharacters",
    "numAttachedPersonas", "numAttachedBots", "show_rules",
    "rules_visible", "language",
)
MAX_TEXT = 6000
COVER_REFS_PATH = Path(__file__).resolve().parents[1] / "data" / "rulebook-cover-refs.json"
PUBLIC_CDN_COVER = re.compile(
    r"^https://cdn\.nd-api\.com/avatars/[a-f0-9-]{36}\.(?:webp|png|jpg|jpeg)"
    r"\?class=image500x650$", re.I
)


def load_public_cover_references(path: Path = COVER_REFS_PATH) -> dict[str, str]:
    """Read known PUBLIC cover URLs only; never image content."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    covers = data.get("covers") if isinstance(data, dict) else None
    if not isinstance(covers, dict):
        return {}
    return {
        id.lower(): url for id, url in covers.items()
        if isinstance(id, str) and RULEBOOK_ID.fullmatch(id.lower())
        and isinstance(url, str) and PUBLIC_CDN_COVER.fullmatch(url)
    }


def listing_with_cover(
    doc: dict[str, Any], id: str, known_cover: str = "",
    cover_references: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Use public catalog image first, then saved public links, then last known."""
    listing = public_metadata(doc)
    if not cover_from_metadata(listing):
        cover = (cover_references or {}).get(id) or known_cover
        if cover:
            listing["cover_image_url"] = cover
    return listing


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def rulebook_id(doc: Any) -> str | None:
    if not isinstance(doc, dict):
        return None
    for field in ("rulebook_id", "rulebookId", "id"):
        value = doc.get(field)
        if isinstance(value, str) and RULEBOOK_ID.fullmatch(value.strip().lower()):
            return value.strip().lower()
    return None

def public_metadata(doc: dict[str, Any]) -> dict[str, Any]:
    """Whitelist public listing fields. Do not accidentally archive rules."""
    result: dict[str, Any] = {}
    for field in PUBLIC_FIELDS:
        value = doc.get(field)
        if isinstance(value, str):
            result[field] = value[:MAX_TEXT]
        elif type(value) in (int, float, bool):
            result[field] = value
        elif field == "tags" and isinstance(value, list):
            result[field] = [str(x)[:120] for x in value[:80] if isinstance(x, str)]
    return result

IMAGE_FIELDS = (
    "cover_image_url", "coverImageUrl", "cover_url", "coverUrl",
    "cover_image", "coverImage", "cover_path", "coverPath",
    "avatar_url", "image_url", "thumbnail_url", "avatar", "image", "cover",
)

def cover_from_metadata(doc: dict[str, Any]) -> str:
    """Retain only a public image reference, never an unrelated private object."""
    for key in IMAGE_FIELDS:
        value = doc.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value and len(value) <= 2048 and not value.lower().startswith(
                    ("javascript:", "data:", "file:", "blob:")):
                return value
    return ""

def public_cover_field_names(doc: dict[str, Any]) -> list[str]:
    """Report image-like public listing field NAMES only; never log values.

    This helps identify renamed cover fields without dumping search responses
    or fetching private Rulebook contents.
    """
    return sorted(
        key for key in doc
        if isinstance(key, str)
        and len(key) <= 80
        and re.search(r"cover|image|avatar|thumbnail|picture|photo", key, re.I)
        and not re.search(r"secret|token|auth|credential|private|signed", key, re.I)
    )


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]

def rulebook_key(store: R2ArchiveStore, id: str) -> str:
    return store.key("rulebooks", id.replace("-", "")[:2], id + ".json")

def state_key(store: R2ArchiveStore) -> str:
    return store.key(store.meta_prefix, "rulebooks-state.json")

def index_key(store: R2ArchiveStore) -> str:
    return store.key("indexes", "rulebooks.json")

def configure_public_key(client: archive.Client, config: dict[str, Any]) -> str | None:
    """Only use the application's dedicated PUBLIC Rulebook search scope."""
    settings = config.setdefault("rulebooks", {})
    collection = str(settings.get("typesense_collection") or "rulebooks_public")
    key = ""
    try:
        client._sleep()
        response = client.session.get(
            str(settings.get("application_config_url") or
                "https://prod.nd-api.com/v2/applications/spicychat"),
            headers={
                "Accept": "application/json", "X-App-Id": "spicychat",
                "X-Guest-UserId": client.guest_user_id,
                "X-Country": str((config.get("character_api") or {}).get("country") or "US"),
            },
            timeout=client.timeout,
        )
        if response.ok:
            payload = response.json() if response.content else {}
            ts = payload.get("typesenseConfig") if isinstance(payload, dict) else {}
            if isinstance(ts, dict):
                key = str(ts.get("apiKeyRulebook") or ts.get("apiKeyRulebooks") or "").strip()
                collection = str(
                    ts.get("collectionNameRulebook") or ts.get("collectionNameRulebooks") or collection
                ).strip()
    except Exception as exc:
        print(f"Rulebooks: public configuration unavailable: {exc}", flush=True)
    if not key:
        key = str(settings.get("typesense_key") or "").strip()
    # NEVER fall back to the bot or Lorebook key: their permissions differ.
    if not key or collection != "rulebooks_public":
        print("Rulebooks: dedicated public rulebook search scope unavailable; skipping safely.", flush=True)
        return None
    client.typesense_key = key
    settings["typesense_collection"] = collection
    return collection

def query(*, page: int, per_page: int, collection: str = "rulebooks_public") -> dict[str, Any]:
    return {
        "collection": collection,
        "q": "*",
        "query_by": "name",
        "filter_by": "application_ids:=spicychat",
        "sort_by": "createdAt:desc",
        "page": page,
        "per_page": per_page,
    }

def build_record(previous: dict[str, Any] | None, doc: dict[str, Any], at: str
                 ) -> tuple[dict[str, Any], bool]:
    id = rulebook_id(doc)
    if not id:
        raise ValueError("Rulebook has no valid public ID")
    snapshot = public_metadata(doc)
    previous_cover = cover_from_metadata((previous or {}).get("listing") or {})
    if not cover_from_metadata(snapshot) and previous_cover:
        snapshot["cover_image_url"] = previous_cover
    fp = digest(snapshot)
    record = deepcopy(previous) if isinstance(previous, dict) else {
        "schemaVersion": SCHEMA_VERSION, "id": id,
        "firstSeenAt": at, "versions": [], "statusHistory": [],
    }
    record.setdefault("firstSeenAt", at)
    old_status = (record.get("status") or {}).get("current")
    old_hash = record.get("fingerprint")
    changed = old_hash != fp or old_status != "public"
    if old_hash != fp:
        record.setdefault("versions", []).append({"at": at, "metadata": snapshot})
    if old_status != "public":
        record.setdefault("statusHistory", []).append({"at": at, "status": "public"})
    record["listing"] = snapshot
    record["fingerprint"] = fp
    record["lastObservedAt"] = at
    record["status"] = {"current": "public"}
    return record, changed

def summary(record: dict[str, Any]) -> dict[str, Any]:
    data = record.get("listing") or {}
    id = record["id"]
    return {
        "id": id,
        "name": data.get("name") or data.get("title") or id,
        "description": data.get("description") or "",
        "cover": cover_from_metadata(data),
        "creator": data.get("creator_username") or "",
        "tags": data.get("tags") or [],
        "isNsfw": bool(data.get("is_nsfw") or data.get("isNsfw")),
        "numRules": (data.get("num_rules") or data.get("numRules") or
                     data.get("rule_count") or 0),
        "numBots": data.get("numAttachedBots") or data.get("numAttachedCharacters") or 0,
        "numPersonas": data.get("numAttachedPersonas") or 0,
        "createdAt": data.get("createdAt"),
        "updatedAt": data.get("updatedAt"),
        "status": (record.get("status") or {}).get("current") or "unknown",
        "firstSeenAt": record.get("firstSeenAt"),
        "lastSeenAt": record.get("lastObservedAt"),
    }

def apply_disappearance(record: dict[str, Any], at: str) -> bool:
    if (record.get("status") or {}).get("current") != "public":
        return False
    record["status"] = {"current": "not-public"}
    record.setdefault("statusHistory", []).append({"at": at, "status": "not-public"})
    return True

def run() -> int:
    config = archive.load_config()
    options = config.get("rulebooks") or {}
    if options.get("enabled") is False:
        print("Rulebooks: disabled in configuration.", flush=True)
        return 0
    if not R2ArchiveStore.credentials_present():
        print("Rulebooks: R2 credentials unavailable; skipped.", flush=True)
        return 0
    store = R2ArchiveStore(config)
    if not store.is_migrated():
        print("Rulebooks: R2 migration not completed; skipped.", flush=True)
        return 0
    client = archive.Client(config)
    collection = configure_public_key(client, config)
    if collection is None:
        return 0

    at = now_iso()
    state = store.get_json(state_key(store), {"schemaVersion": SCHEMA_VERSION, "known": {}})
    if not isinstance(state, dict):
        raise RuntimeError("Rulebook state is invalid")
    known = state.setdefault("known", {})
    if not isinstance(known, dict):
        raise RuntimeError("Rulebook index state is invalid")
    per_page = max(1, min(100, int(options.get("page_size") or 48)))
    max_pages = max(1, min(100, int(options.get("max_pages_per_run") or 20)))
    seen: set[str] = set()
    errors: list[str] = []
    complete = False
    found = None
    new_count = changed_count = 0
    cover_references = load_public_cover_references()
    public_cover_refs = 0
    public_image_fields: dict[str, int] = {}
    started = time.monotonic()

    for page in range(1, max_pages + 1):
        response = client.multi_search([query(page=page, per_page=per_page, collection=collection)])
        if not response.ok:
            errors.append(response.error or f"HTTP {response.status}")
            break
        result = (response.data.get("results") or [{}])[0]
        if result.get("error"):
            errors.append(str(result["error"])[:300])
            break
        docs, found_here = archive.extract_hits(result)
        if found is None and found_here is not None:
            found = found_here
        if not docs:
            complete = True
            break
        for doc in docs:
            id = rulebook_id(doc)
            if not id or id in seen:
                continue
            seen.add(id)
            public_listing = public_metadata(doc)
            if cover_from_metadata(public_listing):
                public_cover_refs += 1
            for field in public_cover_field_names(doc):
                public_image_fields[field] = public_image_fields.get(field, 0) + 1
            old = known.get(id)
            previous_cover = cover_from_metadata((old or {}).get("summary") or {})
            metadata = listing_with_cover(doc, id, previous_cover, cover_references)
            fingerprint = digest(metadata)
            if (isinstance(old, dict) and old.get("fingerprint") == fingerprint
                    and old.get("status") == "public"):
                old["lastSeenAt"] = at
                continue
            existing = store.get_json(rulebook_key(store, id), None)
            record, changed = build_record(existing, doc, at)
            if not isinstance(existing, dict):
                new_count += 1
            if changed:
                changed_count += 1
            store.put_json(rulebook_key(store, id), record, public=True,
                           category="data", known_new=not isinstance(existing, dict))
            known[id] = {
                "fingerprint": record["fingerprint"], "status": "public",
                "summary": summary(record),
                "firstSeenAt": record["firstSeenAt"], "lastSeenAt": at,
            }
        print(f"Rulebooks: page {page}, {len(docs)} public listings.", flush=True)
        if len(docs) < per_page or (found is not None and page * per_page >= found):
            complete = True
            break
    # Never label entries gone based on incomplete/failed listings.
    if complete and not errors:
        for id, previous in list(known.items()):
            if id in seen or not isinstance(previous, dict):
                continue
            if previous.get("status") != "public":
                continue
            record = store.get_json(rulebook_key(store, id), None)
            if not isinstance(record, dict):
                continue
            if apply_disappearance(record, at):
                store.put_json(rulebook_key(store, id), record, public=True)
                previous["status"] = "not-public"
                previous["summary"] = summary(record)

    rows: list[dict[str, Any]] = []
    for id, item in known.items():
        if not isinstance(item, dict) or not isinstance(item.get("summary"), dict):
            continue
        row = dict(item["summary"])
        row["status"] = item.get("status") or "unknown"
        row["firstSeenAt"] = item.get("firstSeenAt") or row.get("firstSeenAt")
        row["lastSeenAt"] = item.get("lastSeenAt") or row.get("lastSeenAt")
        rows.append(row)
    rows.sort(key=lambda row: str(row.get("createdAt") or row.get("firstSeenAt") or ""), reverse=True)
    public_saved_covers = sum(
        1 for row in rows if row["status"] == "public" and bool(row.get("cover"))
    )
    print(
        f"Rulebooks: public listing covers {public_cover_refs}/{len(seen)}; "
        f"saved public covers {public_saved_covers}/{sum(x['status'] == 'public' for x in rows)}; "
        f"public image field names/counts: {', '.join(f'{key}={count}' for key, count in sorted(public_image_fields.items())) or 'none'}",
        flush=True,
    )
    store.put_json(index_key(store), {
        "schemaVersion": SCHEMA_VERSION, "generatedAt": at,
        "collection": collection, "totalArchived": len(rows),
        "publicNow": sum(1 for x in rows if x["status"] == "public"),
        "notPublic": sum(1 for x in rows if x["status"] == "not-public"),
        "coverStats": {
            "publicListingWithCover": public_cover_refs,
            "publicSavedWithCover": public_saved_covers,
            "publicSeen": len(seen),
        },
        "lastScanComplete": complete and not errors, "rulebooks": rows,
    }, public=True)
    state["lastRun"] = {
        "at": at, "found": found, "seen": len(seen),
        "complete": complete and not errors, "new": new_count,
        "changed": changed_count, "errors": errors,
        "durationSeconds": round(time.monotonic() - started, 1),
    }
    store.put_json(state_key(store), state)
    store.flush_usage(force=True)
    print(f"Rulebooks: {len(seen)} public listings, {new_count} new, {changed_count} changed; "
          f"full pass={complete and not errors}; {len(rows)} archived.", flush=True)
    if errors:
        print("Rulebooks: incomplete scan: " + "; ".join(errors), flush=True)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(run())
