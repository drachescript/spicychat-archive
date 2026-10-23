#!/usr/bin/env python3
"""Mirror the last COMPLETE R2 file audit into the public site's tiny JSON.

One R2 GET, no bucket listing, no bot reads and no changes to Archive records.
The page can use this file even if a visitor cannot fetch the R2 report.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import archive as legacy
from storage_r2 import R2ArchiveStore

DESTINATION = legacy.SITE_DATA_DIR / "archive-coverage.json"
COUNTS = (
    "observedDiscoveryIds",
    "materializedIndexIds",
    "storedBotFileIds",
    "storedFileWithoutIndex",
    "indexedWithoutStoredFile",
)


def verified_summary(report: object) -> dict:
    if not isinstance(report, dict):
        raise ValueError("No complete file-audit object was returned")
    if report.get("schemaVersion") != 2 or report.get("complete") is not True or report.get("readOnly") is not True:
        raise ValueError("File audit is not a completed read-only v2 inventory")
    stamp = report.get("generatedAt")
    if not isinstance(stamp, str):
        raise ValueError("File audit is missing its date")
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if when.tzinfo is None or when > datetime.now(timezone.utc) + timedelta(days=1):
        raise ValueError("File audit has an invalid date")
    for key in COUNTS:
        if type(report.get(key)) is not int or report[key] < 0:
            raise ValueError(f"File audit has an invalid {key}")
    observed, indexed, stored = (report[key] for key in COUNTS[:3])
    files_not_in_search, search_without_files = (report[key] for key in COUNTS[3:])
    if (stored > observed or files_not_in_search > stored or search_without_files > indexed or
            indexed - search_without_files != stored - files_not_in_search):
        raise ValueError("File audit totals do not reconcile")
    extra = report.get("listing") or {}
    if not isinstance(extra, dict):
        raise ValueError("Invalid R2 listing summary")
    return {
        "schemaVersion": 2,
        "complete": True,
        "readOnly": True,
        "source": "published-r2-full-file-audit",
        "generatedAt": stamp,
        "materializedIndexGeneratedAt": report.get("materializedIndexGeneratedAt"),
        **{key: report[key] for key in COUNTS},
        "observedWithoutStoredFile": report.get("observedWithoutStoredFile"),
        "listing": {"r2ListPages": extra.get("r2ListPages")},
    }


def save_if_newer(summary: dict, destination: Path = DESTINATION) -> bool:
    try:
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if (isinstance(existing, dict) and
                datetime.fromisoformat(str(existing.get("generatedAt", "")).replace("Z", "+00:00")) >=
                datetime.fromisoformat(summary["generatedAt"].replace("Z", "+00:00"))):
            return False
    except (OSError, ValueError, TypeError):
        pass
    legacy.write_json_if_changed(destination, summary)
    return True


def main() -> int:
    try:
        if not R2ArchiveStore.credentials_present():
            print("Coverage summary: R2 credentials unavailable; keeping last published audit.", flush=True)
            return 0
        store = R2ArchiveStore(legacy.load_config())
        raw = store.get_json(store.key("indexes", "discovery-id-audit.json"), None)
        report = verified_summary(raw)
        changed = save_if_newer(report)
        print("Coverage summary: " + ("synced" if changed else "already current") +
              f" ({report['storedBotFileIds']:,} saved bot files, checked {report['generatedAt']}).",
              flush=True)
    except Exception as exc:
        # A reporting mirror must never abort the crawler or replace known-good
        # counts with a broken/partial file.
        print(f"Coverage summary: keeping previous verified snapshot ({exc}).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
