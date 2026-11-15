#!/usr/bin/env python3
"""Export aggregate per-bot views from Cloudflare Analytics Engine.

Run on Hetzner, not on the public website. No IPs, cookies or user records.
Read-only API access; does not write to Cloudflare R2 or GitHub.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

ID_RE = re.compile(r"^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$")
DATASET = "sca_bot_views_v1"


def query_sql(days: int, limit: int, offset: int) -> str:
    if not 1 <= days <= 90 or not 1 <= limit <= 5000 or offset < 0:
        raise ValueError("Invalid query window, page size or offset")
    return (
        "SELECT index1 AS bot_id, SUM(_sample_interval) AS views "
        f"FROM {DATASET} WHERE timestamp >= NOW() - INTERVAL '{days}' DAY "
        "GROUP BY bot_id ORDER BY views DESC, bot_id ASC "
        f"LIMIT {limit} OFFSET {offset} FORMAT JSON"
    )


def validate_rows(body: object) -> list[tuple[str, int]]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("Unexpected Analytics Engine response; refusing incomplete export")
    results = []
    for row in body["data"]:
        if not isinstance(row, dict):
            raise ValueError("Malformed result row")
        bot_id = str(row.get("bot_id") or "").lower().strip()
        try:
            views = int(row.get("views"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Malformed view count") from exc
        if not ID_RE.fullmatch(bot_id) or views < 0:
            raise ValueError("Analytics result contains an invalid bot ID/count")
        results.append((bot_id, views))
    return results


def export_counts(account_id: str, token: str, days: int, limit: int,
                  max_pages: int, session=requests) -> dict[str, int]:
    if not re.fullmatch(r"[a-f0-9]{32}", account_id, flags=re.IGNORECASE):
        raise ValueError("Cloudflare account ID is invalid")
    if not token:
        raise ValueError("An Account Analytics Read API token is required")
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/analytics_engine/sql"
    all_counts: dict[str, int] = {}
    for page in range(max_pages):
        sql = query_sql(days, limit, page * limit)
        response = session.post(endpoint, headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "text/plain; charset=utf-8",
        }, data=sql.encode("utf-8"), timeout=60)
        response.raise_for_status()
        rows = validate_rows(response.json())
        for bot_id, views in rows:
            if bot_id in all_counts:
                raise ValueError("Duplicate bot ID across pages; aborting incomplete export")
            all_counts[bot_id] = views
        if len(rows) < limit:
            return all_counts
    raise ValueError("Reached page limit; increase --max-pages rather than publishing partial data")


def write_private_csv(path: Path, counts: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to write inside the checked-out repository.
    repo = Path(__file__).resolve().parents[2]
    if path.resolve().is_relative_to(repo):
        raise ValueError("Output must be outside the repository; metrics are private")
    with tempfile.NamedTemporaryFile(
        mode="w", newline="", encoding="utf-8", dir=path.parent,
        prefix=path.name + ".", suffix=".tmp", delete=False,
    ) as handle:
        temp = Path(handle.name)
        writer = csv.writer(handle)
        writer.writerow(["bot_id", "views"])
        for bot_id, views in sorted(counts.items()):
            writer.writerow([bot_id, views])
    temp.replace(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--page-size", type=int, default=2000)
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        result = export_counts(
            os.environ.get("CLOUDFLARE_R2_ACCOUNT_ID", ""),
            os.environ.get("CLOUDFLARE_ANALYTICS_READ_TOKEN", ""),
            args.days, args.page_size, args.max_pages,
        )
        write_private_csv(args.output, result)
    except Exception as exc:
        print("Popularity export failed safely: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps({
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "lookbackDays": args.days,
        "botsWithObservedViews": len(result),
        "totalEstimatedViews": sum(result.values()),
        "output": str(args.output),
        "note": "Zero missing from export does NOT imply zero traffic; verify collection coverage.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
