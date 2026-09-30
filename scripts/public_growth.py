#!/usr/bin/env python3
"""Publish real SpicyChat public-index growth separately from archive backfill growth.

The archive's own bot count can jump by hundreds of thousands while a historical
backfill is running. That is useful crawler progress, but it is not SpicyChat's
public catalog growth. This script derives net public-index movement only from the
`publicIndexBots` samples already stored in stats history and publishes a tiny
stable file for the website/Discord integration.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATS_PATH = DATA / "stats.json"
FEED_PATH = DATA / "archive-feed.json"
OUT_PATH = DATA / "public-growth.json"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")


def epoch(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def public_samples(stats: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in stats.get("runs") or []:
        if not isinstance(row, dict) or row.get("kind") != "archive-run":
            continue
        when = epoch(row.get("at"))
        try:
            count = int(row.get("publicIndexBots"))
        except (TypeError, ValueError):
            continue
        if when is None or count <= 0:
            continue
        rows.append({"at": row.get("at"), "epoch": when, "count": count})
    rows.sort(key=lambda row: row["epoch"])
    return rows


def period(rows: list[dict[str, Any]], hours: int) -> dict[str, Any]:
    if len(rows) < 2:
        return {
            "hours": hours,
            "net": None,
            "complete": False,
            "spanHours": 0.0,
            "baselineAt": None,
        }

    latest = rows[-1]
    cutoff = latest["epoch"] - hours * 3600
    before = [row for row in rows[:-1] if row["epoch"] <= cutoff]
    baseline = before[-1] if before else rows[0]
    span_hours = max(0.0, (latest["epoch"] - baseline["epoch"]) / 3600)
    return {
        "hours": hours,
        "net": int(latest["count"] - baseline["count"]),
        "complete": bool(baseline["epoch"] <= cutoff),
        "spanHours": round(span_hours, 2),
        "baselineAt": baseline["at"],
    }


def build_public_growth(stats: dict[str, Any]) -> dict[str, Any]:
    rows = public_samples(stats)
    generated_at = stats.get("generatedAt")
    if not rows:
        return {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "sampleCount": 0,
            "latestPublicIndexBots": int(stats.get("publicIndexBots") or 0),
            "firstSampleAt": None,
            "lastSampleAt": None,
            "observedHours": 0.0,
            "observedNet": 0,
            "averagePerDay": 0.0,
            "periods": {
                "24h": period([], 24),
                "7d": period([], 24 * 7),
                "30d": period([], 24 * 30),
            },
        }

    first, latest = rows[0], rows[-1]
    observed_hours = max(0.0, (latest["epoch"] - first["epoch"]) / 3600)
    observed_net = int(latest["count"] - first["count"])
    periods = {
        "24h": period(rows, 24),
        "7d": period(rows, 24 * 7),
        "30d": period(rows, 24 * 30),
    }

    pace_window = periods["24h"] if periods["24h"]["complete"] else None
    if pace_window and pace_window["spanHours"] > 0:
        pace = pace_window["net"] / (pace_window["spanHours"] / 24)
    elif observed_hours > 0:
        pace = observed_net / (observed_hours / 24)
    else:
        pace = 0.0

    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "sampleCount": len(rows),
        "latestPublicIndexBots": int(latest["count"]),
        "firstSampleAt": first["at"],
        "lastSampleAt": latest["at"],
        "observedHours": round(observed_hours, 2),
        "observedNet": observed_net,
        "averagePerDay": round(pace, 1),
        "periods": periods,
    }


def main() -> int:
    stats = read_json(STATS_PATH, {})
    if not isinstance(stats, dict):
        print("Public growth: stats.json is unavailable or invalid.")
        return 0

    public_growth = build_public_growth(stats)
    write_json(OUT_PATH, public_growth)

    # Keep archive/backfill growth available for crawler diagnostics, but expose
    # the public-index series explicitly so the website never confuses the two.
    stats["publicGrowth"] = public_growth
    write_json(STATS_PATH, stats)

    feed = read_json(FEED_PATH, {})
    if isinstance(feed, dict) and feed:
        feed["publicGrowth"] = public_growth
        write_json(FEED_PATH, feed)

    p24 = public_growth.get("periods", {}).get("24h", {})
    print(
        "Public growth: "
        f"{public_growth.get('latestPublicIndexBots', 0):,} live; "
        f"{int(p24.get('net') or 0):+,} net over {p24.get('spanHours', 0):.2f}h; "
        f"{public_growth.get('averagePerDay', 0):+,.1f}/day normalized."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
