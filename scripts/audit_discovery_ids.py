#!/usr/bin/env python3
"""Read-only, complete per-ID reconciliation of discovery, search and R2 files.

Do NOT use the arithmetic difference between the public SpicyChat count and
historical observations to infer missing/deleted characters. This audit checks
the Archive's own recorded IDs only; it never requests live SpicyChat status,
deletes anything, or changes bot records.

R2 LIST is used to inventory actual bot JSON objects rather than HEAD-checking
one million IDs. A partial/truncated inventory is an error, never a result.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any, Iterable

import archive as legacy
from publish_name_trends import _read_shards
from storage_r2 import R2ArchiveStore

INDEX_SHARDS = 64
SCHEMA_VERSION = 2
MAX_LIST_PAGES = 2000
SAMPLE_SIZE = 30


def _set(ids: Iterable[str]) -> set[str]:
    return {str(value).strip().lower() for value in ids if str(value).strip()}


def compare_sets(discovered: Iterable[str], materialized: Iterable[str]) -> tuple[list[str], list[str], int]:
    """Legacy two-index comparison kept for old callers and tests."""
    observed = _set(discovered)
    indexed = _set(materialized)
    return sorted(observed - indexed), sorted(indexed - observed), len(observed & indexed)


def classify(
    *,
    observed_count: int, indexed_count: int, discovery_only: list[str],
    index_only: list[str], overlap: int, head_results: list[tuple[str, bool]],
    at: str, max_checks: int,
) -> dict[str, Any]:
    """Legacy bounded HEAD-result classifier; never treat untested as missing."""
    missing = [bot_id for bot_id, exists in head_results if not exists]
    exists = [bot_id for bot_id, present in head_results if present]
    untouched = len(discovery_only) - len(head_results)
    if untouched < 0:
        raise ValueError("More checked IDs than discovery-only candidates")
    return {
        "schemaVersion": 1,
        "generatedAt": at,
        "observedDiscoveryIds": observed_count,
        "materializedIndexIds": indexed_count,
        "presentInBothIndexes": overlap,
        "discoveryOnlyIds": len(discovery_only),
        "indexOnlyIds": len(index_only),
        "botFileHeadChecks": len(head_results),
        "discoveryOnlyWithStoredBotFile": len(exists),
        "discoveryOnlyWithNoBotFile": len(missing),
        "discoveryOnlyUnchecked": untouched,
        "allDiscoveryOnlyIdsChecked": untouched == 0,
        "headCheckLimit": max_checks,
        "discoveryOnlyWithoutBotFileIds": missing,
        "discoveryOnlyWithFileExamples": exists[:100],
        "materializedButNotDiscoveredExamples": index_only[:100],
        "doesNotVerifyPresentDaySpicyChat": True,
    }


def inventory_bot_file_ids(store: R2ArchiveStore, *, max_pages: int = MAX_LIST_PAGES) -> tuple[set[str], dict[str, int]]:
    """Enumerate all actual bot JSON keys without per-ID HEAD/GET requests.

    A ListObjects response is only evidence of object *existence*, not valid
    JSON content, preserved bot fields or current availability on SpicyChat.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    prefix = store.bot_prefix.rstrip("/") + "/"
    token: str | None = None
    ids: set[str] = set()
    pages = objects = irregular = duplicate = 0
    while True:
        if pages >= max_pages:
            raise RuntimeError(
                f"Bot-object listing exceeded {max_pages:,} pages; refusing to publish partial audit"
            )
        args: dict[str, Any] = {"Bucket": store.bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            args["ContinuationToken"] = token
        page = store.s3.list_objects_v2(**args)
        pages += 1
        for obj in page.get("Contents") or []:
            objects += 1
            key = str(obj.get("Key") or "")
            # Reject artifacts and unexpected paths rather than counting them as
            # materialized characters. Exact canonical bot keys are required.
            if not key.startswith(prefix) or not key.endswith(".json"):
                irregular += 1
                continue
            bot_id = key.rsplit("/", 1)[-1][:-5].strip().lower()
            if not bot_id or store.bot_key(bot_id) != key:
                irregular += 1
                continue
            if bot_id in ids:
                duplicate += 1
            ids.add(bot_id)
        if not page.get("IsTruncated"):
            break
        new_token = page.get("NextContinuationToken")
        if not new_token or new_token == token:
            raise RuntimeError("R2 object listing truncated without a progressing continuation token")
        token = str(new_token)
    return ids, {
        "r2ListPages": pages,
        "r2KeysVisitedUnderBotPrefix": objects,
        "ignoredNoncanonicalBotKeys": irregular,
        "duplicateCanonicalBotKeys": duplicate,
    }


def build_audit(
    discovered: Iterable[str], indexed: Iterable[str], stored: Iterable[str],
    *, at: str, listing: dict[str, int] | None = None,
    materialized_index_generated_at: str | None = None,
    archive_snapshot_at: str | None = None,
) -> dict[str, Any]:
    """Classify every ID across all three sources, with exact memberships."""
    observed, search, files = _set(discovered), _set(indexed), _set(stored)
    both = observed & search
    only_discovery = observed - search
    only_index = search - observed
    missing_file = observed - files
    missing_index = observed - search
    file_without_discovery = files - observed
    index_without_file = search - files

    def sample(values: set[str]) -> list[str]:
        # Sort only a small sample so reports do not contain huge ID lists.
        return sorted(values)[:SAMPLE_SIZE]

    report = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": at,
        "complete": True,
        "readOnly": True,
        "sources": {
            "discovery": "Saved discovery-order IDs plus pending crash-recovery journals",
            "searchIndex": "All 64 materialized text-search shards",
            "storedBotFiles": "Full Cloudflare R2 listing of canonical bots/<prefix>/<id>.json objects",
        },
        "observedDiscoveryIds": len(observed),
        "materializedIndexIds": len(search),
        "storedBotFileIds": len(files),
        "presentInAllThree": len(observed & search & files),
        "observedWithStoredFile": len(observed & files),
        "observedWithoutStoredFile": len(missing_file),
        "observedAndIndexedWithoutStoredFile": len(both - files),
        "observedWithoutIndexButWithStoredFile": len(only_discovery & files),
        "observedWithoutIndexAndWithoutStoredFile": len(only_discovery - files),
        "observedWithoutIndex": len(missing_index),
        "indexedWithoutDiscovery": len(only_index),
        "indexedWithoutStoredFile": len(index_without_file),
        "indexedWithoutDiscoveryButStored": len(only_index & files),
        "storedFileWithoutDiscovery": len(file_without_discovery),
        "storedFileWithoutIndex": len(files - search),
        "observedAndIndexed": len(both),
        "observedWithoutStoredFileExamples": sample(missing_file),
        "observedUnindexedButStoredExamples": sample(only_discovery & files),
        "indexedWithoutStoredFileExamples": sample(index_without_file),
        "storedFileWithoutDiscoveryExamples": sample(file_without_discovery),
        "materializedIndexGeneratedAt": materialized_index_generated_at,
        "discoveryManifestGeneratedAt": archive_snapshot_at,
        "listing": dict(listing or {}),
        "notLiveStatusVerification": True,
        "notAnExplanationOfPublicCountGap": True,
        "notes": [
            "Every ID was compared with the *complete* saved discovery, text-search and R2 object key sets.",
            "An observed ID without a bot JSON means this Archive lacks a saved bot record for it, not that the character is unavailable or deleted from SpicyChat.",
            "A bot JSON file being present does not establish that the file is complete or that the character is still public.",
            "An old public observation is not confirmation of present public availability.",
            "This audit does not resolve the historical-IDs versus SpicyChat live-total arithmetic difference.",
            "No character records, statuses or historical discovery IDs were modified or removed.",
            "Index and discovery snapshots can have different generation times; totals may briefly differ while other jobs publish.",
        ],
    }
    # These invariants prove each ID was counted in exactly one appropriate
    # bucket, rather than silently dropping or double-counting records.
    assert report["observedWithStoredFile"] + report["observedWithoutStoredFile"] == len(observed)
    assert report["presentInAllThree"] + report["observedAndIndexedWithoutStoredFile"] == len(both)
    assert report["observedWithoutIndexButWithStoredFile"] + report["observedWithoutIndexAndWithoutStoredFile"] == len(only_discovery)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Calculate results without publishing to R2")
    ap.add_argument("--max-list-pages", type=int, default=MAX_LIST_PAGES,
                    help="Fail if listing all R2 bot objects requires more than this many pages")
    args = ap.parse_args()
    if not R2ArchiveStore.credentials_present():
        raise RuntimeError("R2 credentials not present")
    store = R2ArchiveStore(legacy.load_config())
    if not store.is_migrated():
        raise RuntimeError("R2 migration incomplete")

    store.load_discovery_order()
    discovery = store.discovery_order
    index_ids: set[str] = set()
    for shard in _read_shards(store, "text-search", INDEX_SHARDS):
        index_ids.update(_set(shard))
    stored_ids, listing = inventory_bot_file_ids(store, max_pages=args.max_list_pages)
    manifest = legacy.read_json(legacy.SITE_DATA_DIR / "manifest.json", {}) or {}

    report = build_audit(
        discovery, index_ids, stored_ids,
        at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        listing=listing,
        materialized_index_generated_at=(store.get_json(store.key("indexes", "text-search.json"), {}) or {}).get("generatedAt"),
        archive_snapshot_at=manifest.get("generatedAt"),
    )
    print(
        f"Complete per-ID Archive inventory: {report['observedDiscoveryIds']:,} observed; "
        f"{report['storedBotFileIds']:,} stored JSON objects; "
        f"{report['materializedIndexIds']:,} indexed; "
        f"{report['observedWithoutStoredFile']:,} observed without a stored JSON file; "
        f"{report['observedWithoutIndexButWithStoredFile']:,} stored but not searchable; "
        f"{report['indexedWithoutStoredFile']:,} indexed without JSON; "
        f"{listing['r2ListPages']:,} R2 LIST pages. "
        "No live SpicyChat checks or deletion.",
        flush=True,
    )
    if not args.dry_run:
        store.put_json(store.key("indexes", "discovery-id-audit.json"), report, public=True)
        store.flush_usage(force=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
