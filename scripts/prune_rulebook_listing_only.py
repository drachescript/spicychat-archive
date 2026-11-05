#!/usr/bin/env python3
"""One-time, fail-closed cleanup of R2 Rulebook records with ONLY listing metadata.

Only touches the separate rulebooks/<first2>/<uuid>.json namespace. Never
touches bot data, Lorebooks, media, index/state objects, or objects containing
shared rule definitions or any unknown fields. A full, complete public catalog
must exist before an apply run. No full-bucket inventory.
"""
from __future__ import annotations

import argparse
import re
from typing import Any

import archive
from storage_r2 import R2ArchiveStore
from rulebook_archive import (PUBLIC_FIELDS, RULEBOOK_ID, has_saved_public_rule_content,
                              index_key, rulebook_key)

KEY_RE = re.compile(
    r"^rulebooks/([a-f0-9]{2})/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\.json$"
)
RECORD_FIELDS = {
    "schemaVersion", "id", "firstSeenAt", "lastObservedAt", "versions",
    "statusHistory", "listing", "fingerprint", "status",
}
VERSION_FIELDS = {"at", "metadata"}
STATUS_FIELDS = {"at", "status"}


def listing_only_record(record: Any, key: str) -> bool:
    """Approve ONLY the exact public-listing record schema our old crawler wrote."""
    if not isinstance(record, dict) or set(record) - RECORD_FIELDS:
        return False
    match = KEY_RE.fullmatch(key)
    if not match:
        return False
    identifier = match.group(2)
    if match.group(1) != identifier.replace("-", "")[:2]:
        return False
    if record.get("schemaVersion") != 1 or record.get("id") != identifier:
        return False
    if has_saved_public_rule_content(record):
        return False
    listing = record.get("listing")
    if not isinstance(listing, dict) or not set(listing) <= set(PUBLIC_FIELDS):
        return False
    versions = record.get("versions")
    if not isinstance(versions, list):
        return False
    for version in versions:
        if not isinstance(version, dict) or set(version) - VERSION_FIELDS:
            return False
        metadata = version.get("metadata")
        if not isinstance(metadata, dict) or not set(metadata) <= set(PUBLIC_FIELDS):
            return False
    history = record.get("statusHistory")
    if not isinstance(history, list) or any(
        not isinstance(x, dict) or set(x) - STATUS_FIELDS for x in history
    ):
        return False
    status = record.get("status")
    if not isinstance(status, dict) or set(status) != {"current"}:
        return False
    return status["current"] in {"public", "not-public"}


def candidates(store: R2ArchiveStore, max_objects: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    token = None
    while True:
        args: dict[str, Any] = {
            "Bucket": store.bucket, "Prefix": store.key("rulebooks") + "/",
            "MaxKeys": 1000,
        }
        if token:
            args["ContinuationToken"] = token
        page = store.s3.list_objects_v2(**args)
        results.extend(page.get("Contents") or [])
        if len(results) > max_objects:
            raise RuntimeError(f"Rulebook objects exceed safety cap {max_objects}; no changes made")
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
        if not token:
            raise RuntimeError("Incomplete Rulebook object listing; no changes made")
    return results


def prune(store: R2ArchiveStore, *, apply: bool = False,
          max_objects: int = 800) -> dict[str, Any]:
    catalog = store.get_json(index_key(store), None)
    if not isinstance(catalog, dict) or catalog.get("lastScanComplete") is not True:
        raise RuntimeError("Require complete published Rulebook catalog before cleanup")
    if int(catalog.get("catalogNow") or 0) < 1:
        raise RuntimeError("Catalog-only index migration has not completed")
    rows = catalog.get("rulebooks")
    if not isinstance(rows, list):
        raise RuntimeError("Invalid public Rulebook catalog")
    protected_ids = {
        x.get("id") for x in rows if isinstance(x, dict) and x.get("contentSaved") is True
    }
    objects = candidates(store, max_objects)
    deletable: list[tuple[str, int]] = []
    protected: list[str] = []
    for item in objects:
        key = str(item.get("Key") or "")
        match = KEY_RE.fullmatch(key)
        if not match or match.group(2) in protected_ids:
            protected.append(key)
            continue
        record = store.get_json(key, None)
        if listing_only_record(record, key):
            deletable.append((key, int(item.get("Size") or 0)))
        else:
            protected.append(key)
    # Check every object before any DELETE. Fail closed on unexpected format.
    if protected:
        print(f"Rulebooks: preserving {len(protected)} object(s) with unverified or meaningful data")
    removed = 0
    if apply:
        for key, _ in deletable:
            if store.delete_bytes(key):
                removed += 1
        store.flush_usage(force=True)
    summary = {
        "dryRun": not apply, "candidates": len(objects),
        "listingOnly": len(deletable), "protected": len(protected),
        "removed": removed, "bytesEstimated": sum(sz for _, sz in deletable),
    }
    print(f"Rulebook listing-only R2 cleanup: {summary}", flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Delete verified metadata-only objects")
    parser.add_argument("--max-objects", type=int, default=800)
    args = parser.parse_args()
    if args.max_objects < 1 or args.max_objects > 1000:
        parser.error("max-objects must be between 1 and 1000")
    if not R2ArchiveStore.credentials_present():
        raise RuntimeError("R2 credentials required")
    store = R2ArchiveStore(archive.load_config())
    if not store.is_migrated():
        raise RuntimeError("Archive R2 migration must be complete")
    prune(store, apply=args.apply, max_objects=args.max_objects)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
