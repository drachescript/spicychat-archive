#!/usr/bin/env python3
"""Analyze pending public Bot Status submissions for admin review.

Public submissions have already been privacy-sanitized by the upload Worker.
This script only compares those historical observations with the archive and
writes review metadata. It NEVER mutates live bot records and NEVER changes
availability/status.

The normal importer remains the only path that merges approved snapshots into
archive records, and its existing status-verification protections still apply.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any

import archive as legacy
from storage_r2 import R2ArchiveStore


ROOT = Path(__file__).resolve().parents[1]
PENDING_PREFIX = "_submissions/bot-status/pending/"
META_PREFIX = "_submissions/bot-status/meta/pending/"
ANALYSIS_PREFIX = "_submissions/bot-status/analysis/"
MAX_SUBMISSIONS_PER_RUN = 5
READ_WORKERS = 16

VOLATILE_COMPARE_FIELDS = {
    "updatedAt",
    "updated_at",
    "lastUpdatedAt",
    "last_updated_at",
    "num_messages",
    "num_messages_24h",
    "rating_score",
    "rating_count",
    "recommendation_score",
    "rank",
}

UNAVAILABLE_STATUSES = {
    "deleted",
    "missing",
    "unavailable",
    "not_found",
    "not-found",
    "private",
    "removed",
    "gone",
}


def utc_now() -> str:
    return legacy.utc_now()


def _get_json(store: R2ArchiveStore, key: str, default=None):
    # Reuse storage_r2.py's normal 404/retry handling instead of depending on
    # provider-specific boto exception classes.
    try:
        return store.get_json(key, default)
    except Exception:
        return default


def _put_json(store: R2ArchiveStore, key: str, value: Any) -> None:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    store.s3.put_object(
        Bucket=store.bucket,
        Key=key,
        Body=raw,
        ContentType="application/json; charset=utf-8",
        CacheControl="no-store",
    )


def _read_gzip_json(store: R2ArchiveStore, key: str) -> dict[str, Any] | None:
    try:
        obj = store.s3.get_object(Bucket=store.bucket, Key=key)
        raw = obj["Body"].read()
    except Exception:
        return None
    try:
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _list_pending_meta(store: R2ArchiveStore, limit: int) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    token = None

    while len(rows) < limit:
        kwargs: dict[str, Any] = {
            "Bucket": store.bucket,
            "Prefix": META_PREFIX,
            "MaxKeys": min(1000, max(1, limit - len(rows))),
        }
        if token:
            kwargs["ContinuationToken"] = token

        page = store.s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents") or []:
            key = str(obj.get("Key") or "")
            if not key.endswith(".json"):
                continue
            meta = _get_json(store, key, None)
            if not isinstance(meta, dict):
                continue
            if meta.get("analysisReady"):
                continue
            submission_id = str(meta.get("submissionId") or key.rsplit("/", 1)[-1].removesuffix(".json"))
            if submission_id:
                rows.append((submission_id, meta))
            if len(rows) >= limit:
                break

        if not page.get("IsTruncated") or len(rows) >= limit:
            break
        token = page.get("NextContinuationToken")

    rows.sort(key=lambda row: str(row[1].get("submittedAt") or ""))
    return rows


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    raw = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _meaningful(value: Any) -> bool:
    return legacy.meaningful(value)


def _normalized_for_compare(key: str, value: Any):
    if key == "tags" and isinstance(value, list):
        return sorted({str(x).strip().lower() for x in value if str(x).strip()})
    if isinstance(value, dict):
        return {
            k: _normalized_for_compare(k, v)
            for k, v in sorted(value.items())
            if k not in VOLATILE_COMPARE_FIELDS
        }
    if isinstance(value, list):
        return [_normalized_for_compare(key, item) for item in value]
    return value


def _diff_fields(snapshot: dict[str, Any], last_known: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for key, incoming in snapshot.items():
        if key in VOLATILE_COMPARE_FIELDS or key in {"character_id", "characterId", "id", "uuid"}:
            continue
        if not _meaningful(incoming):
            continue

        existing = last_known.get(key)
        if not _meaningful(existing):
            changed.append(key)
            continue

        if _normalized_for_compare(key, incoming) != _normalized_for_compare(key, existing):
            changed.append(key)
    return sorted(set(changed))


def _has_unavailable_history(record: dict[str, Any] | None, observed_status: str) -> bool:
    if str(observed_status or "").strip().lower() in UNAVAILABLE_STATUSES:
        return True
    if not isinstance(record, dict):
        return False

    current = str((record.get("status") or {}).get("current") or "").lower()
    if current in UNAVAILABLE_STATUSES:
        return True

    for row in record.get("availabilityHistory") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or row.get("state") or "").lower() in UNAVAILABLE_STATUSES:
            return True
    return False


def classify_record(
    *,
    bot_id: str,
    saved_at: str,
    snapshot: dict[str, Any],
    observed_status: str,
    problems: list[str],
    existing: dict[str, Any] | None,
    load_error: str | None = None,
) -> dict[str, Any]:
    digest = _snapshot_hash(snapshot)
    problem_list = [str(x) for x in problems if str(x)]
    if load_error:
        problem_list.append("archive_read_error")

    existing_status = (
        str((existing.get("status") or {}).get("current") or "")
        if isinstance(existing, dict)
        else ""
    )

    unavailable_history = _has_unavailable_history(existing, observed_status)

    if problem_list:
        classification = "problem"
        diff_fields: list[str] = []
        exact_duplicate = False
    elif existing is None:
        classification = "new"
        diff_fields = []
        exact_duplicate = False
    else:
        import_history = (
            ((existing.get("imports") or {}).get("qolBotStatus") or {}).get("snapshots")
            or []
        )
        exact_duplicate = any(
            isinstance(row, dict) and row.get("sha256") == digest
            for row in import_history
        )
        if exact_duplicate:
            classification = "exact_duplicate"
            diff_fields = []
        else:
            diff_fields = _diff_fields(snapshot, existing.get("lastKnown") or {})
            classification = "changed" if diff_fields else "known"

    return {
        "botId": bot_id,
        "savedAt": saved_at,
        "name": snapshot.get("name") or snapshot.get("title") or "",
        "creator": snapshot.get("creator_username") or snapshot.get("creator") or "",
        "classification": classification,
        "safe": classification not in {"problem", "exact_duplicate"},
        "unavailableHistory": unavailable_history,
        "observedStatus": observed_status,
        "existingStatus": existing_status,
        "diffFields": diff_fields,
        "problems": sorted(set(problem_list)),
        "snapshotHash": digest,
    }


def _load_existing_direct(store: R2ArchiveStore, bot_id: str):
    try:
        record = store.get_json(store.bot_key(bot_id), None)
        return bot_id, record if isinstance(record, dict) else None, None
    except Exception as exc:
        return bot_id, None, str(exc)


def analyze_submission(
    store: R2ArchiveStore,
    submission_id: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    payload = _read_gzip_json(store, f"{PENDING_PREFIX}{submission_id}.json.gz")
    if not isinstance(payload, dict):
        raise RuntimeError("pending payload missing or invalid")

    items = payload.get("savedCopies")
    if not isinstance(items, list):
        raise RuntimeError("pending payload has no savedCopies list")

    known_ids = {
        str(item.get("botId") or "").lower()
        for item in items
        if isinstance(item, dict)
        and str(item.get("botId") or "").lower() in store.known_ids
    }

    existing_by_id: dict[str, dict[str, Any] | None] = {}
    errors_by_id: dict[str, str] = {}

    if known_ids:
        print(
            f"  {submission_id}: loading {len(known_ids):,} known archive records "
            f"with {READ_WORKERS} concurrent readers...",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
            futures = {
                pool.submit(_load_existing_direct, store, bot_id): bot_id
                for bot_id in sorted(known_ids)
            }
            done = 0
            for future in as_completed(futures):
                bot_id, record, error = future.result()
                done += 1
                if error:
                    errors_by_id[bot_id] = error
                else:
                    existing_by_id[bot_id] = record
                if done % 500 == 0 or done == len(futures):
                    print(f"    review reads {done:,}/{len(futures):,}", flush=True)

    records: list[dict[str, Any]] = []
    breakdown = {
        "total": 0,
        "new": 0,
        "known": 0,
        "changed": 0,
        "exactDuplicate": 0,
        "problem": int(meta.get("malformedCount") or 0),
        "malformed": int(meta.get("malformedCount") or 0),
        "unavailableHistory": 0,
        "safe": 0,
    }

    for item in items:
        if not isinstance(item, dict):
            breakdown["problem"] += 1
            continue

        bot_id = str(item.get("botId") or "").lower()
        snapshot = item.get("snapshot")
        if not bot_id or not isinstance(snapshot, dict):
            breakdown["problem"] += 1
            continue

        row = classify_record(
            bot_id=bot_id,
            saved_at=str(item.get("savedAt") or payload.get("exportedAt") or meta.get("submittedAt") or ""),
            snapshot=deepcopy(snapshot),
            observed_status=str(item.get("observedStatus") or ""),
            problems=list(item.get("problems") or []),
            existing=existing_by_id.get(bot_id),
            load_error=errors_by_id.get(bot_id),
        )
        records.append(row)
        breakdown["total"] += 1

        if row["classification"] == "new":
            breakdown["new"] += 1
        elif row["classification"] == "known":
            breakdown["known"] += 1
        elif row["classification"] == "changed":
            breakdown["changed"] += 1
        elif row["classification"] == "exact_duplicate":
            breakdown["exactDuplicate"] += 1
        else:
            breakdown["problem"] += 1

        if row["unavailableHistory"]:
            breakdown["unavailableHistory"] += 1
        if row["safe"]:
            breakdown["safe"] += 1

    return {
        "schemaVersion": 1,
        "submissionId": submission_id,
        "analyzedAt": utc_now(),
        "breakdown": breakdown,
        "records": records,
    }


def write_summary(path: str | None, rows: list[dict[str, Any]]) -> None:
    total_bots = sum(int((row.get("breakdown") or {}).get("total") or 0) for row in rows)
    safe = sum(int((row.get("breakdown") or {}).get("safe") or 0) for row in rows)
    text = "\n".join(
        [
            "# Public Bot Status submission review",
            "",
            f"- Submissions analyzed: **{len(rows)}**",
            f"- Bot records analyzed: **{total_bots:,}**",
            f"- Safe records available for approval: **{safe:,}**",
        ]
    ) + "\n"
    print(text)
    if path:
        Path(path).write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-submissions", type=int, default=MAX_SUBMISSIONS_PER_RUN)
    ap.add_argument("--summary-file")
    args = ap.parse_args()

    config = legacy.load_config()
    if str((config.get("storage") or {}).get("mode") or "").lower() != "r2":
        print("Public submission analysis skipped: archive is not in R2 mode.")
        write_summary(args.summary_file, [])
        return 0
    if not R2ArchiveStore.credentials_present():
        print("ERROR: R2 credentials are required.")
        return 2

    store = R2ArchiveStore(config)
    if not store.is_migrated():
        print("ERROR: R2 migration marker is missing.")
        return 2

    store.load_discovery_order()

    pending = _list_pending_meta(store, max(1, int(args.max_submissions)))
    if not pending:
        print("Public Bot Status submission queue: nothing awaiting analysis.")
        write_summary(args.summary_file, [])
        return 0

    analyzed: list[dict[str, Any]] = []
    for submission_id, meta in pending:
        print(
            f"Analyzing public Bot Status submission {submission_id}: "
            f"{int(meta.get('botCount') or 0):,} bot copies",
            flush=True,
        )
        try:
            analysis = analyze_submission(store, submission_id, meta)
        except Exception as exc:
            print(f"  analysis failed: {exc}", flush=True)
            meta = dict(meta)
            meta["analysisError"] = str(exc)[:500]
            meta["analysisReady"] = False
            _put_json(store, f"{META_PREFIX}{submission_id}.json", meta)
            continue

        _put_json(store, f"{ANALYSIS_PREFIX}{submission_id}.json", analysis)

        meta = dict(meta)
        meta["status"] = "pending"
        meta["analysisReady"] = True
        meta["analyzedAt"] = analysis["analyzedAt"]
        meta["analysisError"] = None
        meta["breakdown"] = analysis["breakdown"]
        _put_json(store, f"{META_PREFIX}{submission_id}.json", meta)

        analyzed.append(analysis)
        b = analysis["breakdown"]
        print(
            "  ready for review: "
            f"{b['new']:,} new, {b['changed']:,} changed, "
            f"{b['known']:,} known, {b['exactDuplicate']:,} exact duplicates, "
            f"{b['unavailableHistory']:,} unavailable-history flags, "
            f"{b['problem']:,} problem/malformed.",
            flush=True,
        )

    write_summary(args.summary_file, analyzed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
