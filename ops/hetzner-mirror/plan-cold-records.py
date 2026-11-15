#!/usr/bin/env python3
"""Conservative, read-only cold-storage candidate report.

This NEVER deletes, uploads, changes routing, or marks backups verified.
It uses a fully populated Hetzner mirror, a private views CSV and an explicitly
provided telemetry start date to avoid treating unmeasured bots as unpopular.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ID = re.compile(r"^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$")


def age_days(path: Path, now: datetime) -> float:
    return (now.timestamp() - path.stat().st_mtime) / 86400


def load_views(csv_path: Path) -> dict[str, int]:
    views = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or ()) != {"bot_id", "views"}:
            raise ValueError("Expected private CSV with bot_id,views columns")
        for row in reader:
            bot_id = str(row["bot_id"] or "").lower()
            if not ID.fullmatch(bot_id):
                raise ValueError("Invalid bot ID in private views export")
            value = int(row["views"])
            if value < 0:
                raise ValueError("Negative view count")
            views[bot_id] = value
    return views


def rank_cold(mirror: Path, views: dict[str, int], *,
              min_age: int, max_views: int, now: datetime) -> list[dict]:
    candidates = []
    for path in mirror.glob("bots/*/*.json"):
        bot_id = path.stem.lower()
        if not ID.fullmatch(bot_id):
            continue
        # Prefix sanity protects against arbitrary local files.
        if path.parent.name != bot_id.replace("-", "")[:2]:
            continue
        try:
            days = age_days(path, now)
            bytes_used = path.stat().st_size
        except OSError:
            continue
        if days < min_age or views.get(bot_id, 0) > max_views:
            continue
        candidates.append({
            "id": bot_id, "relativePath": path.relative_to(mirror).as_posix(),
            "views30d": views.get(bot_id, 0),
            "mirrorBytes": bytes_used, "mirrorAgeDays": int(days),
        })
    candidates.sort(key=lambda row: (row["views30d"], -row["mirrorAgeDays"], row["id"]))
    return candidates


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", type=Path, required=True)
    ap.add_argument("--views-csv", type=Path, required=True)
    ap.add_argument("--tracking-started-at", required=True,
                    help="UTC date when the Worker first actually recorded views (YYYY-MM-DD)")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=2000)
    args = ap.parse_args(argv)
    try:
        policy = json.loads((Path(__file__).parent / "hybrid-policy.json").read_text(encoding="utf-8"))
        if policy["offloadEnabled"] or policy["deleteR2Objects"]:
            raise ValueError("Dry-run planner refuses an enabled offload policy")
        if args.limit < 1 or args.limit > 100000:
            raise ValueError("Invalid result limit")
        now = datetime.now(timezone.utc)
        started = datetime.strptime(args.tracking_started_at, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        min_days = int(policy["eligibilityDefaults"]["minimumCollectionDays"])
        if now - started < timedelta(days=min_days):
            raise ValueError(f"Telemetry is not mature: collect at least {min_days} days first")
        mirror = args.mirror.resolve()
        if not (mirror / "bots").is_dir():
            raise ValueError("No mirrored bots/ folder: do not infer missing files as cold")
        views = load_views(args.views_csv)
        eligible = rank_cold(
            mirror, views,
            min_age=int(policy["eligibilityDefaults"]["minimumRecordAgeDays"]),
            max_views=int(policy["eligibilityDefaults"]["maximumRecentViews"]),
            now=now,
        )
        report = {
            "schemaVersion": 1, "generatedAt": now.isoformat(),
            "mode": "report-only-no-deletes",
            "mirrorRoot": str(mirror),
            "trackingStartedAt": started.isoformat(),
            "eligibleRecordCount": len(eligible),
            "eligibleMirrorBytes": sum(x["mirrorBytes"] for x in eligible),
            "shown": min(args.limit, len(eligible)),
            "candidates": eligible[:args.limit],
            "warnings": [
                "Mirror local timestamps are not authoritative bot creation dates.",
                "No proof of current R2 presence, backup verification, or route availability.",
                "Zero recorded views is only a proxy for popularity, not proof nobody visited.",
                "Discovery shards, state, indexes, image URLs and media are never candidates here.",
                "No files may be moved or deleted based on this report alone."
            ],
        }
        repo = Path(__file__).resolve().parents[2]
        output = args.output.resolve()
        if output.is_relative_to(repo):
            raise ValueError("Write private reports outside the repository")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Read-only report: {len(eligible)} cold candidates, "
              f"{report['eligibleMirrorBytes']:,} mirrored bytes; {output}")
    except Exception as exc:
        print("Cold-storage planning stopped safely: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
