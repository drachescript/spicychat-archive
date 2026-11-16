#!/usr/bin/env python3
"""Recover a missed Mon/Wed/Fri deep scan without weakening R2 safeguards.

This watchdog reads already published Git stats + quota snapshots and GitHub
workflow status. It never scans Typesense or writes R2 itself. Missing schedules,
queued crawlers, cooldowns, API errors and stale quota snapshots all fail closed.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from manual_discovery_cooldown import check_cooldown, parse_time

ROOT = Path(__file__).resolve().parents[1]
STATS = ROOT / "data" / "stats.json"
USAGE = ROOT / "data" / "r2-usage.json"
WORKFLOW = "archive.yml"
REPO = "drachescript/spicychat-archive"
RECOVERY_TITLE = "Missed deep sweep recovery"
ACTIVE = {"queued", "pending", "waiting", "requested", "in_progress"}


def latest_slot(now: datetime) -> datetime:
    """Latest due M/W/F 02:17 UTC full-sweep slot."""
    now = now.astimezone(timezone.utc)
    for days in range(8):
        candidate = (now - timedelta(days=days)).replace(
            hour=2, minute=17, second=0, microsecond=0
        )
        if candidate.weekday() in {0, 2, 4} and candidate <= now:
            return candidate
    raise RuntimeError("No scheduled deep sweep slot in the last eight days")


def recent_scan_after_slot(stats: dict[str, Any], slot: datetime) -> bool:
    for run in stats.get("runs") or []:
        if not isinstance(run, dict) or run.get("kind") != "archive-run":
            continue
        explore = run.get("exploration") or {}
        try:
            pages = int(explore.get("pageBudget") or 0)
            completed = int(explore.get("pagesCompleted") or 0)
        except (TypeError, ValueError):
            continue
        at = parse_time(run.get("at"))
        if at and at >= slot and pages >= 1000 and completed > 0:
            return True
    return False


def quota_safe(usage: dict[str, Any], now: datetime) -> bool:
    checked = parse_time(usage.get("checkedAt"))
    if not checked or not timedelta(0) <= now - checked <= timedelta(hours=24):
        return False
    if usage.get("selectedMode") != "r2" or not usage.get("r2ReadAllowed"):
        return False
    values = usage.get("usage") or {}
    limits = usage.get("limits") or {}
    try:
        # Proactive margin; actual archive run still applies the authoritative
        # R2 quota guard with fresh Cloudflare readings before accessing R2.
        return (
            int(values["classA"]) < min(900000, int(limits["classAPause"]))
            and int(values["classB"]) < int(limits["classBPause"])
            and int(values["storageBytes"]) < int(limits["storageResumeBytes"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def decide(stats: dict[str, Any], usage: dict[str, Any],
           runs: list[dict[str, Any]], now: datetime) -> tuple[bool, str]:
    now = now.astimezone(timezone.utc)
    slot = latest_slot(now)
    if now < slot + timedelta(hours=8):
        return False, "Scheduled full sweep still has an eight-hour completion window."
    if recent_scan_after_slot(stats, slot):
        return False, "A deep sweep already ran after the scheduled slot."
    cooldown = check_cooldown(2500, stats, now)
    if not cooldown["allowed"]:
        return False, "Deep-sweep 24-hour cooldown is still active."
    if not quota_safe(usage, now):
        return False, "Missing/stale R2 readings or proactive quota limits prevent recovery."

    for run in runs:
        if not isinstance(run, dict):
            continue
        created = parse_time(run.get("created_at"))
        if not created or created < slot:
            continue
        title = str(run.get("display_title") or "")
        status = str(run.get("status") or "").lower()
        if title.startswith(RECOVERY_TITLE):
            return False, "Recovery already attempted for this scheduled slot."
        # GitHub Actions serializes archive writers but do not add redundant
        # recovery while an archive workflow is queued/running.
        if status in ACTIVE:
            return False, "An archive workflow is active or queued."
    return True, "Scheduled deep sweep missing; cooldown, R2 quota and writer checks passed."


def github_api(path: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    method = "POST" if payload is not None else "GET"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        "https://api.github.com" + path,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "spicychat-archive-missed-sweep-watchdog",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if payload is not None:
            return {"status": response.status}
        return json.load(response)


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or repo != REPO:
        print("Watchdog fail-closed: expected archive repo and Actions token are required.")
        return 1
    now = datetime.now(timezone.utc)
    try:
        stats = json.loads(STATS.read_text(encoding="utf-8"))
        usage = json.loads(USAGE.read_text(encoding="utf-8"))
        response = github_api(
            f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?per_page=100", token
        )
        if not isinstance(response.get("workflow_runs"), list):
            raise RuntimeError("Missing GitHub workflow run history")
        allowed, reason = decide(stats, usage, response["workflow_runs"], now)
        print("Deep-sweep watchdog: " + reason, flush=True)
        if not allowed:
            return 0
        # A named mode distinguishes this attempt from manual scans. The
        # target workflow preflight checks the *same* 24-hour cooldown again.
        result = github_api(
            f"/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
            token,
            payload={"ref": "main", "inputs": {"run_mode": "recover-catalog"}},
        )
        if result["status"] not in {200, 201, 202, 204}:
            raise RuntimeError(f"Unexpected workflow dispatch status {result['status']}")
        print("Deep-sweep watchdog: queued ONE 2,500-page recovery via archive.yml.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, urllib.error.HTTPError) as exc:
        print(f"Deep-sweep watchdog failed closed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
