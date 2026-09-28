#!/usr/bin/env python3
"""Finalize a migration that already has a completed R2 migration marker.

This exists for the case where R2 migration completed successfully, but a later
workflow step timed out before Git could commit config/runtime cleanup. It never
re-uploads the bot archive.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import archive as legacy
from migrate_r2 import prune_local
from storage_r2 import R2ArchiveStore

ROOT = Path(__file__).resolve().parents[1]


def build_runtime(config: dict[str, Any], store: R2ArchiveStore) -> dict[str, Any]:
    deleted_url = store.public_url(store.key("indexes", "deleted.json"))
    return {
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prune-local", action="store_true")
    parser.add_argument("--summary-file")
    args = parser.parse_args()

    config = legacy.load_config()
    if not R2ArchiveStore.credentials_present():
        raise SystemExit("R2 credentials are missing.")

    store = R2ArchiveStore(config)
    marker = store.get_json(store.marker_key, None)
    if not isinstance(marker, dict):
        raise SystemExit("R2 migration marker is missing; run the full migration instead.")

    expected = int(marker.get("botRecords") or 0)
    if expected <= 0:
        raise SystemExit("R2 migration marker does not contain a valid bot-record count.")

    print(f"Completed R2 migration marker found ({expected:,} bot records).", flush=True)
    print("Skipping bot/media re-upload and finalizing the Git-side switch.", flush=True)

    store.load_discovery_order()
    actual = len(store.discovery_order)
    if actual < expected:
        raise SystemExit(
            f"R2 discovery order contains only {actual:,} IDs, but migration marker expects {expected:,}."
        )

    # Verify a few actual archived records before deleting the Git-side copies.
    sample_positions = sorted({0, actual // 2, actual - 1})
    checked = 0
    for pos in sample_positions:
        bot_id = store.discovery_order[pos]
        record = store.load_bot(bot_id)
        if not isinstance(record, dict) or str(record.get("id") or "").lower() != bot_id:
            raise SystemExit(f"R2 verification failed for archived bot {bot_id}.")
        checked += 1

    config.setdefault("storage", {})["mode"] = "r2"
    legacy.write_json_if_changed(ROOT / "config.json", config)
    legacy.write_json_if_changed(ROOT / "data" / "runtime.json", build_runtime(config, store))

    if args.prune_local:
        prune_local()

    summary = (
        "# R2 migration finalization\n\n"
        f"- Existing completed migration reused: **yes**\n"
        f"- Bot records declared by marker: **{expected:,}**\n"
        f"- Discovery IDs currently in R2: **{actual:,}**\n"
        f"- Sample archived records verified: **{checked}**\n"
        f"- Bot/media archive re-uploaded: **no**\n"
        f"- Git working tree pruned: **{'yes' if args.prune_local else 'no'}**\n"
        f"- R2 mode activated in `config.json`: **yes**\n"
    )
    print(summary, flush=True)
    if args.summary_file:
        Path(args.summary_file).write_text(summary, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
