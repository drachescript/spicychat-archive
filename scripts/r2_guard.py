#!/usr/bin/env python3
"""Choose R2 vs Git fallback before each archive run.

The guard queries Cloudflare's GraphQL Analytics API for account-wide R2 usage.
It intentionally switches away from R2 well before the Standard free-tier limits.
When R2 is paused, archive_fallback.py stores compact observation journals in Git.
When usage is healthy again, replay_fallback.py migrates those observations back.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
DEFAULT_STATUS = ROOT / "data" / "r2-usage.json"
GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"

CLASS_A = {
    "listbuckets", "putbucket", "listobjects", "listobjectsv2", "putobject",
    "copyobject", "completemultipartupload", "createmultipartupload",
    "lifecyclestoragetiertransition", "listmultipartuploads", "uploadpart",
    "uploadpartcopy", "listparts", "putbucketencryption", "putbucketcors",
    "putbucketlifecycleconfiguration",
}
CLASS_B = {
    "headbucket", "headobject", "getobject", "usagesummary",
    "getbucketencryption", "getbucketlocation", "getbucketcors",
    "getbucketlifecycleconfiguration",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def month_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def normalized_action(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def classify(action: str) -> str:
    key = normalized_action(action)
    if key in CLASS_A:
        return "A"
    if key in CLASS_B:
        return "B"
    # Conservative fallback for action names that Cloudflare adds later.
    if key.startswith(("get", "head", "usage")):
        return "B"
    if key.startswith("delete") or key.startswith("abort"):
        return "FREE"
    return "A"


def query_usage(account_id: str, token: str) -> dict[str, Any]:
    query = r"""
    query R2ArchiveGuard($accountTag: string!, $startDate: Time!, $endDate: Time!) {
      viewer {
        accounts(filter: { accountTag: $accountTag }) {
          r2OperationsAdaptiveGroups(
            limit: 10000
            filter: { datetime_geq: $startDate, datetime_leq: $endDate }
          ) {
            sum { requests }
            dimensions { actionType }
          }
          r2StorageAdaptiveGroups(
            limit: 10000
            filter: { datetime_geq: $startDate, datetime_leq: $endDate }
            orderBy: [datetime_DESC]
          ) {
            max { objectCount uploadCount payloadSize metadataSize }
            dimensions { bucketName datetime }
          }
        }
      }
    }
    """
    payload = {
        "query": query,
        "variables": {
            "accountTag": account_id,
            "startDate": month_start(),
            "endDate": utc_now(),
        },
    }
    r = requests.post(
        GRAPHQL_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError("Cloudflare GraphQL: " + "; ".join(str(e.get("message") or e) for e in data["errors"]))
    accounts = (((data.get("data") or {}).get("viewer") or {}).get("accounts") or [])
    if not accounts:
        raise RuntimeError("Cloudflare GraphQL returned no account data")
    account = accounts[0]

    class_a = class_b = free_ops = 0
    actions: dict[str, int] = {}
    for row in account.get("r2OperationsAdaptiveGroups") or []:
        action = str((row.get("dimensions") or {}).get("actionType") or "unknown")
        count = int((row.get("sum") or {}).get("requests") or 0)
        actions[action] = actions.get(action, 0) + count
        kind = classify(action)
        if kind == "A":
            class_a += count
        elif kind == "B":
            class_b += count
        else:
            free_ops += count

    # Storage is sampled over time. Take the latest sample for each bucket, then
    # sum those latest bucket sizes because the free tier is account-wide.
    latest: dict[str, dict[str, Any]] = {}
    for row in account.get("r2StorageAdaptiveGroups") or []:
        dims = row.get("dimensions") or {}
        name = str(dims.get("bucketName") or "")
        dt = str(dims.get("datetime") or "")
        if not name:
            continue
        if name not in latest or dt > latest[name]["datetime"]:
            m = row.get("max") or {}
            latest[name] = {
                "datetime": dt,
                "payloadSize": int(m.get("payloadSize") or 0),
                "metadataSize": int(m.get("metadataSize") or 0),
                "objectCount": int(m.get("objectCount") or 0),
            }
    storage_bytes = sum(v["payloadSize"] + v["metadataSize"] for v in latest.values())
    objects = sum(v["objectCount"] for v in latest.values())
    return {
        "classA": class_a,
        "classB": class_b,
        "freeOperations": free_ops,
        "storageBytes": storage_bytes,
        "objectCount": objects,
        "actions": dict(sorted(actions.items())),
        "buckets": latest,
    }


def emit_output(path: str | None, values: dict[str, Any]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for key, value in values.items():
            text = str(value).replace("\n", " ")
            f.write(f"{key}={text}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--github-output")
    ap.add_argument("--status-file", default=str(DEFAULT_STATUS))
    ap.add_argument("--migration-check", action="store_true")
    args = ap.parse_args()

    config = read_json(CONFIG_PATH, {})
    storage = config.get("storage") or {}
    r2 = storage.get("r2") or {}
    guard = storage.get("quota_guard") or {}
    configured_mode = str(storage.get("mode") or "auto").lower()
    status_path = Path(args.status_file)
    prior = read_json(status_path, {})

    # Before the one-time migration the old local archive remains authoritative.
    if configured_mode != "r2" and not args.migration_check:
        status = {
            "schemaVersion": 1,
            "checkedAt": utc_now(),
            "selectedMode": "legacy",
            "reasonCodes": ["pre-migration"],
            "message": "R2 migration has not been committed yet; continue current local/Git storage.",
            "r2ReadAllowed": True,
        }
        write_json(status_path, status)
        emit_output(args.github_output, {
            "mode": "legacy", "reason": "pre-migration", "replay_write_budget": 0,
            "explore_pages": int(config.get("crawler", {}).get("explore_pages_per_run", 12)),
            "disable_images": "false", "r2_read_allowed": "true",
        })
        return 0

    account_id = os.environ.get("CLOUDFLARE_R2_ACCOUNT_ID", "").strip()
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    fail_closed = bool(guard.get("fail_closed", True))

    pause_storage = int(guard.get("storage_pause_bytes") or 8_500_000_000)
    resume_storage = int(guard.get("storage_resume_bytes") or 8_250_000_000)
    pause_a = int(guard.get("class_a_pause") or 750_000)
    resume_a = int(guard.get("class_a_resume") or 500_000)
    pause_b = int(guard.get("class_b_pause") or 7_500_000)
    resume_b = int(guard.get("class_b_resume") or 5_000_000)
    read_disable_b = int(guard.get("class_b_read_disable") or 9_000_000)
    image_pause_storage = int(guard.get("image_pause_storage_bytes") or 6_000_000_000)
    max_replay = int(guard.get("replay_max_writes_per_run") or 25_000)
    reserve_a = int(guard.get("class_a_reserve_per_run") or 10_000)
    default_pages = int(config.get("crawler", {}).get("explore_pages_per_run", 12))

    usage: dict[str, Any] | None = None
    error = None
    if not account_id or not api_token:
        error = "Missing CLOUDFLARE_R2_ACCOUNT_ID or CLOUDFLARE_API_TOKEN"
    else:
        try:
            usage = query_usage(account_id, api_token)
        except Exception as exc:
            error = str(exc)

    if usage is None:
        selected = "github" if fail_closed or args.migration_check else "r2"
        status = {
            "schemaVersion": 1,
            "checkedAt": utc_now(),
            "selectedMode": selected,
            "reasonCodes": ["metrics-unavailable"],
            "message": error or "R2 metrics unavailable",
            "r2ReadAllowed": False if selected == "github" else True,
            "limits": {
                "storageBytes": 10_000_000_000,
                "classA": 1_000_000,
                "classB": 10_000_000,
            },
        }
        write_json(status_path, status)
        emit_output(args.github_output, {
            "mode": selected, "reason": "metrics-unavailable", "replay_write_budget": 0,
            "explore_pages": 0 if selected == "github" else default_pages,
            "disable_images": "true" if selected == "github" else "false",
            "r2_read_allowed": "false" if selected == "github" else "true",
        })
        if args.migration_check and selected != "r2":
            print(f"R2 migration blocked: {error}")
            return 2
        return 0

    a = int(usage["classA"])
    b = int(usage["classB"])
    s = int(usage["storageBytes"])
    previous_mode = str(prior.get("selectedMode") or "")
    previous_reasons = set(prior.get("reasonCodes") or [])

    reasons: list[str] = []
    # Hysteresis: a dimension that caused fallback must fall below its resume
    # point before it stops blocking. Other dimensions only need to stay below
    # their pause point.
    storage_limit_now = resume_storage if previous_mode == "github" and "storage" in previous_reasons else pause_storage
    a_limit_now = resume_a if previous_mode == "github" and "class-a" in previous_reasons else pause_a
    b_limit_now = resume_b if previous_mode == "github" and "class-b" in previous_reasons else pause_b
    if s >= storage_limit_now:
        reasons.append("storage")
    if a >= a_limit_now:
        reasons.append("class-a")
    if b >= b_limit_now:
        reasons.append("class-b")

    selected = "github" if reasons else "r2"
    read_allowed = b < read_disable_b
    disable_images = s >= image_pause_storage or a >= max(0, pause_a - 50_000)

    # Leave enough Class A room for state/index objects and a normal scan. This
    # is only used when replaying Git fallback journals back to R2.
    available_replay = max(0, pause_a - a - reserve_a)
    replay_budget = min(max_replay, available_replay) if selected == "r2" else 0

    # Broad discovery is the largest source of new object writes. Throttle it as
    # Class A headroom shrinks, even before full fallback is necessary.
    if selected == "r2":
        writable_new_bots = max(0, pause_a - a - reserve_a - replay_budget)
        safe_pages = min(default_pages, max(0, writable_new_bots // 250))
    else:
        safe_pages = 0

    status = {
        "schemaVersion": 1,
        "checkedAt": utc_now(),
        "monthStart": month_start(),
        "selectedMode": selected,
        "reasonCodes": reasons,
        "message": "R2 inside safety envelope" if not reasons else "R2 paused; new observations are being journaled in Git until usage is healthy again.",
        "r2ReadAllowed": read_allowed,
        "imagesAllowed": not disable_images,
        "usage": usage,
        "limits": {
            "storageFreeBytes": 10_000_000_000,
            "classAFree": 1_000_000,
            "classBFree": 10_000_000,
            "storagePauseBytes": pause_storage,
            "storageResumeBytes": resume_storage,
            "classAPause": pause_a,
            "classAResume": resume_a,
            "classBPause": pause_b,
            "classBResume": resume_b,
            "classBReadDisable": read_disable_b,
            "imagePauseStorageBytes": image_pause_storage,
        },
        "planned": {
            "fallbackReplayWriteBudget": replay_budget,
            "r2ExplorePages": safe_pages,
        },
    }
    write_json(status_path, status)
    reason_text = ",".join(reasons) if reasons else "healthy"
    emit_output(args.github_output, {
        "mode": selected,
        "reason": reason_text,
        "replay_write_budget": replay_budget,
        "explore_pages": safe_pages,
        "disable_images": "true" if disable_images else "false",
        "r2_read_allowed": "true" if read_allowed else "false",
    })

    print(
        f"R2 guard: {selected} | storage {s:,}/10,000,000,000 | "
        f"Class A {a:,}/1,000,000 | Class B {b:,}/10,000,000 | "
        f"reasons={reason_text}"
    )
    if args.migration_check and selected != "r2":
        print("R2 migration blocked by free-tier guard.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
