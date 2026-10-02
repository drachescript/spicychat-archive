#!/usr/bin/env python3
"""Build and maintain compact public indexes used by archive history pages.

The full per-bot records stay in R2.  These indexes make creator history,
recent edits, restored bots, first/last-seen filters, and archive health fast
without making browsers fetch thousands of individual bot objects.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from storage_r2 import R2ArchiveStore
from rich_field_index import field_flags, field_mask

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 3
CHANGES_LIMIT = 10000
ACTIVITY_LIMIT = 100000
RESTORED_LIMIT = 10000
TEXT_SEARCH_SHARDS = 64
TEXT_BLOOM_BYTES = 32
TEXT_BLOOM_HASHES = 4

SEARCH_FIELD_LEAVES = {
    "name", "title", "description", "greeting", "greetings", "persona", "personality",
    "definition", "character_definition", "characterDefinition", "scenario", "dialogue",
    "example_dialogue", "example_dialogues",
}

VOLATILE_LEAVES = {
    "updatedAt", "updated_at", "lastUpdatedAt", "last_updated_at",
    "num_messages", "num_messages_24h", "rating_score", "rating_count",
    "token_count", "rank", "ranking",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_ms(value: Any) -> int:
    if not value:
        return 0
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return 0


def meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(meaningful(x) for x in value)
    if isinstance(value, dict):
        return any(meaningful(x) for x in value.values())
    return True


def clean_text(value: Any, limit: int = 320) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(value)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def last_known(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("lastKnown")
    return value if isinstance(value, dict) else {}


def creator_name(record: dict[str, Any]) -> str:
    lk = last_known(record)
    return str(lk.get("creator_username") or lk.get("creator") or "").strip()


def creator_key(value: Any) -> str:
    return str(value or "").strip().lower()


def creator_id(record: dict[str, Any]) -> str:
    lk = last_known(record)
    return str(lk.get("creator_id") or lk.get("creatorId") or lk.get("user_id") or "").strip()


def creator_bucket(value: Any) -> str:
    # FNV-1a, low byte. Easy to reproduce in browser JS.
    h = 2166136261
    for ch in creator_key(value).encode("utf-8"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return f"{h & 0xFF:02x}"


def bot_name(record: dict[str, Any]) -> str:
    lk = last_known(record)
    return str(lk.get("name") or lk.get("title") or "Unknown bot")


def bot_title(record: dict[str, Any]) -> str:
    lk = last_known(record)
    return str(lk.get("title") or "")


def is_nsfw(record: dict[str, Any]) -> bool:
    lk = last_known(record)
    return bool(lk.get("is_nsfw") or lk.get("avatar_is_nsfw"))


def restoration_candidates(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Public observations after an unavailable state, regardless of source."""
    rows = record.get("availabilityHistory") or []
    if not isinstance(rows, list):
        return []
    restored: list[dict[str, Any]] = []
    saw_unavailable = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").lower()
        if status in {"deleted", "missing", "unavailable"}:
            saw_unavailable = True
        elif status == "public" and saw_unavailable:
            restored.append(row)
            saw_unavailable = False
    current_status = record.get("status") or {}
    candidate = current_status.get("restoreCandidate") if isinstance(current_status, dict) else None
    if str(current_status.get("current") or "").lower() == "deleted" and isinstance(candidate, dict) and candidate.get("at"):
        restored.append({"status": "public", "from": candidate.get("at"), "source": candidate.get("source") or "listing-candidate", "candidateOnly": True})
    return restored


def restored_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Only direct Character API confirmation counts as a verified restoration."""
    return [
        row for row in restoration_candidates(record)
        if str(row.get("source") or "").lower() == "character-api"
    ]


def latest_content_change(record: dict[str, Any]) -> str:
    best = ""
    for row in record.get("fieldHistory") or []:
        if not isinstance(row, dict):
            continue
        leaf = str(row.get("path") or "").split(".")[-1]
        if leaf in VOLATILE_LEAVES:
            continue
        at = str(row.get("at") or "")
        if at > best:
            best = at
    return best


def creator_bot_meta(record: dict[str, Any]) -> dict[str, Any]:
    status = str((record.get("status") or {}).get("current") or "unknown").lower()
    restored = restored_events(record)
    lk = last_known(record)
    avatar = record.get("avatarArchive") or {}
    metrics = (record.get("metrics") or {}).get("latest") or {}
    source_avatar = lk.get("avatar_url") or lk.get("avatar") or lk.get("image")
    lorebooks = lk.get("lorebooks")
    has_lorebooks = lk.get("has_lorebooks")
    if not isinstance(has_lorebooks, bool):
        has_lorebooks = meaningful(lorebooks)
    definition_visible = lk.get("definition_visible")
    if not isinstance(definition_visible, bool):
        definition_visible = None
    return {
        "status": status,
        "firstSeenAt": record.get("firstSeenAt"),
        "lastSeenAt": record.get("lastSeenAt"),
        "restoreCount": len(restored),
        "lastChangeAt": latest_content_change(record) or None,
        "name": bot_name(record),
        "title": bot_title(record),
        "tags": lk.get("tags") if isinstance(lk.get("tags"), list) else [],
        "isNsfw": is_nsfw(record),
        "avatar": avatar.get("publicUrl") or source_avatar,
        "avatarFallback": source_avatar,
        "avatarArchived": bool(avatar.get("publicUrl")),
        "messages": metrics.get("num_messages"),
        "rating": metrics.get("rating_score"),
        "savedMask": int(field_mask(record) or 0),
        "definitionVisible": definition_visible,
        "definitionSize": str(lk.get("definition_size_category") or "").lower(),
        "hasLorebooks": has_lorebooks,
        "language": str(lk.get("language") or "").lower(),
    }


def _fnv1a32(text: str) -> int:
    h = 2166136261
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def text_search_bucket(bot_id: str) -> str:
    return f"{_fnv1a32(str(bot_id or '').lower()) & (TEXT_SEARCH_SHARDS - 1):02x}"


def _search_value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return str(value)
    return str(value)


def searchable_tokens(record: dict[str, Any]) -> set[str]:
    pieces: list[str] = []
    lk = last_known(record)
    for key in SEARCH_FIELD_LEAVES:
        if key in lk and meaningful(lk.get(key)):
            pieces.append(_search_value_text(lk.get(key)))
    for event in record.get("fieldHistory") or []:
        if not isinstance(event, dict):
            continue
        leaf = str(event.get("path") or "").split(".")[-1]
        if leaf not in SEARCH_FIELD_LEAVES:
            continue
        if meaningful(event.get("from")):
            pieces.append(_search_value_text(event.get("from")))
        if meaningful(event.get("to")):
            pieces.append(_search_value_text(event.get("to")))
    tokens: set[str] = set()
    for piece in pieces:
        for token in re.findall(r"[a-z0-9][a-z0-9_'’-]{2,}", piece.lower()):
            tokens.add(token.replace("’", "'"))
            if len(tokens) >= 2048:
                return tokens
    return tokens


def text_bloom(record: dict[str, Any]) -> str:
    bits = bytearray(TEXT_BLOOM_BYTES)
    for token in searchable_tokens(record):
        for seed in range(TEXT_BLOOM_HASHES):
            bit = _fnv1a32(f"{seed}:{token}") % (TEXT_BLOOM_BYTES * 8)
            bits[bit >> 3] |= 1 << (bit & 7)
    return base64.b64encode(bytes(bits)).decode("ascii")


def text_search_meta(record: dict[str, Any]) -> list[Any]:
    return [
        bot_name(record),
        creator_name(record),
        bool(is_nsfw(record)),
        int(field_mask(record) or 0),
        text_bloom(record),
        record.get("lastSeenAt"),
    ]


def month_key(value: Any) -> str:
    text = str(value or "")
    return text[:7] if len(text) >= 7 and text[4:5] == "-" else "unknown"


def activity_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    bot_id = str(record.get("id") or "").lower()
    base = {
        "id": bot_id,
        "name": bot_name(record),
        "creator": creator_name(record),
        "title": bot_title(record),
        "isNsfw": is_nsfw(record),
    }
    rows: list[dict[str, Any]] = []
    if record.get("firstSeenAt"):
        rows.append({**base, "at": record.get("firstSeenAt"), "type": "new", "source": "archive"})
    saw_unavailable = False
    for event in record.get("availabilityHistory") or []:
        if not isinstance(event, dict):
            continue
        status = str(event.get("status") or "").lower()
        at = event.get("from") or event.get("at")
        source = str(event.get("source") or "")
        if status in {"deleted", "missing", "unavailable"}:
            rows.append({**base, "at": at, "type": "deleted", "source": source})
            saw_unavailable = True
        elif status == "public" and saw_unavailable:
            rows.append({
                **base,
                "at": at,
                "type": "restored" if source.lower() == "character-api" else "restore-candidate",
                "source": source,
            })
            saw_unavailable = False
    current_status = record.get("status") or {}
    candidate = current_status.get("restoreCandidate") if isinstance(current_status, dict) else None
    if str(current_status.get("current") or "").lower() == "deleted" and isinstance(candidate, dict) and candidate.get("at"):
        sig = (str(candidate.get("at")), str(candidate.get("source") or "listing-candidate"))
        if not any(x.get("type") == "restore-candidate" and (str(x.get("at")), str(x.get("source"))) == sig for x in rows):
            rows.append({**base, "at": candidate.get("at"), "type": "restore-candidate", "source": candidate.get("source") or "listing-candidate"})
    return rows


def change_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    bot_id = str(record.get("id") or "").lower()
    name = bot_name(record)
    creator = creator_name(record)
    creator_identity = creator_id(record)
    rows: list[dict[str, Any]] = []
    for event in record.get("fieldHistory") or []:
        if not isinstance(event, dict):
            continue
        path = str(event.get("path") or "")
        leaf = path.split(".")[-1]
        if not path or leaf in VOLATILE_LEAVES:
            continue
        rows.append({
            "id": bot_id,
            "name": name,
            "creator": creator,
            "creatorId": creator_identity,
            "at": event.get("at"),
            "path": path,
            "kind": event.get("kind") or "value",
            "source": event.get("source") or "",
            "from": clean_text(event.get("from")),
            "to": clean_text(event.get("to")),
        })
    return rows


def restored_row(record: dict[str, Any]) -> dict[str, Any] | None:
    restored = restored_events(record)
    if not restored:
        return None
    lk = last_known(record)
    avatar = record.get("avatarArchive") or {}
    latest = restored[-1]
    return {
        "id": str(record.get("id") or "").lower(),
        "name": bot_name(record),
        "title": bot_title(record),
        "creator": creator_name(record),
        "tags": lk.get("tags") if isinstance(lk.get("tags"), list) else [],
        "isNsfw": is_nsfw(record),
        "avatar": avatar.get("publicUrl") or lk.get("avatar_url") or lk.get("avatar") or lk.get("image"),
        "avatarFallback": lk.get("avatar_url") or lk.get("avatar") or lk.get("image"),
        "avatarArchived": bool(avatar.get("publicUrl")),
        "restoredAt": latest.get("from"),
        "restoreCount": len(restored),
        "firstSeenAt": record.get("firstSeenAt"),
        "lastSeenAt": record.get("lastSeenAt"),
        "status": str((record.get("status") or {}).get("current") or "unknown").lower(),
    }


def _index_key(store: R2ArchiveStore, name: str) -> str:
    return store.key("indexes", name)


def _creator_key(store: R2ArchiveStore, bucket: str) -> str:
    return store.key("indexes", "creators", f"{bucket}.json")


def _text_key(store: R2ArchiveStore, bucket: str) -> str:
    return store.key("indexes", "text-search", f"{bucket}.json")


def _load_state(store: R2ArchiveStore) -> dict[str, Any]:
    state = getattr(store, "_site_indexes_state", None)
    if isinstance(state, dict):
        return state
    times_payload = store.get_json(_index_key(store, "archive-times.json"), {}) or {}
    changes_payload = store.get_json(_index_key(store, "changes.json"), {}) or {}
    restored_payload = store.get_json(_index_key(store, "restored.json"), {}) or {}
    activity_payload = store.get_json(_index_key(store, "activity.json"), {}) or {}
    times_ok = (
        int(times_payload.get("schemaVersion") or 0) == SCHEMA_VERSION
        and times_payload.get("complete") is True
        and isinstance(times_payload.get("bots"), dict)
    )
    changes_ok = int(changes_payload.get("schemaVersion") or 0) == SCHEMA_VERSION and isinstance(changes_payload.get("changes"), list)
    restored_ok = int(restored_payload.get("schemaVersion") or 0) == SCHEMA_VERSION and isinstance(restored_payload.get("bots"), list)
    activity_ok = int(activity_payload.get("schemaVersion") or 0) == SCHEMA_VERSION and isinstance(activity_payload.get("events"), list)
    state = {
        "baseComplete": bool(times_ok and changes_ok and restored_ok and activity_ok),
        "times": dict(times_payload.get("bots") or {}) if times_ok else {},
        "changes": list(changes_payload.get("changes") or []) if changes_ok else [],
        "restored": {str(x.get("id") or "").lower(): x for x in (restored_payload.get("bots") or []) if isinstance(x, dict) and x.get("id")} if restored_ok else {},
        "activity": list(activity_payload.get("events") or []) if activity_ok else [],
        "historyPending": {},
        "creatorShards": {},
        "textShards": {},
        "dirtyCreators": set(),
        "dirtyText": set(),
        "dirtyTimes": False,
        "dirtyChanges": False,
        "dirtyRestored": False,
        "dirtyActivity": False,
    }
    setattr(store, "_site_indexes_state", state)
    return state


def _load_creator_shard(store: R2ArchiveStore, state: dict[str, Any], bucket: str) -> dict[str, Any]:
    shards = state["creatorShards"]
    if bucket not in shards:
        payload = store.get_json(_creator_key(store, bucket), {}) or {}
        schema_ok = int(payload.get("schemaVersion") or 0) == SCHEMA_VERSION
        creators = payload.get("creators") if schema_ok else {}
        if state.get("baseComplete") and payload and not schema_ok:
            state["baseComplete"] = False
        shards[bucket] = deepcopy(creators) if isinstance(creators, dict) else {}
    return shards[bucket]


def _load_text_shard(store: R2ArchiveStore, state: dict[str, Any], bucket: str) -> dict[str, Any]:
    shards = state["textShards"]
    if bucket not in shards:
        payload = store.get_json(_text_key(store, bucket), {}) or {}
        schema_ok = int(payload.get("schemaVersion") or 0) == SCHEMA_VERSION
        bots = payload.get("bots") if schema_ok else {}
        if state.get("baseComplete") and payload and not schema_ok:
            state["baseComplete"] = False
        shards[bucket] = deepcopy(bots) if isinstance(bots, dict) else {}
    return shards[bucket]


def _update_text_record(store: R2ArchiveStore, state: dict[str, Any], record: dict[str, Any]) -> None:
    bot_id = str(record.get("id") or "").lower()
    if not bot_id:
        return
    bucket = text_search_bucket(bot_id)
    shard = _load_text_shard(store, state, bucket)
    meta = text_search_meta(record)
    if shard.get(bot_id) != meta:
        shard[bot_id] = meta
        state["dirtyText"].add(bucket)


def _update_creator_record(store: R2ArchiveStore, state: dict[str, Any], *, creator: str, bot_id: str, meta: dict[str, Any] | None) -> None:
    key = creator_key(creator)
    if not key:
        return
    bucket = creator_bucket(key)
    shard = _load_creator_shard(store, state, bucket)
    entry = shard.get(key)
    if not isinstance(entry, dict):
        entry = {"display": creator, "bots": {}}
        shard[key] = entry
    if creator and not entry.get("display"):
        entry["display"] = creator
    bots = entry.setdefault("bots", {})
    before = bots.get(bot_id)
    if meta is None:
        if bot_id in bots:
            bots.pop(bot_id, None)
            state["dirtyCreators"].add(bucket)
        if not bots:
            shard.pop(key, None)
    elif before != meta:
        bots[bot_id] = meta
        state["dirtyCreators"].add(bucket)


def note_record(store: R2ArchiveStore, record: dict[str, Any]) -> None:
    bot_id = str(record.get("id") or "").lower()
    if not bot_id:
        return
    state = _load_state(store)
    creator = creator_name(record)
    key = creator_key(creator)
    old = state["times"].get(bot_id)
    old_creator = str(old[2] if isinstance(old, list) and len(old) > 2 else "")
    new_time = [epoch_ms(record.get("firstSeenAt")), epoch_ms(record.get("lastSeenAt")), key]
    if old != new_time:
        state["times"][bot_id] = new_time
        state["dirtyTimes"] = True
    if old_creator and old_creator != key:
        _update_creator_record(store, state, creator=old_creator, bot_id=bot_id, meta=None)
    if key:
        _update_creator_record(store, state, creator=creator, bot_id=bot_id, meta=creator_bot_meta(record))
    _update_text_record(store, state, record)

    existing_keys = {
        (str(x.get("id") or ""), str(x.get("at") or ""), str(x.get("path") or ""), str(x.get("kind") or ""), str(x.get("source") or ""))
        for x in state["changes"]
        if isinstance(x, dict)
    }
    added = False
    for row in change_rows(record):
        sig = (row["id"], str(row.get("at") or ""), row["path"], row["kind"], row["source"])
        if sig not in existing_keys:
            state["changes"].append(row)
            existing_keys.add(sig)
            state["historyPending"].setdefault(month_key(row.get("at")), []).append(row)
            added = True
    if added:
        state["changes"].sort(key=lambda x: str(x.get("at") or ""), reverse=True)
        del state["changes"][CHANGES_LIMIT:]
        state["dirtyChanges"] = True

    existing_activity = {
        (str(x.get("id") or ""), str(x.get("at") or ""), str(x.get("type") or ""), str(x.get("source") or ""))
        for x in state["activity"] if isinstance(x, dict)
    }
    activity_added = False
    for row in activity_rows(record):
        sig = (row["id"], str(row.get("at") or ""), str(row.get("type") or ""), str(row.get("source") or ""))
        if sig not in existing_activity:
            state["activity"].append(row)
            existing_activity.add(sig)
            activity_added = True
    if activity_added:
        state["activity"].sort(key=lambda x: str(x.get("at") or ""), reverse=True)
        del state["activity"][ACTIVITY_LIMIT:]
        state["dirtyActivity"] = True

    restored = restored_row(record)
    previous_restored = state["restored"].get(bot_id)
    if restored:
        if previous_restored != restored:
            state["restored"][bot_id] = restored
            state["dirtyRestored"] = True
    elif bot_id in state["restored"]:
        state["restored"].pop(bot_id, None)
        state["dirtyRestored"] = True


def _flush_change_history(store: R2ArchiveStore, state: dict[str, Any], now: str) -> None:
    pending = state.get("historyPending") or {}
    if not pending:
        return
    manifest_key = _index_key(store, "changes-history.json")
    manifest = store.get_json(manifest_key, {}) or {}
    months = dict(manifest.get("months") or {})
    for month, rows in sorted(pending.items()):
        if not rows:
            continue
        key = _index_key(store, f"changes-history/{month}.json")
        payload = store.get_json(key, {}) or {}
        existing = list(payload.get("changes") or [])
        seen = {
            (str(x.get("id") or ""), str(x.get("at") or ""), str(x.get("path") or ""),
             str(x.get("kind") or ""), str(x.get("source") or ""))
            for x in existing if isinstance(x, dict)
        }
        for row in rows:
            sig = (row["id"], str(row.get("at") or ""), row["path"], row["kind"], row["source"])
            if sig not in seen:
                existing.append(row)
                seen.add(sig)
        existing.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
        store.put_json(key, {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "month": month,
            "changes": existing,
        }, public=True)
        months[month] = len(existing)
    store.put_json(manifest_key, {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now,
        "months": dict(sorted(months.items(), reverse=True)),
        "totalChanges": sum(int(v or 0) for v in months.values()),
    }, public=True)
    state["historyPending"] = {}


def flush_indexes(store: R2ArchiveStore, *, force: bool = False) -> None:
    state = _load_state(store)
    if not state.get("baseComplete"):
        # Never turn a schema migration/missing base index into a deceptively
        # "complete" partial index just because a normal crawler run touched a
        # few records. The deployment backfill owns creating the first complete
        # index for a schema version.
        print("Site indexes: incremental publish skipped; full index rebuild required.", flush=True)
        return
    now = utc_now()
    if force or state["dirtyTimes"]:
        store.put_json(_index_key(store, "archive-times.json"), {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "complete": True,
            "bots": state["times"],
        }, public=True)
        state["dirtyTimes"] = False
    if force or state["dirtyChanges"]:
        store.put_json(_index_key(store, "changes.json"), {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "changes": state["changes"][:CHANGES_LIMIT],
        }, public=True)
        state["dirtyChanges"] = False
    if force or state["dirtyActivity"]:
        store.put_json(_index_key(store, "activity.json"), {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "events": state["activity"][:ACTIVITY_LIMIT],
        }, public=True)
        state["dirtyActivity"] = False
    if force or state["dirtyRestored"]:
        rows = sorted(state["restored"].values(), key=lambda x: str(x.get("restoredAt") or ""), reverse=True)[:RESTORED_LIMIT]
        store.put_json(_index_key(store, "restored.json"), {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "bots": rows,
        }, public=True)
        state["dirtyRestored"] = False
    _flush_change_history(store, state, now)
    for bucket in sorted(state["dirtyCreators"]):
        creators = state["creatorShards"].get(bucket) or {}
        store.put_json(_creator_key(store, bucket), {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "creators": creators,
        }, public=True)
    state["dirtyCreators"].clear()
    for bucket in sorted(state["dirtyText"]):
        bots = state["textShards"].get(bucket) or {}
        store.put_json(_text_key(store, bucket), {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "bots": bots,
        }, public=True)
    if state["dirtyText"]:
        manifest = store.get_json(_index_key(store, "text-search.json"), {}) or {}
        store.put_json(_index_key(store, "text-search.json"), {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "shards": [f"{i:02x}" for i in range(TEXT_SEARCH_SHARDS)],
            "botCount": len(state["times"]),
            "bloomBytes": TEXT_BLOOM_BYTES,
            "hashes": TEXT_BLOOM_HASHES,
        }, public=True)
    state["dirtyText"].clear()


def index_is_complete(store: R2ArchiveStore) -> bool:
    for name in ("archive-times.json", "changes.json", "changes-history.json", "activity.json", "restored.json", "repair-queue.json", "text-search.json", "health.json"):
        payload = store.get_json(_index_key(store, name), None)
        if not isinstance(payload, dict) or int(payload.get("schemaVersion") or 0) != SCHEMA_VERSION:
            return False
    times = store.get_json(_index_key(store, "archive-times.json"), {}) or {}
    return times.get("complete") is True and isinstance(times.get("bots"), dict)


def rebuild_indexes(store: R2ArchiveStore, *, workers: int = 24) -> dict[str, Any]:
    prefix = store.bot_prefix.rstrip("/") + "/"
    keys = [key for key in store._list_keys(prefix) if key.endswith(".json")]
    print(f"Site indexes: scanning {len(keys):,} materialized bot records...", flush=True)

    times: dict[str, list[Any]] = {}
    changes: list[dict[str, Any]] = []
    activity: list[dict[str, Any]] = []
    restored: dict[str, dict[str, Any]] = {}
    creator_shards: dict[str, dict[str, Any]] = {}
    text_shards: dict[str, dict[str, Any]] = {}
    records_by_id: dict[str, dict[str, Any]] = {}

    health = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "materializedBots": 0,
        "unreadableRecords": 0,
        "missingId": 0,
        "missingCreator": 0,
        "missingName": 0,
        "deletedIndexEntries": 0,
        "deletedIndexMissingRecord": 0,
        "deletedStatusMismatch": 0,
        "richFieldMismatches": 0,
        "availabilityHistoryProblems": 0,
        "restoreCandidates": 0,
        "verifiedRestorations": 0,
        "stalePublicBots": 0,
        "repairQueueBots": 0,
    }

    def load_one(key: str):
        try:
            record = store.get_json(key, None)
            return record if isinstance(record, dict) else None
        except Exception:
            return None

    processed = 0
    with ThreadPoolExecutor(max_workers=max(1, min(32, int(workers)))) as pool:
        futures = [pool.submit(load_one, key) for key in keys]
        for future in as_completed(futures):
            record = future.result()
            processed += 1
            if not isinstance(record, dict):
                health["unreadableRecords"] += 1
                continue
            bot_id = str(record.get("id") or "").lower()
            if not bot_id:
                health["missingId"] += 1
                continue
            records_by_id[bot_id] = record
            health["materializedBots"] += 1
            creator = creator_name(record)
            ckey = creator_key(creator)
            if not ckey:
                health["missingCreator"] += 1
            if bot_name(record) == "Unknown bot":
                health["missingName"] += 1

            rows = record.get("availabilityHistory") or []
            if not isinstance(rows, list) or any(not isinstance(x, dict) or not x.get("status") for x in rows):
                health["availabilityHistoryProblems"] += 1

            times[bot_id] = [epoch_ms(record.get("firstSeenAt")), epoch_ms(record.get("lastSeenAt")), ckey]
            changes.extend(change_rows(record))
            activity.extend(activity_rows(record))
            health["restoreCandidates"] += len(restoration_candidates(record))
            health["verifiedRestorations"] += len(restored_events(record))
            rr = restored_row(record)
            if rr:
                restored[bot_id] = rr
            if ckey:
                bucket = creator_bucket(ckey)
                creators = creator_shards.setdefault(bucket, {})
                entry = creators.setdefault(ckey, {"display": creator, "bots": {}})
                entry["bots"][bot_id] = creator_bot_meta(record)
            tb = text_search_bucket(bot_id)
            text_shards.setdefault(tb, {})[bot_id] = text_search_meta(record)

            if processed % 5000 == 0:
                print(f"  site-index scan {processed:,}/{len(keys):,}", flush=True)

    changes.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    all_changes = changes
    changes = all_changes[:CHANGES_LIMIT]
    activity.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    activity = activity[:ACTIVITY_LIMIT]
    restored_rows = sorted(restored.values(), key=lambda x: str(x.get("restoredAt") or ""), reverse=True)[:RESTORED_LIMIT]

    store.load_deleted_index()
    health["deletedIndexEntries"] = len(store.deleted_index)
    for bot_id in store.deleted_index:
        record = records_by_id.get(str(bot_id).lower())
        if record is None:
            health["deletedIndexMissingRecord"] += 1
        elif str((record.get("status") or {}).get("current") or "").lower() != "deleted":
            health["deletedStatusMismatch"] += 1

    rich_payload = store.get_json(_index_key(store, "rich-fields.json"), {}) or {}
    rich_bots = rich_payload.get("bots") if isinstance(rich_payload, dict) else {}
    if not isinstance(rich_bots, dict):
        rich_bots = {}
    for bot_id, record in records_by_id.items():
        expected = int(field_mask(record) or 0)
        actual = int(rich_bots.get(bot_id) or 0)
        if expected != actual:
            health["richFieldMismatches"] += 1

    now = utc_now()
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    repair_rows: list[dict[str, Any]] = []
    for bot_id, record in records_by_id.items():
        status = str((record.get("status") or {}).get("current") or "").lower()
        reasons: list[str] = []
        last_seen = str(record.get("lastSeenAt") or "")
        try:
            seen_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        except Exception:
            seen_dt = None
        if status == "public" and seen_dt and seen_dt < stale_cutoff:
            reasons.append("stale-public")
            health["stalePublicBots"] += 1
        if not creator_name(record):
            reasons.append("missing-creator")
        if bot_name(record) == "Unknown bot":
            reasons.append("missing-name")
        lk = last_known(record)
        if lk.get("definition_visible") is True and not int(field_mask(record) or 0):
            reasons.append("visible-definition-not-captured")
        if reasons:
            repair_rows.append({
                "id": bot_id,
                "reasons": reasons,
                "lastSeenAt": record.get("lastSeenAt"),
                "status": status,
            })
    repair_rows.sort(key=lambda x: (
        0 if "visible-definition-not-captured" in x["reasons"] else 1,
        0 if "missing-name" in x["reasons"] or "missing-creator" in x["reasons"] else 1,
        str(x.get("lastSeenAt") or ""),
    ))
    repair_rows = repair_rows[:10000]
    health["repairQueueBots"] = len(repair_rows)

    store.put_json(_index_key(store, "archive-times.json"), {
        "schemaVersion": SCHEMA_VERSION, "generatedAt": now, "complete": True, "bots": times,
    }, public=True)
    store.put_json(_index_key(store, "changes.json"), {
        "schemaVersion": SCHEMA_VERSION, "generatedAt": now, "changes": changes,
    }, public=True)
    history_months: dict[str, int] = {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in all_changes:
        grouped.setdefault(month_key(row.get("at")), []).append(row)
    for month, rows in sorted(grouped.items()):
        rows.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
        store.put_json(_index_key(store, f"changes-history/{month}.json"), {
            "schemaVersion": SCHEMA_VERSION, "generatedAt": now, "month": month, "changes": rows,
        }, public=True)
        history_months[month] = len(rows)
    store.put_json(_index_key(store, "changes-history.json"), {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now,
        "months": dict(sorted(history_months.items(), reverse=True)),
        "totalChanges": len(all_changes),
    }, public=True)
    store.put_json(_index_key(store, "activity.json"), {
        "schemaVersion": SCHEMA_VERSION, "generatedAt": now, "events": activity,
    }, public=True)
    store.put_json(_index_key(store, "restored.json"), {
        "schemaVersion": SCHEMA_VERSION, "generatedAt": now, "bots": restored_rows,
    }, public=True)
    store.put_json(_index_key(store, "repair-queue.json"), {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now,
        "staleAfterDays": 30,
        "bots": repair_rows,
    }, public=True)
    for bucket in [f"{i:02x}" for i in range(TEXT_SEARCH_SHARDS)]:
        store.put_json(_text_key(store, bucket), {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now,
            "bots": text_shards.get(bucket, {}),
        }, public=True)
    store.put_json(_index_key(store, "text-search.json"), {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now,
        "shards": [f"{i:02x}" for i in range(TEXT_SEARCH_SHARDS)],
        "botCount": sum(len(x) for x in text_shards.values()),
        "bloomBytes": TEXT_BLOOM_BYTES,
        "hashes": TEXT_BLOOM_HASHES,
    }, public=True)
    for bucket, creators in creator_shards.items():
        store.put_json(_creator_key(store, bucket), {
            "schemaVersion": SCHEMA_VERSION, "generatedAt": now, "creators": creators,
        }, public=True)

    health["creatorCount"] = sum(len(x) for x in creator_shards.values())
    health["changesIndexed"] = len(changes)
    health["changesPreserved"] = len(all_changes)
    health["activityEvents"] = len(activity)
    health["restoredBots"] = len(restored_rows)
    health["unverifiedRestoreCandidates"] = max(0, health["restoreCandidates"] - health["verifiedRestorations"])
    health["archiveTimesIndexed"] = len(times)
    health["richFieldIndexBots"] = len(rich_bots)
    health["textSearchBots"] = sum(len(x) for x in text_shards.values())
    health["generatedAt"] = now
    health["status"] = "healthy" if not any(
        health[key] for key in (
            "unreadableRecords", "missingId", "deletedIndexMissingRecord",
            "deletedStatusMismatch", "richFieldMismatches",
        )
    ) else "needs-attention"
    store.put_json(_index_key(store, "health.json"), health, public=True)

    state = {
        "baseComplete": True,
        "times": times,
        "changes": changes,
        "activity": activity,
        "historyPending": {},
        "restored": {x["id"]: x for x in restored_rows},
        "creatorShards": creator_shards,
        "textShards": text_shards,
        "dirtyCreators": set(),
        "dirtyText": set(),
        "dirtyTimes": False,
        "dirtyChanges": False,
        "dirtyRestored": False,
        "dirtyActivity": False,
    }
    setattr(store, "_site_indexes_state", state)

    print(
        "Site indexes complete: "
        f"{health['materializedBots']:,} detailed bot records, "
        f"{health['creatorCount']:,} creators, "
        f"{health['changesIndexed']:,} hot changes / {health['changesPreserved']:,} preserved, "
        f"{health['restoredBots']:,} verified restored bots.",
        flush=True,
    )
    return health


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--rebuild-if-missing", action="store_true")
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    store = R2ArchiveStore(config)
    if not store.is_migrated():
        print("ERROR: R2 migration marker is missing.")
        return 2

    if args.rebuild or (args.rebuild_if_missing and not index_is_complete(store)):
        rebuild_indexes(store, workers=args.workers)
        store.flush_usage(force=True)
        return 0

    health = store.get_json(_index_key(store, "health.json"), {}) or {}
    print(
        "Site indexes already complete: "
        f"{int(health.get('materializedBots') or 0):,} detailed bot records, "
        f"{int(health.get('creatorCount') or 0):,} creators."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
