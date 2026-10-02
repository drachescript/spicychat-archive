#!/usr/bin/env python3
"""Import SpicyChat QoL Bot Status Center saved copies into the R2 archive.

The upload worker only places an authenticated export bundle into a private-ish
transient R2 queue. This script runs inside the normal archive workflow and does
the actual merge using the archive's R2 credentials.

Safety rules:
- only a whitelist of public bot/profile fields is accepted;
- user chats, cookies, account data, extension settings and arbitrary unknown
  fields are never copied into bot records;
- an imported snapshot never marks a bot public or deleted by itself;
- existing last-known values are never overwritten by an older imported copy;
- imports can fill fields the archive has never observed;
- every imported bot is queued for normal character-API enrichment/verification;
- exact duplicate snapshots are skipped.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import archive as legacy
from storage_r2 import R2ArchiveStore
from rich_field_index import flush_index as flush_rich_field_index, note_record as note_rich_field_record


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "qol-bot-status-import"
INBOX_PREFIX = "_imports/bot-status/queued/"
RECEIPT_PREFIX = "_meta/imports/bot-status/receipts"
FAILURE_PREFIX = "_meta/imports/bot-status/failures"
MAX_BUNDLES_PER_RUN = 10
MAX_IMPORT_HISTORY = 32
PRIORITY_QUEUE_LIMIT = 20_000

PUBLIC_IMPORT_FIELDS = {
    # IDs
    "character_id", "characterId", "id", "uuid",
    # Authored/public character data
    "name", "title", "description", "greeting", "greetings",
    "alternate_greetings", "personality", "scenario",
    "example_dialogue", "example_dialogues", "definition", "persona",
    "character_definition", "characterDefinition", "system_prompt",
    "post_history_instructions",
    # Public metadata
    "creator_username", "creator", "language", "type", "visibility", "tags",
    "avatar_url", "avatar", "image", "avatar_is_nsfw", "is_nsfw",
    "definition_visible", "definition_size_category", "token_count",
    "has_lorebooks", "lorebooks", "group_size_category", "group_addable",
    "createdAt", "updatedAt",
    # Public counters may be historically useful but never drive status.
    "num_messages", "num_messages_24h", "rating_score", "rating_count",
}

QOL_NESTED_FIELD_ALIASES = {
    "name": "name",
    "title": "title",
    "description": "description",
    "greeting": "greeting",
    "personality": "personality",
    "scenario": "scenario",
    "exampleDialogues": "example_dialogues",
    "tags": "tags",
    "visibility": "visibility",
    "creator": "creator",
    "image": "image",
    "messageCount": "num_messages",
    "rating": "rating_score",
    "tokenCount": "token_count",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    except Exception:
        return fallback


def _time_key(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _min_time(a: str | None, b: str | None) -> str | None:
    if not a:
        return b
    if not b:
        return a
    return a if _time_key(a) <= _time_key(b) else b


def _max_time(a: str | None, b: str | None) -> str | None:
    if not a:
        return b
    if not b:
        return a
    return a if _time_key(a) >= _time_key(b) else b


def _priority_add(state: dict[str, Any], key: str, bot_id: str) -> None:
    q = state.setdefault(key, [])
    if bot_id not in q:
        q.append(bot_id)
    if len(q) > PRIORITY_QUEUE_LIMIT:
        del q[: len(q) - PRIORITY_QUEUE_LIMIT]


def _unwrap_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    current = value
    # Common wrapper layers used by APIs/extensions. Keep this deliberately
    # conservative; arbitrary unrelated nested objects are not traversed.
    for _ in range(4):
        moved = False
        for key in ("snapshot", "savedCopy", "character", "bot", "record"):
            nested = current.get(key)
            if isinstance(nested, dict):
                current = nested
                moved = True
                break
        if not moved:
            data = current.get("data")
            if isinstance(data, dict) and any(
                k in data for k in ("character_id", "characterId", "id", "name", "title")
            ):
                current = data
                moved = True
        if not moved:
            break
    return current if isinstance(current, dict) else {}


def sanitize_snapshot(snapshot: dict[str, Any], bot_id: str) -> dict[str, Any]:
    raw = _unwrap_snapshot(snapshot)
    clean: dict[str, Any] = {}
    for key in PUBLIC_IMPORT_FIELDS:
        if key in raw:
            clean[key] = deepcopy(raw[key])

    # QoL Bot Status Center stores its public character copy under
    # snapshot.fields. Translate that shape into the archive's canonical public
    # fields so both private direct imports and admin-approved public imports
    # dedupe/merge the same way.
    nested_fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    for source_key, archive_key in QOL_NESTED_FIELD_ALIASES.items():
        if archive_key not in clean and source_key in nested_fields:
            clean[archive_key] = deepcopy(nested_fields[source_key])

    # One canonical ID makes downstream archive helpers deterministic.
    clean["character_id"] = bot_id.lower()
    for other in ("characterId", "id", "uuid"):
        if other in clean and str(clean.get(other) or "").lower() != bot_id.lower():
            clean.pop(other, None)

    return legacy.clean_for_archive(clean)


def _container(payload: Any) -> Iterable[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("savedCopies", "bots", "records", "items", "snapshots"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return value.values()
    # A single snapshot export is also accepted.
    if any(k in payload for k in ("character_id", "characterId", "botId", "id")):
        return [payload]
    return []


def extract_entries(payload: Any, *, fallback_at: str | None = None):
    fallback_at = fallback_at or utc_now()
    exported_at = fallback_at
    if isinstance(payload, dict):
        exported_at = _parse_time(
            payload.get("exportedAt") or payload.get("createdAt"),
            fallback_at,
        )

    for item in _container(payload):
        if not isinstance(item, dict):
            continue
        raw = _unwrap_snapshot(item)
        bot_id = (
            item.get("botId")
            or item.get("characterId")
            or item.get("character_id")
            or item.get("id")
            or raw.get("character_id")
            or raw.get("characterId")
            or raw.get("id")
            or raw.get("uuid")
        )
        bot_id = str(bot_id or "").strip().lower()
        if not bot_id:
            continue

        saved_at = _parse_time(
            item.get("savedAt")
            or item.get("snapshotAt")
            or item.get("capturedAt")
            or item.get("updatedAt")
            or exported_at,
            exported_at,
        )
        snapshot = sanitize_snapshot(item, bot_id)
        if len(snapshot) <= 1:  # only canonical ID survived
            continue
        yield bot_id, saved_at, snapshot


def _fill_missing(base: dict[str, Any], incoming: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    out = deepcopy(base)
    changed = False
    for key, value in incoming.items():
        if isinstance(value, dict):
            existing = out.get(key) if isinstance(out.get(key), dict) else {}
            merged, nested_changed = _fill_missing(existing, value)
            if nested_changed:
                out[key] = merged
                changed = True
        elif legacy.meaningful(value) and not legacy.meaningful(out.get(key)):
            out[key] = deepcopy(value)
            changed = True
    return out, changed


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _new_import_record(bot_id: str, saved_at: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    metrics = {
        key: snapshot[key]
        for key in ("num_messages", "num_messages_24h", "rating_score", "rating_count", "token_count")
        if key in snapshot and legacy.meaningful(snapshot.get(key))
    }
    record = {
        "schemaVersion": 1,
        "id": bot_id,
        "firstSeenAt": saved_at,
        "lastSeenAt": saved_at,
        # Historical import is evidence that a saved public copy existed, but it
        # is NOT current availability evidence.
        "status": {
            "current": "unverified-import",
            "since": saved_at,
            "lastVerifiedAt": None,
            "evidence": "SpicyChat QoL Bot Status Center saved copy",
        },
        "sources": {
            SOURCE: {
                "lastImportedAt": saved_at,
                "kind": "historical-snapshot",
            }
        },
        "current": {SOURCE: deepcopy(snapshot)},
        "lastKnown": deepcopy(snapshot),
        "fieldHistory": [],
        "availabilityHistory": [{
            "status": "unverified-import",
            "from": saved_at,
            "source": SOURCE,
        }],
        "listings": {},
        "metrics": {
            "latest": deepcopy(metrics),
            "history": ([{"at": saved_at, "source": SOURCE, **metrics}] if metrics else []),
        },
        "avatarArchive": {},
        "imports": {
            "qolBotStatus": {
                "snapshots": [],
            }
        },
    }
    return record


def merge_snapshot(
    record: dict[str, Any] | None,
    *,
    bot_id: str,
    saved_at: str,
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], bool, bool]:
    """Return (record, changed, duplicate). Does not change verified availability."""
    digest = _snapshot_hash(snapshot)

    if record is None:
        record = _new_import_record(bot_id, saved_at, snapshot)
        history = record["imports"]["qolBotStatus"]["snapshots"]
        history.append({"at": saved_at, "sha256": digest})
        return record, True, False

    record = deepcopy(record)
    import_root = record.setdefault("imports", {}).setdefault("qolBotStatus", {})
    history = import_root.setdefault("snapshots", [])
    if any(row.get("sha256") == digest for row in history if isinstance(row, dict)):
        return record, False, True

    changed = False
    history.append({"at": saved_at, "sha256": digest})
    import_root["snapshots"] = history[-MAX_IMPORT_HISTORY:]
    changed = True

    # Keep the newest imported snapshot as the source's current value, without
    # pretending it is current SpicyChat availability.
    source_info = record.setdefault("sources", {}).setdefault(SOURCE, {})
    prior_import_at = source_info.get("lastImportedAt")
    if not prior_import_at or _time_key(saved_at) >= _time_key(prior_import_at):
        record.setdefault("current", {})[SOURCE] = deepcopy(snapshot)
        source_info["lastImportedAt"] = saved_at
        source_info["kind"] = "historical-snapshot"

    # Historical imports may fill gaps but never overwrite a value the archive
    # already knows from Typesense/character API/newer imports.
    merged, filled = _fill_missing(record.get("lastKnown") or {}, snapshot)
    if filled:
        record["lastKnown"] = merged
        changed = True

    record["firstSeenAt"] = _min_time(record.get("firstSeenAt"), saved_at) or saved_at
    record["lastSeenAt"] = _max_time(record.get("lastSeenAt"), saved_at) or saved_at

    # Preserve imported metrics only if this bot did not have any yet.
    imported_metrics = {
        key: snapshot[key]
        for key in ("num_messages", "num_messages_24h", "rating_score", "rating_count", "token_count")
        if key in snapshot and legacy.meaningful(snapshot.get(key))
    }
    metrics = record.setdefault("metrics", {"latest": {}, "history": []})
    if imported_metrics and not (metrics.get("latest") or {}):
        metrics["latest"] = deepcopy(imported_metrics)
        metrics.setdefault("history", []).append({"at": saved_at, "source": SOURCE, **imported_metrics})
        changed = True

    # Critically: status / lastVerifiedAt / deleted state are intentionally untouched.
    return record, changed, False


def decode_export_bytes(raw: bytes) -> Any:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def _bundle_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _receipt_key(store: R2ArchiveStore, digest: str) -> str:
    return store.key(RECEIPT_PREFIX, f"{digest}.json")


def _failure_key(store: R2ArchiveStore, digest: str) -> str:
    return store.key(FAILURE_PREFIX, f"{digest}.json")


def _read_queue_object(store: R2ArchiveStore, key: str) -> bytes:
    obj = store.s3.get_object(Bucket=store.bucket, Key=key)
    return obj["Body"].read()


def _delete_transient_queue_object(store: R2ArchiveStore, key: str) -> None:
    # The upload worker writes this transient object directly, so it was never
    # added to storage_r2.py's internal byte counter. Delete directly as well.
    store.s3.delete_object(Bucket=store.bucket, Key=key)


def list_inbox(store: R2ArchiveStore, max_bundles: int) -> list[str]:
    keys: list[str] = []
    token = None
    while len(keys) < max_bundles:
        kwargs: dict[str, Any] = {
            "Bucket": store.bucket,
            "Prefix": INBOX_PREFIX,
            "MaxKeys": min(1000, max_bundles - len(keys)),
        }
        if token:
            kwargs["ContinuationToken"] = token
        page = store.s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents") or []:
            key = str(obj.get("Key") or "")
            if key and not key.endswith("/"):
                keys.append(key)
                if len(keys) >= max_bundles:
                    break
        if not page.get("IsTruncated") or len(keys) >= max_bundles:
            break
        token = page.get("NextContinuationToken")
    return sorted(keys)


def process_payload(
    store: R2ArchiveStore,
    state: dict[str, Any],
    payload: Any,
    *,
    received_at: str,
) -> dict[str, int]:
    stats = {
        "entries": 0,
        "newRecords": 0,
        "updatedRecords": 0,
        "duplicates": 0,
        "invalid": 0,
        "queuedForEnrichment": 0,
        "queuedForImages": 0,
    }

    for bot_id, saved_at, snapshot in extract_entries(payload, fallback_at=received_at):
        stats["entries"] += 1

        known = bot_id in store.known_ids
        existing = store.load_bot(bot_id) if known else None
        record, changed, duplicate = merge_snapshot(
            existing,
            bot_id=bot_id,
            saved_at=saved_at,
            snapshot=snapshot,
        )

        if duplicate:
            stats["duplicates"] += 1
        elif changed:
            if not known:
                store.register_id(bot_id)
                stats["newRecords"] += 1
            else:
                stats["updatedRecords"] += 1
            store.save_bot(record)
            note_rich_field_record(store, record)

        # Verify imported bots through the normal public character API over future
        # scheduled runs. That API, not the import, decides current availability.
        before = len(state.setdefault("priorityEnrichment", []))
        _priority_add(state, "priorityEnrichment", bot_id)
        if len(state["priorityEnrichment"]) > before:
            stats["queuedForEnrichment"] += 1

        avatar = legacy.normalize_avatar_url(
            snapshot.get("avatar_url") or snapshot.get("avatar") or snapshot.get("image")
        )
        if avatar and not (record.get("avatarArchive") or {}).get("publicUrl"):
            before_images = len(state.setdefault("priorityImages", []))
            _priority_add(state, "priorityImages", bot_id)
            if len(state["priorityImages"]) > before_images:
                stats["queuedForImages"] += 1

    return stats


def _merge_stats(total: dict[str, int], row: dict[str, int]) -> None:
    for key, value in row.items():
        total[key] = int(total.get(key) or 0) + int(value or 0)


def process_one_raw_bundle(
    store: R2ArchiveStore,
    state: dict[str, Any],
    raw: bytes,
    *,
    queue_key: str | None = None,
    source_name: str = "local-file",
) -> dict[str, Any]:
    received_at = utc_now()
    digest = _bundle_hash(raw)
    receipt_key = _receipt_key(store, digest)

    if store.get_json(receipt_key, None):
        if queue_key:
            _delete_transient_queue_object(store, queue_key)
        return {
            "bundle": source_name,
            "sha256": digest,
            "alreadyImported": True,
            "stats": {},
        }

    try:
        payload = decode_export_bytes(raw)
        stats = process_payload(store, state, payload, received_at=received_at)
        if not stats["entries"]:
            raise ValueError("export contains no usable saved bot copies")

        receipt = {
            "schemaVersion": 1,
            "importedAt": received_at,
            "sha256": digest,
            "source": source_name,
            "stats": stats,
        }
        store.put_json(receipt_key, receipt)
        if queue_key:
            _delete_transient_queue_object(store, queue_key)
        return {
            "bundle": source_name,
            "sha256": digest,
            "alreadyImported": False,
            "stats": stats,
        }
    except Exception as exc:
        failure = {
            "schemaVersion": 1,
            "failedAt": received_at,
            "sha256": digest,
            "source": source_name,
            "error": str(exc)[:1000],
        }
        store.put_json(_failure_key(store, digest), failure)
        if queue_key:
            # Do not leave a potentially malformed/private upload sitting behind
            # the bucket's public custom domain. The user can re-export after fixing it.
            _delete_transient_queue_object(store, queue_key)
        return {
            "bundle": source_name,
            "sha256": digest,
            "failed": True,
            "error": str(exc),
            "stats": {},
        }


def write_summary(path: str | None, totals: dict[str, Any]) -> None:
    lines = [
        "# Bot Status Center import",
        "",
        f"- Bundles processed: **{totals.get('bundles', 0)}**",
        f"- Failed bundles: **{totals.get('failedBundles', 0)}**",
        f"- Saved copies read: **{totals.get('entries', 0):,}**",
        f"- New archive records: **{totals.get('newRecords', 0):,}**",
        f"- Existing records enriched from saved copies: **{totals.get('updatedRecords', 0):,}**",
        f"- Exact duplicate snapshots skipped: **{totals.get('duplicates', 0):,}**",
        f"- Queued for normal current-status enrichment: **{totals.get('queuedForEnrichment', 0):,}**",
        f"- Queued for avatar archiving: **{totals.get('queuedForImages', 0):,}**",
    ]
    text = "\n".join(lines) + "\n"
    print(text)
    if path:
        Path(path).write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inbox", action="store_true", help="Process authenticated R2 upload queue")
    mode.add_argument("--file", help="Import one local .json/.json.gz export using configured R2 credentials")
    ap.add_argument("--max-bundles", type=int, default=MAX_BUNDLES_PER_RUN)
    ap.add_argument("--summary-file")
    args = ap.parse_args()

    config = legacy.load_config()
    if str((config.get("storage") or {}).get("mode") or "").lower() != "r2":
        print("Bot Status import skipped: archive is not configured for R2.")
        write_summary(args.summary_file, {"bundles": 0})
        return 0
    if not R2ArchiveStore.credentials_present():
        print("ERROR: R2 credentials are required for Bot Status import.")
        return 2

    store = R2ArchiveStore(config)
    if not store.is_migrated():
        print("ERROR: R2 migration marker is missing.")
        return 2

    state = store.load_state()
    store.load_discovery_order()
    store.load_deleted_index()

    totals: dict[str, Any] = {
        "bundles": 0,
        "failedBundles": 0,
        "entries": 0,
        "newRecords": 0,
        "updatedRecords": 0,
        "duplicates": 0,
        "invalid": 0,
        "queuedForEnrichment": 0,
        "queuedForImages": 0,
    }

    if args.file:
        path = Path(args.file)
        raw = path.read_bytes()
        result = process_one_raw_bundle(
            store, state, raw, source_name=path.name
        )
        totals["bundles"] += 1
        if result.get("failed"):
            totals["failedBundles"] += 1
        _merge_stats(totals, result.get("stats") or {})
    else:
        keys = list_inbox(store, max(1, int(args.max_bundles)))
        if not keys:
            print("Bot Status import queue: empty.")
        for key in keys:
            print(f"Importing queued Bot Status export: {key}", flush=True)
            try:
                raw = _read_queue_object(store, key)
                result = process_one_raw_bundle(
                    store,
                    state,
                    raw,
                    queue_key=key,
                    source_name=key.rsplit("/", 1)[-1],
                )
            except Exception as exc:
                # A transport/R2 error is different from a malformed bundle: keep
                # the job failed so the queue object can be retried next run.
                print(f"ERROR reading queued import {key}: {exc}", file=sys.stderr)
                return 2

            totals["bundles"] += 1
            if result.get("failed"):
                totals["failedBundles"] += 1
                print(f"  failed: {result.get('error')}", flush=True)
            elif result.get("alreadyImported"):
                print("  already imported; removed duplicate queue upload.", flush=True)
            else:
                row = result.get("stats") or {}
                print(
                    "  imported: "
                    f"{row.get('entries', 0):,} snapshots, "
                    f"{row.get('newRecords', 0):,} new, "
                    f"{row.get('updatedRecords', 0):,} existing enriched, "
                    f"{row.get('duplicates', 0):,} duplicates.",
                    flush=True,
                )
            _merge_stats(totals, result.get("stats") or {})

    # Persist queues/new IDs before the normal archive crawler starts.
    store.flush_discovery_journal()
    store.save_discovery_order()
    store.save_deleted_index()
    store.save_state(state)
    flush_rich_field_index(store)
    store.flush_usage(force=True)

    write_summary(args.summary_file, totals)
    return 0 if totals["failedBundles"] == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
