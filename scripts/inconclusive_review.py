"""Review-only public index of unresolved missing-ID API responses.

Use the queue already loaded into memory by the archive. No per-bot R2 GETs,
no additional Character API calls, and no changes to verified bot history.
Only IDs already queued from past public discovery are included. User review
decisions are annotations, never proof of current API availability.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
DESIGNATIONS = frozenset({"unreviewed", "public", "unavailable", "restricted", "inconclusive"})
ERROR_CLASSES = {"api-error", "empty-response", "awaiting-confirmation"}
SCHEMA_VERSION = 1


def valid_id(value: Any) -> str:
    value = str(value or "").lower().strip()
    return value if UUID.fullmatch(value) else ""


def safe_http_url(value: Any) -> str:
    raw = str(value or "").strip()[:1000]
    try:
        parsed = urlparse(raw)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            return raw
    except ValueError:
        pass
    return ""


def normalize_corrections(data: Any) -> list[dict[str, str]]:
    """Validate owner-editable JSON; never execute or trust a status claim."""
    if not isinstance(data, dict) or not isinstance(data.get("corrections"), list):
        return []
    unique: dict[str, dict[str, str]] = {}
    for row in data["corrections"]:
        if not isinstance(row, dict):
            continue
        bot_id = valid_id(row.get("id"))
        designation = str(row.get("designation") or "").lower().strip()
        if not bot_id or designation not in DESIGNATIONS:
            continue
        item = {"id": bot_id, "designation": designation}
        note = str(row.get("note") or "").strip()[:500]
        if note:
            item["note"] = note
        evidence = safe_http_url(row.get("evidenceUrl"))
        if evidence:
            item["evidenceUrl"] = evidence
        replacement = valid_id(row.get("correctBotId"))
        if replacement and replacement != bot_id:
            item["correctBotId"] = replacement
        unique[bot_id] = item
    return list(unique.values())


def classify_case(info: dict[str, Any], *, required: int = 2) -> str | None:
    if not isinstance(info, dict) or not info.get("lastAt"):
        return None  # Not checked yet; don't confuse unqueried with an error.
    try:
        status = int(info.get("lastStatus") or 0)
    except (ValueError, TypeError):
        status = 0
    if status == 404 and 0 < int(info.get("count") or 0) < max(2, required):
        return "awaiting-confirmation"
    if status == 200 and info.get("errorKind") == "empty-character-payload":
        return "empty-response"
    if status not in (200, 404):
        return "api-error"
    return None


def build_review_index(
    state: dict[str, Any], corrections: Any, at: str, *, required: int = 2
) -> tuple[dict[str, Any], dict[str, Any]]:
    checks = state.get("missingChecks") or {}
    if not isinstance(checks, dict):
        checks = {}
    decisions = normalize_corrections(corrections)
    pending = set(checks)

    # Automatically prune manual annotations after *conclusive* API outcomes.
    # Their removal is not itself evidence: the verifier performed the action.
    remaining_corrections = [row for row in decisions if row["id"] in pending]
    by_id = {row["id"]: row for row in remaining_corrections}
    entries: list[dict[str, Any]] = []
    counts = {"api-error": 0, "empty-response": 0, "awaiting-confirmation": 0}
    unqueried = 0

    for raw_id, info in checks.items():
        bot_id = valid_id(raw_id)
        if not bot_id or not isinstance(info, dict):
            continue
        category = classify_case(info, required=required)
        if not category:
            if not info.get("lastAt"):
                unqueried += 1
            continue
        counts[category] += 1
        try:
            status = int(info.get("lastStatus") or 0)
        except (ValueError, TypeError):
            status = 0
        entry = {
            "id": bot_id,
            "category": category,
            "httpStatus": status,
            "confirmations": max(0, int(info.get("count") or 0)),
            "queuedAt": str(info.get("queuedAt") or "")[:40],
            "lastCheckedAt": str(info.get("lastAt") or "")[:40],
            "archiveUrl": "https://spicychatarchive.drache.uk/bot/?id=" + bot_id,
            "spicychatUrl": "https://spicychat.ai/chatbot/" + bot_id,
        }
        if info.get("errorKind"):
            entry["errorKind"] = str(info.get("errorKind"))[:64]
        if bot_id in by_id and by_id[bot_id]["designation"] != "unreviewed":
            entry["manualReview"] = deepcopy(by_id[bot_id])
        entries.append(entry)
    entries.sort(key=lambda row: (row["category"], row["id"]))
    index = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": at,
        "pendingTotal": len(checks),
        "notYetChecked": unqueried,
        "caseCount": len(entries),
        "counts": counts,
        "cases": entries,
        "note": "Review candidates only. No single HTTP error or 404 proves deletion. Manual designations do not change archived status.",
    }
    # The corrections file is also the editable *review queue*. Every
    # inconclusive case gets a populated row with its bot links and observed
    # status, so the user can simply edit "designation" and "note" in GitHub.
    # Unreviewed is explicitly NOT an API claim, and existing manual reviews
    # are retained until the verifier conclusively resolves an ID.
    corrections_rows = []
    for case in entries:
        row = {
            "id": case["id"],
            "designation": "unreviewed",
            "category": case["category"],
            "httpStatus": case["httpStatus"],
            "lastCheckedAt": case["lastCheckedAt"],
            "spicychatUrl": case["spicychatUrl"],
            "archiveUrl": case["archiveUrl"],
            "note": "",
        }
        manual = by_id.get(case["id"])
        if manual:
            row["designation"] = manual["designation"]
            for name in ("note", "evidenceUrl", "correctBotId"):
                if name in manual:
                    row[name] = manual[name]
        corrections_rows.append(row)
    corrections_file = {"schemaVersion": SCHEMA_VERSION, "corrections": corrections_rows}
    return index, corrections_file


def write_if_changed(path: Path, data: Any) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def publish_review_files(data_dir: Path, state: dict[str, Any], at: str, *, required: int = 2) -> dict[str, Any]:
    corrections_path = data_dir / "inconclusive-corrections.json"
    try:
        corrections = json.loads(corrections_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        corrections = {"schemaVersion": SCHEMA_VERSION, "corrections": []}
    index, cleaned = build_review_index(state, corrections, at, required=required)
    write_if_changed(data_dir / "inconclusive.json", index)
    # Repopulate every unresolved review candidate; conclusive API outcomes
    # disappear automatically, while manually edited designations survive.
    write_if_changed(corrections_path, cleaned)
    print(
        f"Inconclusive review: {index['caseCount']:,} cases "
        f"({index['counts']['api-error']:,} API errors, "
        f"{index['counts']['empty-response']:,} empty responses, "
        f"{index['counts']['awaiting-confirmation']:,} awaiting 2nd 404); "
        f"{index['notYetChecked']:,} IDs still unqueried; "
        f"{sum(row['designation'] != 'unreviewed' for row in cleaned['corrections']):,} manual annotations retained.",
        flush=True,
    )
    return index
