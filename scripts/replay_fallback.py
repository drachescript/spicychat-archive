#!/usr/bin/env python3
"""Replay compact Git fallback observations into the permanent R2 archive."""
from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
from typing import Any

import archive as legacy
from storage_r2 import R2ArchiveStore, StorageQuotaExceeded

ROOT = Path(__file__).resolve().parents[1]
OBS_DIR = ROOT / "fallback" / "observations"
FALLBACK_STATE = ROOT / "fallback" / "state.json"


def priority_add(state: dict[str, Any], key: str, bot_id: str, limit: int = 20000) -> None:
    q = state.setdefault(key, [])
    if bot_id not in q:
        q.append(bot_id)
    if len(q) > limit:
        del q[: len(q) - limit]


def read_lines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict) and row.get("id"):
                rows.append(row)
    return rows


def rewrite_lines(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-writes", type=int, default=None)
    ap.add_argument("--summary-file")
    args = ap.parse_args()

    if not OBS_DIR.exists() or not any(OBS_DIR.glob("*.jsonl.gz")):
        text = "# R2 fallback replay\n\n- No pending Git fallback journals.\n"
        print(text)
        if args.summary_file:
            Path(args.summary_file).write_text(text, encoding="utf-8")
        return 0

    config = legacy.load_config()
    if not R2ArchiveStore.credentials_present():
        print("R2 fallback replay skipped: R2 credentials are missing.")
        return 2
    store = R2ArchiveStore(config)
    if not store.is_migrated():
        print("R2 fallback replay skipped: migration marker is missing.")
        return 2

    state = store.load_state()
    store.load_discovery_order()
    store.load_deleted_index()
    max_writes = args.max_writes
    if max_writes is None:
        max_writes = int(os.environ.get("SPICYCHAT_R2_REPLAY_WRITES") or 25000)
    max_writes = max(0, int(max_writes))

    files_done = rows_seen = writes = unchanged = rich = 0
    stopped_reason = None

    for path in sorted(OBS_DIR.glob("*.jsonl.gz")):
        rows = read_lines(path)
        keep: list[dict[str, Any]] = []
        for idx, item in enumerate(rows):
            if writes >= max_writes:
                keep.extend(rows[idx:])
                stopped_reason = "replay write budget reached"
                break
            bot_id = str(item.get("id") or "").lower()
            if not bot_id:
                continue
            rows_seen += 1
            existing = store.load_bot(bot_id)
            old_known = (existing or {}).get("lastKnown") or {}
            old_avatar = legacy.normalize_avatar_url(old_known.get("avatar_url") or old_known.get("avatar") or old_known.get("image"))
            old_updated = old_known.get("updatedAt")
            old_def = old_known.get("definition_visible")
            record = existing
            changed = existing is None
            at = str(item.get("at") or legacy.utc_now())

            ts = item.get("typesense")
            if isinstance(ts, dict):
                ts.setdefault("character_id", bot_id)
                record, c = legacy.observe_bot(record, ts, source="typesense", at=at)
                changed = changed or c
                for surface in item.get("surfaces") or []:
                    record.setdefault("sources", {})[f"typesense:{surface}"] = {"lastSeenAt": at, "replayedFromGit": True}

            character = item.get("character")
            if isinstance(character, dict):
                character.setdefault("character_id", bot_id)
                record, c = legacy.observe_bot(record, character, source="character-api", at=at)
                changed = changed or c
                rich += 1

            if not record:
                continue
            new_known = record.get("lastKnown") or {}
            new_avatar = legacy.normalize_avatar_url(new_known.get("avatar_url") or new_known.get("avatar") or new_known.get("image"))
            if new_avatar and old_avatar and new_avatar != old_avatar:
                priority_add(state, "priorityImages", bot_id)
            if existing is not None:
                if new_known.get("updatedAt") != old_updated or new_known.get("definition_visible") != old_def:
                    priority_add(state, "priorityEnrichment", bot_id)

            if changed:
                try:
                    store.save_bot(record)
                    writes += 1
                    if writes % 250 == 0:
                        store.flush_discovery_journal()
                except StorageQuotaExceeded as exc:
                    keep.extend(rows[idx:])
                    stopped_reason = f"R2 storage guard stopped replay: {exc}"
                    break
            else:
                unchanged += 1

        rewrite_lines(path, keep)
        if not keep:
            files_done += 1
        if stopped_reason:
            break

    # Commit compact R2 state only if the object guard still allows it. These are
    # a handful of writes and are reserved by r2_guard.py before replay starts.
    try:
        store.save_state(state)
        store.save_discovery_order()
        store.save_deleted_index()
        store.flush_usage(force=True)
    except StorageQuotaExceeded as exc:
        stopped_reason = stopped_reason or f"R2 state write paused: {exc}"

    pending_files = len(list(OBS_DIR.glob("*.jsonl.gz"))) if OBS_DIR.exists() else 0
    if pending_files == 0:
        FALLBACK_STATE.unlink(missing_ok=True)
        try:
            OBS_DIR.rmdir()
            OBS_DIR.parent.rmdir()
        except OSError:
            pass

    text = "\n".join([
        "# R2 fallback replay",
        "",
        f"- Observation rows checked: **{rows_seen:,}**",
        f"- R2 bot writes: **{writes:,}** / {max_writes:,} budget",
        f"- Already-current records: **{unchanged:,}**",
        f"- Rich character observations replayed: **{rich:,}**",
        f"- Journal files fully drained: **{files_done:,}**",
        f"- Journal files still pending: **{pending_files:,}**",
        f"- Stop reason: **{stopped_reason or 'none'}**",
    ]) + "\n"
    print(text)
    if args.summary_file:
        Path(args.summary_file).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
