#!/usr/bin/env python3
"""Allow scheduled archive runs, but pace optional large manual discovery sweeps.

The public stats log stores completed deep sweeps, so this requires no extra R2
requests or mutable clock file. A blocked request exits successfully with a
clear GitHub Actions summary and never starts a crawler.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATS = ROOT / "data" / "stats.json"

# Large manual sweeps are deliberately exceptional, not a new scheduled cadence.
# Enforce the stricter cooldown of the incoming and previous sweep.
COOLDOWN_HOURS = {1500: 12, 2500: 24}
VALID_PAGES = {50, 100, 250, 500, 750, 1000, 1500, 2500}


def tier_cooldown_hours(pages: int) -> int:
    if pages >= 2500:
        return COOLDOWN_HOURS[2500]
    if pages >= 1500:
        return COOLDOWN_HOURS[1500]
    return 0


def parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def last_manual_sweep(stats: dict[str, Any]) -> tuple[datetime, int] | None:
    # Since scheduled maintenance skips deep discovery, a positive pageBudget
    # identifies manual sweeps in historical archive-run stats.
    values = []
    for row in stats.get("runs") or []:
        if not isinstance(row, dict) or row.get("kind") != "archive-run":
            continue
        explore = row.get("exploration")
        if not isinstance(explore, dict):
            continue
        try:
            pages = int(explore.get("pageBudget") or 0)
        except (TypeError, ValueError):
            continue
        at = parse_time(row.get("at"))
        if at and pages > 0:
            values.append((at, pages))
    return max(values, key=lambda item: item[0]) if values else None


def check_cooldown(
    pages: int, stats: dict[str, Any], now: datetime
) -> dict[str, Any]:
    if pages not in VALID_PAGES:
        raise ValueError(f"Invalid discovery target: {pages}")
    prior = last_manual_sweep(stats)
    if prior is None:
        return {"allowed": True, "hours": tier_cooldown_hours(pages), "previous": None, "nextAt": None}
    last_at, last_pages = prior
    hours = max(tier_cooldown_hours(pages), tier_cooldown_hours(last_pages))
    eligible_at = last_at + timedelta(hours=hours)
    allowed = hours == 0 or now >= eligible_at
    return {
        "allowed": allowed,
        "hours": hours,
        "previous": last_at.isoformat().replace("+00:00", "Z"),
        "previousPages": last_pages,
        "nextAt": eligible_at.isoformat().replace("+00:00", "Z") if not allowed else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=0,
                        help="Positive manual page choice; zero for scheduled/import-only")
    parser.add_argument("--stats", type=Path, default=STATS)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.pages == 0:
        result = {"allowed": True, "hours": 0, "previous": None, "nextAt": None}
    else:
        stats = json.loads(args.stats.read_text(encoding="utf-8"))
        if not isinstance(stats, dict) or not isinstance(stats.get("runs"), list):
            raise RuntimeError("Cannot verify manual sweep cooldown without a valid archive run history")
        result = check_cooldown(args.pages, stats, now)

    permitted = "true" if result["allowed"] else "false"
    message = (
        f"Manual deep sweep: {args.pages:,} pages allowed."
        if result["allowed"] else
        f"Manual deep sweep skipped safely: {args.pages:,} pages would violate "
        f"the {result['hours']}h cooldown since the {result['previousPages']:,}-page sweep "
        f"at {result['previous']}. Try again after {result['nextAt']}."
    )
    print(message, flush=True)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"allowed={permitted}\n")
            if result["nextAt"]:
                output.write(f"next_at={result['nextAt']}\n")
    with Path(os.environ.get("GITHUB_STEP_SUMMARY") or os.devnull).open("a", encoding="utf-8") as summary:
        summary.write(f"### Manual discovery cooldown\n\n{message}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
