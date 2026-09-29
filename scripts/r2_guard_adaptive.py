#!/usr/bin/env python3
"""Run the normal R2 quota guard, then expose an adaptive discovery ceiling.

The existing guard deliberately caps its explore_pages output at
crawler.explore_pages_per_run. Adaptive discovery needs a larger *ceiling*
without weakening the quota checks, so this wrapper reuses the guard's decision
and raises the healthy ceiling up to crawler.explore_pages_max.

Discovery-only bots are stored in page shards, so the Class A estimate is based
on a small number of writes per discovery page plus one possible locator-index
write for each two-hex UUID prefix. It no longer assumes one R2 write per hit.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def parse_outputs(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--github-output")
    ap.add_argument("--status-file", default=str(ROOT / "data" / "r2-usage.json"))
    ap.add_argument("--migration-check", action="store_true")
    args = ap.parse_args()

    with tempfile.NamedTemporaryFile(prefix="r2-guard-", suffix=".out", delete=False) as f:
        temp_output = Path(f.name)

    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "r2_guard.py"),
        "--github-output", str(temp_output),
        "--status-file", str(args.status_file),
    ]
    if args.migration_check:
        cmd.append("--migration-check")

    try:
        rc = subprocess.run(cmd, cwd=ROOT).returncode
        values = parse_outputs(temp_output)
    finally:
        temp_output.unlink(missing_ok=True)

    if rc != 0:
        return rc

    config = read_json(ROOT / "config.json", {})
    crawler = config.get("crawler") or {}
    storage = config.get("storage") or {}
    r2 = storage.get("r2") or {}
    guard = storage.get("quota_guard") or {}
    status = read_json(Path(args.status_file), {})

    base_pages = max(0, int(crawler.get("explore_pages_per_run") or 20))
    max_pages = max(base_pages, int(crawler.get("explore_pages_max") or base_pages))
    writes_per_page = max(1, int(r2.get("discovery_shard_writes_per_page") or 3))
    locator_reserve = max(0, int(r2.get("discovery_locator_index_reserve") or 256))

    if values.get("mode") == "r2":
        usage = status.get("usage") or {}
        class_a = int(usage.get("classA") or 0)
        pause_a = int(guard.get("class_a_pause") or 700000)
        reserve_a = int(guard.get("class_a_reserve_per_run") or 10000)
        replay = int(values.get("replay_write_budget") or 0)

        writable_ops = max(0, pause_a - class_a - reserve_a - replay)
        page_ops = max(0, writable_ops - locator_reserve)
        quota_pages = max(0, page_ops // writes_per_page)
        values["explore_pages"] = str(min(max_pages, quota_pages))
    elif values.get("mode") == "legacy":
        values["explore_pages"] = str(base_pages)
    else:
        values["explore_pages"] = "0"

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            for key, value in values.items():
                f.write(f"{key}={str(value).replace(chr(10), ' ')}\n")

    print(
        "Adaptive discovery guard: "
        f"mode={values.get('mode')} "
        f"base={base_pages} max={max_pages} "
        f"shardWrites/page={writes_per_page} locatorReserve={locator_reserve} "
        f"ceiling={values.get('explore_pages', '0')} pages"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
