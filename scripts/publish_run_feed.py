#!/usr/bin/env python3
"""Publish a tiny stable Archive status/run feed for the website and Discord bot."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATS_PATH = DATA / "stats.json"
MANIFEST_PATH = DATA / "manifest.json"
CONFIG_PATH = ROOT / "config.json"
RUN_OUTCOME_PATH = DATA / "last-exploration-status.json"
OUT_PATH = DATA / "archive-feed.json"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def exploration_errors(exploration: dict[str, Any]) -> list[str]:
    raw = exploration.get("errors") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def classify_run_status(raw_status: str, exploration: dict[str, Any]) -> str:
    """Separate a saved partial crawl from a hard workflow failure."""
    workflow_status = str(raw_status or "success").strip().lower()
    if workflow_status != "success":
        return "failure"
    if (
        bool(exploration.get("partial"))
        or bool(exploration.get("timeLimited"))
        or exploration_errors(exploration)
    ):
        return "partial"
    return "success"


def stop_reason(exploration: dict[str, Any]) -> str | None:
    errors = exploration_errors(exploration)
    if errors:
        return errors[0]
    if bool(exploration.get("timeLimited")):
        return "Discovery time limit reached before the page budget completed."
    if bool(exploration.get("partial")):
        return "Discovery stopped before the requested crawl completed."
    return None


def apply_current_outcome(
    latest: dict[str, Any],
    exploration: dict[str, Any],
    outcome: Any,
) -> dict[str, Any]:
    """Restore exact exploration details omitted from older compact stats."""
    merged = dict(exploration)
    if not isinstance(outcome, dict):
        return merged

    latest_at = str(latest.get("at") or "")
    outcome_at = str(outcome.get("at") or "")
    if not latest_at or latest_at != outcome_at:
        return merged

    for key in (
        "pageBudget",
        "pagesCompleted",
        "timeLimited",
        "errors",
        "partial",
        "naturalEnd",
        "resultCapReached",
        "switchedToCursor",
    ):
        if key in outcome:
            merged[key] = outcome[key]
    return merged


def exploration_is_successful(exploration: Any) -> bool:
    """Return True only when history proves the exploration ended cleanly."""
    if not isinstance(exploration, dict) or not exploration:
        return False
    if (
        exploration_errors(exploration)
        or bool(exploration.get("timeLimited"))
        or bool(exploration.get("partial"))
    ):
        return False

    budget = int(exploration.get("pageBudget") or 0)
    completed = int(exploration.get("pagesCompleted") or 0)
    if budget > 0 and completed < budget:
        # Older rows did not persist stop reasons. Do not call an ambiguous
        # incomplete historical run successful unless it explicitly says the
        # Typesense pass naturally ran out of hits.
        return bool(
            exploration.get("naturalEnd")
            or exploration.get("switchedToCursor")
        )
    return budget > 0 and completed >= budget


def successful_run_summary(row: dict[str, Any]) -> dict[str, Any]:
    exploration = row.get("exploration") or {}
    return {
        "finishedAt": row.get("at"),
        "durationSeconds": int(row.get("runDurationSeconds") or 0),
        "addedBots": int(row.get("addedSincePrevious") or 0),
        "pagesBudget": int(exploration.get("pageBudget") or 0),
        "pagesCompleted": int(exploration.get("pagesCompleted") or 0),
    }


def last_successful_run(
    runs: list[dict[str, Any]],
    current_status: str,
) -> dict[str, Any] | None:
    if current_status == "success" or not runs:
        return None

    candidates = runs if current_status == "failure" else runs[:-1]
    for row in reversed(candidates):
        if row.get("kind") != "archive-run":
            continue
        if exploration_is_successful(row.get("exploration")):
            return successful_run_summary(row)
    return None


def main() -> int:
    stats = read_json(STATS_PATH, {})
    manifest = read_json(MANIFEST_PATH, {})
    config = read_json(CONFIG_PATH, {})
    outcome = read_json(RUN_OUTCOME_PATH, {})
    runs = [row for row in (stats.get("runs") or []) if isinstance(row, dict)]
    latest = runs[-1] if runs else {}
    exploration = apply_current_outcome(
        latest,
        latest.get("exploration") or {},
        outcome,
    )
    listings = latest.get("listings") or {}
    enrichment = latest.get("enrichment") or {}
    images = latest.get("images") or {}
    storage = latest.get("storage") or manifest.get("storage") or {}

    total = int(stats.get("totalBots") or manifest.get("totalBots") or latest.get("totalBots") or 0)
    added = int(stats.get("latestAdded") or latest.get("addedSincePrevious") or 0)
    crawler = config.get("crawler") or {}
    scheduled_pages = int(crawler.get("explore_pages_per_run") or 0)

    # r2_archive_optimized runs inside a one-off manual config override, so its
    # public stats can otherwise make a 750/1000-page test look like the new
    # scheduled default. Normalize those public labels from checked-in config.
    adaptive = stats.setdefault("adaptiveDiscovery", {})
    adaptive["basePages"] = scheduled_pages
    adaptive["maxPages"] = int(crawler.get("explore_pages_max") or scheduled_pages)
    adaptive["growthPerSuccess"] = int(crawler.get("explore_pages_growth_per_success", 0))
    adaptive["timeLimitSeconds"] = int(crawler.get("explore_time_limit_seconds") or 3600)
    adaptive["nextPageBudget"] = scheduled_pages
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    requested_raw = os.environ.get("SPICYCHAT_ARCHIVE_MANUAL_PAGES", "").strip()
    requested = int_or_none(requested_raw) if requested_raw else scheduled_pages
    raw_run_status = os.environ.get("ARCHIVE_RUN_STATUS", "success")
    run_status = classify_run_status(raw_run_status, exploration)
    run_at = latest.get("at") or stats.get("generatedAt") or utc_now()
    event_at = utc_now()
    github_run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    github_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "").strip()
    run_id = (
        f"github:{github_run_id}:{github_attempt or '1'}"
        if github_run_id
        else f"archive-run:{run_at}"
    )

    total_duration = int(latest.get("runDurationSeconds") or 0)
    discovery_duration = int(exploration.get("durationSeconds") or 0)
    non_discovery_duration = max(0, total_duration - discovery_duration)
    discovery_share = round((discovery_duration / total_duration) * 100, 1) if total_duration else 0.0
    errors = exploration_errors(exploration)
    reason = stop_reason(exploration)

    if run_status == "failure":
        run_payload = {
            "finishedAt": event_at,
            "pagesRequested": requested,
            "scheduledPages": scheduled_pages,
            "workflowRunUrl": os.environ.get("ARCHIVE_RUN_URL", "").strip() or None,
            "error": "Archive crawler step failed before a new run summary was published.",
        }
    else:
        run_payload = {
            "finishedAt": run_at,
            "durationSeconds": total_duration,
            "addedBots": added,
            "deletedConfirmed": int(latest.get("deletedConfirmed") or 0),
            "pagesRequested": requested,
            "scheduledPages": scheduled_pages,
            "pagesBudget": int(exploration.get("pageBudget") or 0),
            "pagesCompleted": int(exploration.get("pagesCompleted") or 0),
            "hits": int(exploration.get("hits") or 0),
            # Keep the two discovery surfaces separate. "addedBots" is the
            # authoritative total delta for the batch; newest-first listing
            # discovery and the deep exploration sweep can each contribute.
            "newFromListings": sum(
                int((info or {}).get("new") or 0)
                for info in listings.values()
                if isinstance(info, dict)
            ),
            "newFromDiscovery": int(exploration.get("new") or 0),
            "changedIngestRecords": int(exploration.get("changed") or 0),
            "discoveryDurationSeconds": discovery_duration,
            "nonDiscoveryDurationSeconds": non_discovery_duration,
            "discoverySharePercent": discovery_share,
            "timeLimited": bool(exploration.get("timeLimited")),
            "naturalEnd": bool(exploration.get("naturalEnd")),
            "resultCapReached": bool(exploration.get("resultCapReached")),
            "switchedToCursor": bool(exploration.get("switchedToCursor")),
            "errors": errors,
            "stopReason": reason,
            "enrichmentAttempted": int(enrichment.get("attempted") or 0),
            "enriched": int(enrichment.get("enriched") or 0),
            "imagesAttempted": int(images.get("attempted") or 0),
            "imagesSaved": int(images.get("saved") or 0),
            "workflowRunUrl": os.environ.get("ARCHIVE_RUN_URL", "").strip() or None,
        }
        run_payload["newOther"] = max(
            0,
            int(run_payload["addedBots"])
            - int(run_payload["newFromListings"])
            - int(run_payload["newFromDiscovery"]),
        )

    payload = {
        "schemaVersion": 1,
        "generatedAt": event_at,
        "runId": run_id,
        "status": run_status,
        "archive": {
            "totalBots": total,
            "deletedBots": int(stats.get("deletedBots") or manifest.get("deletedBots") or latest.get("deletedBots") or 0),
            "publicIndexBots": int(stats.get("publicIndexBots") or manifest.get("activeBots") or latest.get("publicIndexBots") or 0),
            "startedAt": stats.get("startedAt"),
        },
        "run": run_payload,
        "lastSuccessfulRun": last_successful_run(runs, run_status),
        "growth": stats.get("growth") or {},
        "storage": {
            "usedBytes": int(storage.get("usedBytes") or 0),
            "mediaBytes": int(storage.get("mediaBytes") or 0),
            "objects": int(storage.get("objects") or 0),
            "writesThisMonth": int(storage.get("writesThisMonth") or 0),
        },
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if OUT_PATH.exists() and OUT_PATH.read_text(encoding="utf-8") == text:
        print("Archive feed unchanged.")
        return 0
    OUT_PATH.write_text(text, encoding="utf-8")

    if run_status in {"success", "partial"}:
        suffix = (
            f" Partial stop: {reason}"
            if run_status == "partial" and reason
            else ""
        )
        print(
            f"Archive feed: status={run_status}; {total:,} total; "
            f"+{added:,}; {run_payload['pagesCompleted']:,}/{run_payload['pagesBudget']:,} pages; "
            f"{run_payload['durationSeconds']:,}s total.{suffix}"
        )
    else:
        print(
            f"Archive feed: status=failure; {total:,} total; failure event published."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
