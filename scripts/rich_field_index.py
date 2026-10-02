#!/usr/bin/env python3
"""Compact R2 index of bot IDs whose archived records contain rich text fields.

The website needs to answer "does the archive have Personality / Scenario /
Example Dialogue for this bot?" without reading hundreds or thousands of bot
objects per click.  This index stores one bitmask per materialized bot:

  1 = Personality
  2 = Scenario
  4 = Example Dialogue

Discovery-only shard records are intentionally absent until a richer record is
materialized.  The index is cheap to query, updated incrementally, and can be
rebuilt from all materialized bot objects when needed.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage_r2 import R2ArchiveStore

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
PERSONALITY = 1
SCENARIO = 2
DIALOGUE = 4

PERSONALITY_KEYS = ("persona", "personality", "definition", "character_definition", "characterDefinition")
SCENARIO_KEYS = ("scenario",)
DIALOGUE_KEYS = ("dialogue", "example_dialogue", "example_dialogues")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def best_field(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    last_known = record.get("lastKnown") or {}
    if isinstance(last_known, dict):
        for key in keys:
            if meaningful(last_known.get(key)):
                return last_known.get(key)

    current = record.get("current") or {}
    if isinstance(current, dict):
        preferred = (
            "character-api", "qol-bot-status-import", "typesense",
            "typesense:trending", "typesense:popular", "typesense:top-rated",
            "typesense:explore",
        )
        for source in preferred:
            row = current.get(source)
            if not isinstance(row, dict):
                continue
            for key in keys:
                if meaningful(row.get(key)):
                    return row.get(key)
        for row in current.values():
            if not isinstance(row, dict):
                continue
            for key in keys:
                if meaningful(row.get(key)):
                    return row.get(key)
    return None


def field_mask(record: dict[str, Any]) -> int:
    mask = 0
    if meaningful(best_field(record, PERSONALITY_KEYS)):
        mask |= PERSONALITY
    if meaningful(best_field(record, SCENARIO_KEYS)):
        mask |= SCENARIO
    if meaningful(best_field(record, DIALOGUE_KEYS)):
        mask |= DIALOGUE
    return mask


def field_flags(record: dict[str, Any]) -> dict[str, bool]:
    mask = field_mask(record)
    return {
        "personality": bool(mask & PERSONALITY),
        "scenario": bool(mask & SCENARIO),
        "dialogue": bool(mask & DIALOGUE),
    }


def _key(store: R2ArchiveStore) -> str:
    return store.key("indexes", "rich-fields.json")


def _load_state(store: R2ArchiveStore) -> dict[str, Any]:
    state = getattr(store, "_rich_field_index_state", None)
    if isinstance(state, dict):
        return state
    payload = store.get_json(_key(store), None)
    bots: dict[str, int] = {}
    complete = False
    if isinstance(payload, dict) and int(payload.get("schemaVersion") or 0) == SCHEMA_VERSION:
        raw = payload.get("bots")
        if isinstance(raw, dict):
            for bot_id, mask in raw.items():
                try:
                    value = int(mask)
                except (TypeError, ValueError):
                    continue
                if value:
                    bots[str(bot_id).lower()] = value
        complete = payload.get("complete") is True
    state = {"bots": bots, "complete": complete, "dirty": False}
    setattr(store, "_rich_field_index_state", state)
    return state


def note_record(store: R2ArchiveStore, record: dict[str, Any]) -> None:
    bot_id = str(record.get("id") or "").lower()
    if not bot_id:
        return
    state = _load_state(store)
    mask = field_mask(record)
    prior = int(state["bots"].get(bot_id) or 0)
    if mask:
        if prior != mask:
            state["bots"][bot_id] = mask
            state["dirty"] = True
    elif bot_id in state["bots"]:
        state["bots"].pop(bot_id, None)
        state["dirty"] = True


def _payload(state: dict[str, Any]) -> dict[str, Any]:
    bots = state["bots"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "complete": bool(state.get("complete")),
        "counts": {
            "bots": len(bots),
            "personality": sum(1 for mask in bots.values() if int(mask) & PERSONALITY),
            "scenario": sum(1 for mask in bots.values() if int(mask) & SCENARIO),
            "dialogue": sum(1 for mask in bots.values() if int(mask) & DIALOGUE),
        },
        "bots": dict(sorted(bots.items())),
    }


def flush_index(store: R2ArchiveStore, *, force: bool = False) -> dict[str, Any] | None:
    state = _load_state(store)
    if not (force or state.get("dirty")):
        return None
    payload = _payload(state)
    store.put_json(_key(store), payload, public=True)
    state["dirty"] = False
    return payload


def rebuild_index(store: R2ArchiveStore, *, refresh_deleted: bool = True, workers: int = 24) -> dict[str, Any]:
    prefix = store.bot_prefix.rstrip("/") + "/"
    keys = [key for key in store._list_keys(prefix) if key.endswith(".json")]
    print(f"Rich-field index: scanning {len(keys):,} materialized bot records...", flush=True)

    state = {"bots": {}, "complete": True, "dirty": True}
    setattr(store, "_rich_field_index_state", state)
    deleted_records: dict[str, dict[str, Any]] = {}
    deleted_ids = set()
    if refresh_deleted:
        store.load_deleted_index()
        deleted_ids = {str(x).lower() for x in store.deleted_index}

    def load_one(key: str):
        try:
            record = store.get_json(key, None)
            return key, record if isinstance(record, dict) else None
        except Exception:
            return key, None

    processed = errors = 0
    with ThreadPoolExecutor(max_workers=max(1, min(32, int(workers)))) as pool:
        futures = [pool.submit(load_one, key) for key in keys]
        for future in as_completed(futures):
            key, record = future.result()
            processed += 1
            if not isinstance(record, dict):
                errors += 1
                continue
            bot_id = str(record.get("id") or "").lower()
            if not bot_id:
                errors += 1
                continue
            mask = field_mask(record)
            if mask:
                state["bots"][bot_id] = mask
            if bot_id in deleted_ids:
                deleted_records[bot_id] = record
            if processed % 5000 == 0:
                print(f"  rich-field scan {processed:,}/{len(keys):,}", flush=True)

    payload = flush_index(store, force=True) or _payload(state)

    deleted_counts = {"personality": 0, "scenario": 0, "dialogue": 0}
    if refresh_deleted:
        for bot_id, summary in list(store.deleted_index.items()):
            record = deleted_records.get(str(bot_id).lower())
            if not isinstance(record, dict):
                continue
            flags = field_flags(record)
            next_summary = dict(summary or {})
            next_summary["savedFields"] = flags
            next_summary["richFieldsVersion"] = SCHEMA_VERSION
            store.set_deleted_summary(bot_id, next_summary)
            for key, value in flags.items():
                if value:
                    deleted_counts[key] += 1
        store.save_deleted_index()
        store.publish_deleted_index(force=True)

    print(
        "Rich-field index complete: "
        f"{payload['counts']['personality']:,} personality, "
        f"{payload['counts']['scenario']:,} scenario, "
        f"{payload['counts']['dialogue']:,} dialogue; "
        f"{errors:,} unreadable records.",
        flush=True,
    )
    if refresh_deleted:
        print(
            "Deleted rich fields: "
            f"{deleted_counts['personality']:,} personality, "
            f"{deleted_counts['scenario']:,} scenario, "
            f"{deleted_counts['dialogue']:,} dialogue.",
            flush=True,
        )
    return {"index": payload, "deleted": deleted_counts, "errors": errors}


def index_is_complete(store: R2ArchiveStore) -> bool:
    payload = store.get_json(_key(store), None)
    return bool(
        isinstance(payload, dict)
        and int(payload.get("schemaVersion") or 0) == SCHEMA_VERSION
        and payload.get("complete") is True
        and isinstance(payload.get("bots"), dict)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--rebuild-if-missing", action="store_true")
    parser.add_argument("--no-refresh-deleted", action="store_true")
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()

    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    store = R2ArchiveStore(config)
    if not store.is_migrated():
        print("ERROR: R2 migration marker is missing.")
        return 2

    should_rebuild = args.rebuild or (args.rebuild_if_missing and not index_is_complete(store))
    if not should_rebuild:
        payload = store.get_json(_key(store), {}) or {}
        counts = payload.get("counts") or {}
        print(
            "Rich-field index already complete: "
            f"{int(counts.get('personality') or 0):,} personality, "
            f"{int(counts.get('scenario') or 0):,} scenario, "
            f"{int(counts.get('dialogue') or 0):,} dialogue."
        )
        return 0

    rebuild_index(
        store,
        refresh_deleted=not args.no_refresh_deleted,
        workers=args.workers,
    )
    store.flush_usage(force=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
