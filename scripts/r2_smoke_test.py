#!/usr/bin/env python3
"""Fast, non-destructive smoke test for the completed R2 migration."""
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

import requests

import archive as legacy
from storage_r2 import R2ArchiveStore


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-file")
    args = parser.parse_args()

    config = legacy.load_config()
    if not R2ArchiveStore.credentials_present():
        fail("R2 credentials are missing.")

    store = R2ArchiveStore(config)

    print("Smoke 1/6: checking completed migration marker...", flush=True)
    marker = store.get_json(store.marker_key, None)
    if not isinstance(marker, dict):
        fail("R2 migration marker is missing.")
    expected = int(marker.get("botRecords") or 0)
    if expected <= 0:
        fail("R2 migration marker has no valid bot-record count.")

    print("Smoke 2/6: checking compact discovery order...", flush=True)
    store.load_discovery_order()
    actual = len(store.discovery_order)
    if actual < expected:
        fail(f"Discovery order has {actual:,} IDs but marker expects {expected:,}.")

    print("Smoke 3/6: reading sample archived bot objects through S3...", flush=True)
    sample_positions = sorted({0, actual // 4, actual // 2, (actual * 3) // 4, actual - 1})
    sampled_ids: list[str] = []
    for pos in sample_positions:
        bot_id = store.discovery_order[pos]
        record = store.load_bot(bot_id)
        if not isinstance(record, dict):
            fail(f"Could not read archived bot {bot_id} from R2.")
        if str(record.get("id") or "").lower() != bot_id:
            fail(f"Archived bot ID mismatch for {bot_id}.")
        sampled_ids.append(bot_id)
    print(f"  verified {len(sampled_ids)} archived bot objects", flush=True)

    print("Smoke 4/6: checking the public R2 custom domain...", flush=True)
    public_url = store.public_url(store.bot_key(sampled_ids[0]))
    if not public_url:
        fail("R2 public_base_url is not configured.")
    response = requests.get(public_url, timeout=20)
    if response.status_code != 200:
        fail(f"Public R2 bot URL returned HTTP {response.status_code}.")
    try:
        public_record = response.json()
    except Exception as exc:
        fail(f"Public R2 bot URL did not return JSON: {exc}")
    if str(public_record.get("id") or "").lower() != sampled_ids[0]:
        fail("Public R2 bot record did not match the requested bot.")

    print("Smoke 5/6: checking a temporary R2 write/read/delete...", flush=True)
    smoke_key = store.key(store.meta_prefix, "smoke", f"{uuid.uuid4().hex}.txt")
    smoke_body = b"spicychat-archive-r2-smoke\n"
    try:
        store.s3.put_object(
            Bucket=store.bucket,
            Key=smoke_key,
            Body=smoke_body,
            ContentType="text/plain; charset=utf-8",
            CacheControl="no-store",
        )
        obj = store.s3.get_object(Bucket=store.bucket, Key=smoke_key)
        body = obj["Body"].read()
        if body != smoke_body:
            fail("Temporary R2 smoke object read back with different contents.")
    finally:
        try:
            store.s3.delete_object(Bucket=store.bucket, Key=smoke_key)
        except Exception:
            pass

    print("Smoke 6/6: checking Typesense + public character API reachability...", flush=True)
    listings = config.get("listings") or {}
    sort_by = next(iter(listings.values()), "createdAt:desc")
    client = legacy.Client(config)
    request = legacy.typesense_search(config, page=1, per_page=1, sort_by=sort_by)
    search = client.multi_search([request])
    if not search.ok:
        fail(f"Typesense smoke request failed: HTTP {search.status} {search.error or ''}".strip())
    result = (search.data.get("results") or [{}])[0]
    hits, _found = legacy.extract_hits(result)
    if not hits:
        fail("Typesense smoke request returned no public characters.")
    live_id = legacy.normalize_id(hits[0])
    if not live_id:
        fail("Typesense smoke result had no character ID.")

    character = client.character(live_id)
    # The purpose here is reachability, not deletion adjudication. A 4xx can be a
    # transient visibility/rate-limit race; network failure or 5xx is not healthy.
    if character.status == 0 or character.status >= 500:
        fail(f"Character API smoke request failed with HTTP {character.status}: {character.error or ''}")

    summary = (
        "# R2 smoke test\n\n"
        f"- Migration marker: **ok** ({expected:,} bot records)\n"
        f"- Discovery order: **ok** ({actual:,} IDs)\n"
        f"- Archived bot S3 reads: **ok** ({len(sampled_ids)} sampled)\n"
        f"- Public custom-domain bot read: **HTTP {response.status_code}**\n"
        f"- Temporary R2 write/read/delete: **ok**\n"
        f"- Typesense: **ok**\n"
        f"- Character API reachability: **HTTP {character.status}**\n"
    )
    print(summary, flush=True)
    if args.summary_file:
        Path(args.summary_file).write_text(summary, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
