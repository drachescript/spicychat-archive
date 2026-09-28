#!/usr/bin/env python3
"""Run the current R2 crawler with conservative serial-read safety caps.

The R2 crawler currently reads existing per-bot objects serially while ingesting
listing/exploration results. Until that reader is converted to bulk/concurrent
GETs, cap the amount of work per 3-hour run so GitHub Actions cannot spend two
hours in a single scan.

This does not alter deletion confirmation rules or the archived data model.
"""
from __future__ import annotations

import os

import archive as legacy


def _positive_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default)).strip()))
    except (TypeError, ValueError):
        return default


listing_cap = _positive_int("SPICYCHAT_ARCHIVE_LISTING_MAX_HITS", 500)
explore_cap = _positive_int("SPICYCHAT_ARCHIVE_EXPLORE_PAGE_CAP", 2)

original_load_config = legacy.load_config


def capped_load_config():
    config = original_load_config()
    crawler = config.setdefault("crawler", {})
    configured = max(0, int(crawler.get("listing_max_hits") or listing_cap))
    crawler["listing_max_hits"] = min(configured, listing_cap)
    return config


legacy.load_config = capped_load_config

# archive_cloud.py already understands SPICYCHAT_ARCHIVE_EXPLORE_PAGES. Preserve
# the quota guard's lower value, but cap a healthy/default run until bulk reads
# are implemented.
guard_pages = _positive_int(
    "SPICYCHAT_ARCHIVE_EXPLORE_PAGES",
    explore_cap,
)
os.environ["SPICYCHAT_ARCHIVE_EXPLORE_PAGES"] = str(min(guard_pages, explore_cap))

print(
    "R2 safe scan caps: "
    f"listing_max_hits={listing_cap}, "
    f"explore_pages={os.environ['SPICYCHAT_ARCHIVE_EXPLORE_PAGES']}",
    flush=True,
)

import archive_cloud  # noqa: E402  (import after monkeypatch is intentional)


if __name__ == "__main__":
    raise SystemExit(archive_cloud.run())
