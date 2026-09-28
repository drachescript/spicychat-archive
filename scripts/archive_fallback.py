#!/usr/bin/env python3
"""Git fallback collector used while R2 free-tier usage is near a safety limit.

This intentionally does NOT create one JSON file per bot. It stores compact,
gzipped observation journals that are replayed into R2 later. Images are not
downloaded while R2 is paused; their public CDN URLs remain in the observations.
"""
from __future__ import annotations

import argparse
import gzip
import json
import hashlib
from collections import OrderedDict
from pathlib import Path
from typing import Any

import archive as legacy

ROOT = Path(__file__).resolve().parents[1]
FALLBACK_DIR = ROOT / "fallback"
OBS_DIR = FALLBACK_DIR / "observations"
STATE_PATH = FALLBACK_DIR / "state.json"
MANIFEST_PATH = ROOT / "data" / "manifest.json"
USAGE_PATH = ROOT / "data" / "r2-usage.json"


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




def observation_fingerprint(item: dict[str, Any]) -> str:
    payload = {
        "typesense": item.get("typesense") or {},
        "character": item.get("character") or {},
        "characterStatus": item.get("characterStatus"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def pending_fingerprints() -> dict[str, str]:
    """Index the newest pending Git observation for each bot.

    Hot listing bots appear every three hours; without this check we'd keep the
    same public document hundreds of times while R2 is paused. Existing journals
    remain immutable and only genuinely different observations get appended.
    """
    latest: dict[str, str] = {}
    if not OBS_DIR.exists():
        return latest
    for path in sorted(OBS_DIR.glob("*.jsonl.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line in f:
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    bot_id = str((item or {}).get("id") or "").lower()
                    if bot_id:
                        latest[bot_id] = observation_fingerprint(item)
        except Exception:
            continue
    return latest


def fallback_size_bytes() -> int:
    if not FALLBACK_DIR.exists():
        return 0
    return sum(p.stat().st_size for p in FALLBACK_DIR.rglob("*") if p.is_file())

def initial_state() -> dict[str, Any]:
    state = read_json(STATE_PATH, None)
    if isinstance(state, dict):
        return state
    manifest = read_json(MANIFEST_PATH, {})
    exp = manifest.get("exploration") or {}
    return {
        "schemaVersion": 1,
        "exploration": {
            "mode": exp.get("mode") or "page",
            "page": int(exp.get("page") or 1),
            "pass": int(exp.get("pass") or 0),
            "cursorCreatedAt": exp.get("cursorCreatedAt"),
            "lastCreatedAt": exp.get("lastCreatedAt"),
            "lastFound": exp.get("found"),
        },
        "runs": 0,
    }


def add_doc(rows: OrderedDict[str, dict[str, Any]], doc: dict[str, Any], surface: str, at: str) -> None:
    bot_id = legacy.normalize_id(doc)
    if not bot_id:
        return
    item = rows.get(bot_id)
    if item is None:
        item = {"id": bot_id, "at": at, "surfaces": [], "typesense": legacy.clean_for_archive(doc)}
        rows[bot_id] = item
    if surface not in item["surfaces"]:
        item["surfaces"].append(surface)
    # Prefer the newest/rawest document if a listing happened to return a variant.
    item["typesense"] = legacy.merge_last_known(item.get("typesense") or {}, legacy.clean_for_archive(doc))


def scan_listing(client: legacy.Client, config: dict[str, Any], name: str, sort_by: str, rows: OrderedDict[str, dict[str, Any]], at: str, max_hits: int) -> dict[str, Any]:
    page_size = 250
    page = 1
    count = 0
    found = None
    while count < max_hits:
        req = legacy.typesense_search(config, page=page, per_page=min(page_size, max_hits - count), sort_by=sort_by)
        response = client.multi_search([req])
        if not response.ok:
            return {"ok": False, "error": response.error, "count": count, "found": found}
        result = (response.data.get("results") or [{}])[0]
        hits, found_now = legacy.extract_hits(result)
        if found is None:
            found = found_now
        if not hits:
            break
        for doc in hits:
            add_doc(rows, doc, name, at)
        count += len(hits)
        if len(hits) < req["per_page"]:
            break
        page += 1
    return {"ok": True, "count": count, "found": found}


def explore(client: legacy.Client, config: dict[str, Any], rows: OrderedDict[str, dict[str, Any]], state: dict[str, Any], at: str, pages_budget: int) -> dict[str, Any]:
    exp = state.setdefault("exploration", {})
    mode = exp.get("mode") or "page"
    page_size = 250
    hits_total = 0
    errors: list[str] = []

    for _ in range(max(0, pages_budget)):
        if mode == "cursor" and exp.get("cursorCreatedAt"):
            cursor = exp["cursorCreatedAt"]
            filter_by = f"{config['typesense']['application_filter']} && createdAt:<{cursor}"
            req = legacy.typesense_search(config, page=1, per_page=page_size, sort_by="createdAt:desc", filter_by=filter_by)
        else:
            page = int(exp.get("page") or 1)
            req = legacy.typesense_search(config, page=page, per_page=page_size, sort_by="createdAt:desc")

        response = client.multi_search([req])
        if not response.ok:
            errors.append(response.error or f"HTTP {response.status}")
            break
        result = (response.data.get("results") or [{}])[0]
        hits, found = legacy.extract_hits(result)
        exp["lastFound"] = found

        if not hits:
            if mode == "page" and found and (int(exp.get("page") or 1) - 1) * page_size < found:
                cursor = exp.get("lastCreatedAt")
                if cursor is not None:
                    exp["mode"] = mode = "cursor"
                    exp["cursorCreatedAt"] = cursor
                    continue
            exp["pass"] = int(exp.get("pass") or 0) + 1
            exp["page"] = 1
            exp["mode"] = mode = "page"
            exp["cursorCreatedAt"] = None
            exp["lastCreatedAt"] = None
            break

        for doc in hits:
            add_doc(rows, doc, "explore", at)
        hits_total += len(hits)
        last_created = hits[-1].get("createdAt")
        if last_created is not None:
            exp["lastCreatedAt"] = last_created

        if mode == "cursor":
            if last_created is None or last_created == exp.get("cursorCreatedAt"):
                errors.append("fallback cursor could not advance createdAt")
                break
            exp["cursorCreatedAt"] = last_created
        else:
            exp["page"] = int(exp.get("page") or 1) + 1

        if len(hits) < page_size:
            exp["pass"] = int(exp.get("pass") or 0) + 1
            exp["page"] = 1
            exp["mode"] = mode = "page"
            exp["cursorCreatedAt"] = None
            exp["lastCreatedAt"] = None
            break

    return {"hits": hits_total, "mode": mode, "errors": errors}


def enrich(client: legacy.Client, rows: OrderedDict[str, dict[str, Any]], budget: int) -> dict[str, int]:
    processed = enriched = missing = restricted = 0
    # Prefer bots whose public Typesense document says a definition may be visible,
    # then newest/updated records. This captures rich fields during a long R2 pause.
    candidates = list(rows.values())
    candidates.sort(key=lambda x: (
        not bool((x.get("typesense") or {}).get("definition_visible")),
        str((x.get("typesense") or {}).get("updatedAt") or ""),
    ), reverse=False)
    for item in candidates[: max(0, budget)]:
        processed += 1
        response = client.character(item["id"])
        if response.ok:
            payload = legacy.unwrap_character_payload(response.data)
            if payload:
                payload.setdefault("character_id", item["id"])
                item["character"] = legacy.clean_for_archive(payload)
                enriched += 1
        elif response.status == 404:
            missing += 1
            item["characterStatus"] = 404
        elif response.status in {401, 403}:
            restricted += 1
            item["characterStatus"] = response.status
    return {"processed": processed, "enriched": enriched, "missing": missing, "restricted": restricted}


def save_journal(rows: OrderedDict[str, dict[str, Any]], at: str) -> tuple[Path | None, int]:
    if not rows:
        return None, 0
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    slug = at.replace(":", "").replace("-", "")
    path = OBS_DIR / f"{slug}.jsonl.gz"
    # Avoid accidental name collision on a manually repeated run in the same second.
    n = 1
    while path.exists():
        path = OBS_DIR / f"{slug}-{n}.jsonl.gz"
        n += 1
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as f:
        for item in rows.values():
            f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path, path.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary-file")
    args = ap.parse_args()

    config = legacy.load_config()
    fallback = ((config.get("storage") or {}).get("fallback") or {})
    listing_max = int(fallback.get("listing_max_hits") or 1000)
    pages = int(fallback.get("explore_pages_per_run") or 12)
    enrich_budget = int(fallback.get("enrichment_budget") or 100)
    at = legacy.utc_now()
    client = legacy.Client(config)
    state = initial_state()
    rows: OrderedDict[str, dict[str, Any]] = OrderedDict()

    listing_info: dict[str, Any] = {}
    for name, sort_by in config.get("listings", {}).items():
        info = scan_listing(client, config, name, sort_by, rows, at, listing_max)
        listing_info[name] = info
        print(f"fallback {name}: ok={info.get('ok')} count={info.get('count')} found={info.get('found')}")

    exp = explore(client, config, rows, state, at, pages)
    rich = enrich(client, rows, enrich_budget)

    # Do not keep identical hot-listing observations every three hours. Compare
    # against the latest pending Git copy and append only actual changes/new bots.
    prior_fingerprints = pending_fingerprints()
    before_dedupe = len(rows)
    rows = OrderedDict(
        (bot_id, item) for bot_id, item in rows.items()
        if prior_fingerprints.get(bot_id) != observation_fingerprint(item)
    )
    duplicates_skipped = before_dedupe - len(rows)

    max_pending = int(fallback.get("max_pending_bytes") or 1_000_000_000)
    current_pending = fallback_size_bytes()
    if current_pending >= max_pending:
        # Fail closed rather than making the Git repository unbounded. The crawler
        # can catch up through Typesense after R2 becomes healthy again.
        rows.clear()
        journal = None
        compressed_bytes = 0
        fallback_full = True
    else:
        journal, compressed_bytes = save_journal(rows, at)
        fallback_full = False
    state["lastRunAt"] = at
    state["runs"] = int(state.get("runs") or 0) + 1
    state["lastRun"] = {
        "botsJournaled": len(rows),
        "duplicatesSkipped": duplicates_skipped,
        "compressedBytes": compressed_bytes,
        "fallbackFull": fallback_full,
        "exploration": exp,
        "enrichment": rich,
    }
    write_json(STATE_PATH, state)

    # Keep the public manifest honest without pretending the fallback journal has
    # already been merged into the permanent R2 archive.
    manifest = read_json(MANIFEST_PATH, {})
    if isinstance(manifest, dict):
        manifest["lastScan"] = at
        manifest["fallbackPending"] = True
        manifest["fallbackPendingFiles"] = len(list(OBS_DIR.glob("*.jsonl.gz"))) if OBS_DIR.exists() else 0
        manifest["storageMode"] = "r2-paused-git-journal"
        write_json(MANIFEST_PATH, manifest)

    usage = read_json(USAGE_PATH, {})
    reasons = ", ".join(usage.get("reasonCodes") or ["R2 safety guard"])
    text = "\n".join([
        f"# SpicyChat Archive Git fallback — {at}",
        "",
        f"- R2 paused because: **{reasons}**",
        f"- Bots journaled: **{len(rows):,}**",
        f"- Unchanged pending observations skipped: **{duplicates_skipped:,}**",
        f"- Compressed Git journal: **{compressed_bytes / 1024 / 1024:.2f} MiB**",
        f"- Pending Git fallback size: **{fallback_size_bytes() / 1024 / 1024:.2f} MiB** / {max_pending / 1024 / 1024:.0f} MiB safety cap",
        f"- Git fallback safety cap reached: **{'yes — discovery will catch up after R2 resumes' if fallback_full else 'no'}**",
        f"- Exploration hits: **{exp['hits']:,}**",
        f"- Rich API enrichments: **{rich['enriched']:,}** / {rich['processed']:,}",
        "- Avatar binaries were **not** downloaded while R2 is paused.",
        "- These observations will replay into R2 automatically when the quota guard becomes healthy again.",
    ]) + "\n"
    print(text)
    if args.summary_file:
        Path(args.summary_file).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
