"""Cost-bounded, persistent name/avatar preview enrichment for review JSON.

The archive's existing verified-missing queue supplies the IDs. Only its already
saved R2 records are consulted; never query SpicyChat or download image bytes.
Previously checked previews are carried forward from generated JSON, so the
same archived bot is not read again on every scheduled run.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse

PREVIEW_FIELDS = ("name", "title", "creator", "avatar", "avatarFallback", "isNsfw")
MAX_LOOKUPS_PER_RUN = 2500
LOOKUP_WORKERS = 8
EMPTY_RETRY_DAYS = 14


def clean_text(value: Any, limit: int = 200) -> str:
    return " ".join(value.strip().split())[:limit] if isinstance(value, str) else ""


def https_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    try:
        parsed = urlparse(value)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            return value[:1500]
    except ValueError:
        pass
    return ""


def image_url(value: Any, public_base: str = "") -> str:
    safe = https_url(value)
    if safe:
        return safe
    if not isinstance(value, str):
        return ""
    path = value.strip().lstrip("/")
    if path.startswith("avatars/") and all(ch not in path for ch in '<>"\'\\'):
        return "https://cdn.nd-api.com/" + path[:1400] + ("" if "?" in path else "?class=avatar256x256")
    if path.startswith("media/") and all(ch not in path for ch in '<>"\'\\'):
        base = https_url(public_base).rstrip("/")
        return base + "/" + path[:1400] if base else ""
    return ""


def preview_from_record(record: Any, public_base: str = "") -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    known = record.get("lastKnown") or {}
    known = known if isinstance(known, dict) else {}
    archived = record.get("avatarArchive") or {}
    archived = archived if isinstance(archived, dict) else {}
    name = clean_text(known.get("name") or known.get("title"))
    title = clean_text(known.get("title"))
    creator = clean_text(known.get("creator_username") or known.get("creator"))
    archived_image = image_url(
        archived.get("publicUrl") or archived.get("r2Key") or archived.get("path"),
        public_base,
    )
    source_image = image_url(
        known.get("avatar_url") or known.get("avatar") or known.get("image"),
        public_base,
    )
    result: dict[str, Any] = {}
    if name and name.casefold() != "unknown bot":
        result["name"] = name
    if title and title != name:
        result["title"] = title
    if creator:
        result["creator"] = creator
    if archived_image or source_image:
        result["avatar"] = archived_image or source_image
    if archived_image and source_image and archived_image != source_image:
        result["avatarFallback"] = source_image
    result["isNsfw"] = bool(known.get("is_nsfw") or known.get("avatar_is_nsfw"))
    return result


def prior_preview(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    result = {}
    for field in ("name", "title", "creator"):
        value = clean_text(row.get(field))
        if value:
            result[field] = value
    for field in ("avatar", "avatarFallback"):
        value = https_url(row.get(field))
        if value:
            result[field] = value
    if isinstance(row.get("isNsfw"), bool):
        result["isNsfw"] = row["isNsfw"]
    stamp = clean_text(row.get("previewCheckedAt"), 40)
    if stamp:
        result["previewCheckedAt"] = stamp
    return result


def _is_due(preview: dict[str, Any], at: str) -> bool:
    if not preview.get("previewCheckedAt"):
        return True
    if preview.get("name") or preview.get("avatar"):
        return False
    try:
        last = datetime.fromisoformat(preview["previewCheckedAt"].replace("Z", "+00:00"))
        current = datetime.fromisoformat(at.replace("Z", "+00:00"))
        return current - last >= timedelta(days=EMPTY_RETRY_DAYS)
    except (ValueError, TypeError):
        return False


def enrich_case_previews(
    cases: list[dict[str, Any]],
    previous_rows: Any,
    lookup: Callable[[str], Any] | None,
    at: str,
    *,
    public_base: str = "",
    max_lookups: int = MAX_LOOKUPS_PER_RUN,
    workers: int = LOOKUP_WORKERS,
) -> dict[str, int]:
    """Mutate only generated review rows; do not affect verification status."""
    previous = {}
    if isinstance(previous_rows, list):
        previous = {
            str(row.get("id") or "").lower(): prior_preview(row)
            for row in previous_rows if isinstance(row, dict) and row.get("id")
        }
    pending: list[dict[str, Any]] = []
    for case in cases:
        preview = previous.get(case["id"], {})
        case.update(preview)
        if lookup is not None and _is_due(preview, at):
            pending.append(case)

    selected = pending[:max(0, int(max_lookups))]
    success = failures = 0

    def fetch(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        try:
            record = lookup(row["id"])
            return row, preview_from_record(record, public_base)
        except Exception:
            # A transient R2 error must not break the archive run or mark the ID
            # checked, so it can be retried on the next ordinary run.
            return row, None

    if selected:
        with ThreadPoolExecutor(max_workers=max(1, min(16, workers))) as pool:
            for future in as_completed([pool.submit(fetch, row) for row in selected]):
                row, result = future.result()
                if result is None:
                    failures += 1
                    continue
                row.update(result)
                row["previewCheckedAt"] = at
                success += 1
    return {
        "retained": len(cases) - len(pending),
        "lookupAttempts": len(selected),
        "lookupsSucceeded": success,
        "lookupErrors": failures,
        "deferred": len(pending) - len(selected),
        "withNames": sum(bool(row.get("name")) for row in cases),
        "withAvatars": sum(bool(row.get("avatar")) for row in cases),
    }
