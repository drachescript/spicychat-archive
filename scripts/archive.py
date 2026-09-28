#!/usr/bin/env python3
"""SpicyChat public catalog archiver.

Design rules:
- Public data only. No account cookies or private auth.
- Once a non-empty field has been observed, a later missing/hidden field does not erase it.
- Raw/current observations and last-known-good data are kept separately.
- Bots are only marked deleted after repeated explicit 404s from the public character endpoint.
- The crawler always refreshes the public listings, then spends remaining work on broader discovery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
ARCHIVE_DIR = ROOT / "archive"
BOTS_DIR = ARCHIVE_DIR / "bots"
RANKINGS_DIR = ARCHIVE_DIR / "rankings"
MEDIA_DIR = ARCHIVE_DIR / "media"
STATE_PATH = ARCHIVE_DIR / "state.json"
SITE_DIR = ROOT
SITE_DATA_DIR = ROOT / "data"
SITE_CATALOG_DIR = SITE_DATA_DIR / "catalog"
SITE_BOTS_DIR = SITE_DATA_DIR / "bots"

UUID_RE = re.compile(r"^[0-9a-fA-F-]{16,}$")
VOLATILE_FIELDS = {
    "num_messages", "num_messages_24h", "rating_score", "rating_count",
    "recommendation_score", "rank"
}
KNOWN_TEXT_FIELDS = {
    "name", "title", "description", "greeting", "personality", "scenario",
    "example_dialogue", "example_dialogues", "definition", "persona",
    "character_definition", "characterDefinition", "system_prompt",
    "post_history_instructions", "greetings", "alternate_greetings",
    "creator_username", "language", "type", "visibility"
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_if_changed(path: Path, data: Any, *, indent: int | None = 2) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, sort_keys=False, indent=indent, separators=None if indent else (",", ":")) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


def meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True  # False and 0 are meaningful values.


def normalize_id(doc: dict[str, Any]) -> str | None:
    for key in ("character_id", "id", "characterId", "uuid"):
        value = str(doc.get(key) or "").strip()
        if value:
            return value.lower()
    return None


def normalize_avatar_url(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("//"):
        text = "https:" + text
    if text.startswith(("https://", "http://")):
        return text
    text = text.lstrip("/")
    if text.startswith("avatars/"):
        suffix = "" if "?" in text else "?class=avatar256x256"
        return f"https://cdn.nd-api.com/{text}{suffix}"
    return text


def shard_for(bot_id: str) -> str:
    compact = bot_id.replace("-", "").lower()
    if compact and compact[0] in "0123456789abcdef":
        return compact[0]
    return "_"


def bot_path(bot_id: str) -> Path:
    compact = bot_id.replace("-", "").lower()
    prefix = compact[:2] if len(compact) >= 2 and all(c in "0123456789abcdef" for c in compact[:2]) else "__"
    return BOTS_DIR / prefix / f"{bot_id}.json"


def iter_bot_paths() -> Iterable[Path]:
    if not BOTS_DIR.exists():
        return []
    return (p for p in BOTS_DIR.rglob("*.json") if p.is_file())


def clean_for_archive(doc: dict[str, Any]) -> dict[str, Any]:
    """Keep the public document essentially raw, while normalizing avatar URL convenience."""
    out = deepcopy(doc)
    for key in ("avatar_url", "image", "avatar"):
        if key in out and isinstance(out[key], str):
            normalized = normalize_avatar_url(out[key])
            if normalized:
                out[key] = normalized
    return out


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten dict leaves for change history. Lists remain atomic."""
    result: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                result.update(flatten(value, path))
            else:
                result[path] = value
    else:
        result[prefix or "value"] = obj
    return result


def merge_last_known(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Monotonic merge: missing/empty values never erase known values."""
    out = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict):
            existing = out.get(key) if isinstance(out.get(key), dict) else {}
            out[key] = merge_last_known(existing, value)
        elif meaningful(value):
            out[key] = deepcopy(value)
    return out


def append_change(record: dict[str, Any], *, at: str, source: str, path: str, old: Any, new: Any, kind: str = "value") -> None:
    history = record.setdefault("fieldHistory", [])
    event = {"at": at, "source": source, "path": path, "kind": kind}
    if old is not None:
        event["from"] = old
    if new is not None:
        event["to"] = new
    # Avoid exact duplicate tail events.
    if history and history[-1] == event:
        return
    history.append(event)


def observe_bot(record: dict[str, Any] | None, incoming: dict[str, Any], *, source: str, at: str) -> tuple[dict[str, Any], bool]:
    incoming = clean_for_archive(incoming)
    bot_id = normalize_id(incoming)
    if not bot_id:
        raise ValueError("observation has no character id")

    created = record is None
    if record is None:
        record = {
            "schemaVersion": 1,
            "id": bot_id,
            "firstSeenAt": at,
            "lastSeenAt": at,
            "status": {"current": "public", "since": at, "lastVerifiedAt": at},
            "sources": {},
            "current": {},
            "lastKnown": {},
            "fieldHistory": [],
            "availabilityHistory": [{"status": "public", "from": at, "source": source}],
            "listings": {},
            "metrics": {"latest": {}, "history": []},
            "avatarArchive": {},
        }

    changed = created
    previous_current = record.get("current", {}).get(source, {}) if isinstance(record.get("current"), dict) else {}
    prev_flat = flatten(previous_current)
    last_known_flat = flatten(record.get("lastKnown") or {})
    new_flat = flatten(incoming)

    # Track genuine metadata changes against the cross-source last-known value.
    # This prevents the same initial document discovered through four listings from
    # being logged four times as four separate "changes". Volatile metrics are
    # handled separately below.
    for path, value in new_flat.items():
        leaf = path.rsplit(".", 1)[-1]
        if leaf in VOLATILE_FIELDS:
            continue
        old_known = last_known_flat.get(path)
        old_source = prev_flat.get(path)
        if meaningful(value) and old_known != value:
            if not created:
                append_change(record, at=at, source=source, path=path, old=old_known, new=value)
            changed = True
        elif path in prev_flat and meaningful(old_source) and not meaningful(value):
            # Explicitly record that a field disappeared/was hidden from this source,
            # but do not erase the global lastKnown value.
            append_change(record, at=at, source=source, path=path, old=old_source, new=None, kind="hidden-or-empty")
            changed = True

    # Some API versions hide a definition field by omitting the key entirely rather
    # than returning null. Record that state transition while retaining lastKnown.
    for path, old in prev_flat.items():
        leaf = path.rsplit(".", 1)[-1]
        if leaf in KNOWN_TEXT_FIELDS and path not in new_flat and meaningful(old):
            append_change(record, at=at, source=source, path=path, old=old, new=None, kind="hidden-or-missing")
            changed = True

    record.setdefault("current", {})[source] = incoming
    record["lastKnown"] = merge_last_known(record.get("lastKnown") or {}, incoming)
    record["lastSeenAt"] = at
    record.setdefault("sources", {})[source] = {"lastSeenAt": at}

    # Active observation restores a missing/deleted record without erasing its history.
    old_status = record.get("status", {}).get("current")
    if old_status != "public":
        record.setdefault("availabilityHistory", []).append({"status": "public", "from": at, "source": source})
        record["status"] = {"current": "public", "since": at, "lastVerifiedAt": at}
        changed = True
    else:
        record.setdefault("status", {})["lastVerifiedAt"] = at

    # Metrics are stored compactly only when they actually change.
    metric_keys = ("num_messages", "num_messages_24h", "rating_score", "rating_count", "token_count")
    metrics = {k: incoming[k] for k in metric_keys if k in incoming and meaningful(incoming[k])}
    if metrics:
        latest_metrics = record.setdefault("metrics", {}).get("latest") or {}
        if metrics != latest_metrics:
            record["metrics"]["latest"] = deepcopy(metrics)
            record["metrics"].setdefault("history", []).append({"at": at, "source": source, **metrics})
            # Bound high-frequency metric history in the per-bot file; raw ranking snapshots remain separate.
            record["metrics"]["history"] = record["metrics"]["history"][-512:]
            changed = True

    return record, changed


@dataclass
class HTTPResult:
    ok: bool
    status: int
    data: Any = None
    error: str | None = None
    url: str | None = None


class Client:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "spicychat-archive/0.1 (+public archival crawler)",
        })
        self.timeout = int(config["crawler"].get("request_timeout_seconds", 20))
        self.delay = max(0, int(config["crawler"].get("request_delay_ms", 80))) / 1000.0
        self.typesense_key = os.environ.get("SPICYCHAT_TYPESENSE_API_KEY", config["typesense"]["public_search_key"]).strip()
        self.guest_user_id = str(uuid.uuid4())

    def _sleep(self) -> None:
        if self.delay:
            time.sleep(self.delay)

    def multi_search(self, searches: list[dict[str, Any]]) -> HTTPResult:
        ts = self.config["typesense"]
        urls = [ts["primary_url"], *(ts.get("fallback_urls") or [])]
        payload = {"searches": searches}
        last_error = None
        for url in urls:
            try:
                self._sleep()
                r = self.session.post(
                    url,
                    headers={"X-TYPESENSE-API-KEY": self.typesense_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=self.timeout,
                )
                if r.ok:
                    return HTTPResult(True, r.status_code, r.json(), url=url)
                last_error = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code not in {429, 500, 502, 503, 504}:
                    return HTTPResult(False, r.status_code, error=last_error, url=url)
            except Exception as exc:  # network failover
                last_error = str(exc)
        return HTTPResult(False, 0, error=last_error or "all Typesense endpoints failed")

    def character(self, bot_id: str) -> HTTPResult:
        url = self.config["character_api"]["base_url"].rstrip("/") + "/" + bot_id
        try:
            self._sleep()
            r = self.session.get(url, headers={
                "X-App-Id": "spicychat",
                "X-Guest-UserId": self.guest_user_id,
                "X-Country": str(self.config.get("character_api", {}).get("country") or "US"),
            }, timeout=self.timeout)
            data = None
            try:
                data = r.json()
            except Exception:
                data = None
            return HTTPResult(r.ok, r.status_code, data=data, error=None if r.ok else r.text[:300], url=url)
        except Exception as exc:
            return HTTPResult(False, 0, error=str(exc), url=url)

    def image(self, url: str) -> tuple[bytes, str] | None:
        try:
            self._sleep()
            r = self.session.get(url, timeout=self.timeout)
            if not r.ok or not r.content:
                return None
            ctype = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if not ctype.startswith("image/"):
                return None
            return r.content, ctype
        except Exception:
            return None


def load_config() -> dict[str, Any]:
    return read_json(CONFIG_PATH, {})


def typesense_search(config: dict[str, Any], *, page: int = 1, per_page: int = 250, sort_by: str | None = None,
                     filter_by: str | None = None, q: str = "*", include_fields: str | None = None) -> dict[str, Any]:
    ts = config["typesense"]
    search: dict[str, Any] = {
        "collection": ts["collection"],
        "q": q,
        "query_by": ts["query_by"],
        "page": page,
        "per_page": per_page,
        "filter_by": filter_by or ts["application_filter"],
    }
    if sort_by:
        search["sort_by"] = sort_by
    if include_fields:
        search["include_fields"] = include_fields
    return search


def extract_hits(result: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    hits = []
    for hit in result.get("hits") or []:
        doc = hit.get("document") if isinstance(hit, dict) else None
        if isinstance(doc, dict):
            hits.append(doc)
    found = result.get("found")
    try:
        found = int(found)
    except Exception:
        found = None
    return hits, found


def load_bot(bot_id: str) -> dict[str, Any] | None:
    return read_json(bot_path(bot_id), None)


def save_bot(record: dict[str, Any]) -> bool:
    return write_json_if_changed(bot_path(record["id"]), record)


def queue_unique(queue: list[str], bot_id: str) -> None:
    if bot_id not in queue:
        queue.append(bot_id)


def ingest_documents(docs: Iterable[dict[str, Any]], *, source: str, at: str, state: dict[str, Any]) -> tuple[int, int, set[str]]:
    new_count = 0
    changed_count = 0
    seen: set[str] = set()
    for doc in docs:
        bot_id = normalize_id(doc)
        if not bot_id:
            continue
        seen.add(bot_id)
        existing = load_bot(bot_id)
        old_known = (existing or {}).get("lastKnown") or {}
        old_updated = old_known.get("updatedAt")
        old_definition_visible = old_known.get("definition_visible")
        old_avatar = normalize_avatar_url(old_known.get("avatar_url") or old_known.get("avatar") or old_known.get("image"))
        record, changed = observe_bot(existing, doc, source=source, at=at)
        if existing is None:
            new_count += 1
            queue_unique(state.setdefault("enrichmentQueue", []), bot_id)
        else:
            # A creator edit or definition-visibility transition is a strong reason
            # to refresh the richer character endpoint. This catches cases where a
            # Personality/Scenario becomes newly public or gets hidden later.
            if meaningful(doc.get("updatedAt")) and doc.get("updatedAt") != old_updated:
                queue_unique(state.setdefault("enrichmentQueue", []), bot_id)
            if "definition_visible" in doc and doc.get("definition_visible") != old_definition_visible:
                queue_unique(state.setdefault("enrichmentQueue", []), bot_id)
        avatar = normalize_avatar_url(doc.get("avatar_url") or doc.get("avatar") or doc.get("image"))
        if avatar and (existing is None or avatar != old_avatar):
            queue_unique(state.setdefault("imageQueue", []), bot_id)
        if changed and save_bot(record):
            changed_count += 1
    return new_count, changed_count, seen


def scan_listing(client: Client, config: dict[str, Any], name: str, sort_by: str, at: str, state: dict[str, Any]) -> dict[str, Any]:
    page_size = min(250, int(config["crawler"].get("listing_page_size", 250)))
    max_hits = int(config["crawler"].get("listing_max_hits", 2500))
    docs: list[dict[str, Any]] = []
    found = None
    page = 1
    while len(docs) < max_hits:
        req = typesense_search(config, page=page, per_page=min(page_size, max_hits - len(docs)), sort_by=sort_by)
        response = client.multi_search([req])
        if not response.ok:
            return {"ok": False, "error": response.error, "status": response.status, "count": len(docs), "ids": []}
        result = (response.data.get("results") or [{}])[0]
        hits, found_now = extract_hits(result)
        if found is None:
            found = found_now
        if not hits:
            break
        docs.extend(hits)
        if len(hits) < req["per_page"]:
            break
        page += 1

    new, changed, _ = ingest_documents(docs, source=f"typesense:{name}", at=at, state=state)
    ids = [normalize_id(d) for d in docs]
    ids = [x for x in ids if x]
    # Annotate per-bot listing rank only when changed.
    for rank, bot_id in enumerate(ids, 1):
        record = load_bot(bot_id)
        if not record:
            continue
        listing = record.setdefault("listings", {}).get(name) or {}
        next_listing = {"rank": rank, "observedAt": at}
        if listing.get("rank") != rank:
            record.setdefault("listings", {})[name] = next_listing
            save_bot(record)
        else:
            # Avoid touching the bot file just to refresh observedAt.
            record.setdefault("listings", {})[name] = listing

    return {"ok": True, "found": found, "count": len(ids), "new": new, "changed": changed, "ids": ids}


def save_ranking_snapshot(listings: dict[str, Any], at: str) -> bool:
    compact = {
        "schemaVersion": 1,
        "capturedAt": at,
        "listings": {name: info.get("ids", []) for name, info in listings.items() if info.get("ok")},
    }
    # Ranking files are only written if the current rank arrays differ from the newest prior snapshot.
    latest_path = RANKINGS_DIR / "latest.json"
    prior = read_json(latest_path, {})
    if prior.get("listings") == compact["listings"]:
        return False
    safe = at.replace(":", "").replace("-", "")
    write_json_if_changed(RANKINGS_DIR / f"{safe}.json", compact, indent=None)
    write_json_if_changed(latest_path, compact, indent=None)
    return True


def complete_exploration_pass(state: dict[str, Any], at: str) -> int:
    """Queue public bots absent from a completed wildcard pass for explicit verification."""
    exploration = state.setdefault("exploration", {})
    seen = set(exploration.get("seenThisPass") or [])
    queued = 0
    if seen:
        checks = state.setdefault("missingChecks", {})
        for path in iter_bot_paths():
            record = read_json(path, {})
            bot_id = record.get("id")
            if not bot_id or bot_id in seen:
                continue
            if record.get("status", {}).get("current") == "public":
                if bot_id not in checks:
                    checks[bot_id] = {"count": 0, "lastAt": None, "lastStatus": None, "reason": "absent-from-complete-typesense-pass", "queuedAt": at}
                    queued += 1
    exploration["seenThisPass"] = []
    return queued


def explore_more(client: Client, config: dict[str, Any], at: str, state: dict[str, Any]) -> dict[str, Any]:
    """Resume wildcard discovery. If a page cap is hit, attempt createdAt keyset fallback."""
    exploration = state.setdefault("exploration", {})
    mode = exploration.get("mode") or "page"
    page_size = 250
    pages_budget = int(config["crawler"].get("explore_pages_per_run", 8))
    total_new = total_changed = total_hits = 0
    errors: list[str] = []
    missing_queued = 0
    seen_this_pass = set(exploration.get("seenThisPass") or [])

    for _ in range(pages_budget):
        if mode == "cursor" and exploration.get("cursorCreatedAt"):
            cursor = exploration["cursorCreatedAt"]
            base_filter = config["typesense"]["application_filter"]
            # Works when createdAt is a numeric/filterable field. Probe report will expose if unsupported.
            filter_by = f"{base_filter} && createdAt:<{cursor}"
            req = typesense_search(config, page=1, per_page=page_size, sort_by="createdAt:desc", filter_by=filter_by)
        else:
            page = int(exploration.get("page") or 1)
            req = typesense_search(config, page=page, per_page=page_size, sort_by="createdAt:desc")

        response = client.multi_search([req])
        if not response.ok:
            errors.append(response.error or f"HTTP {response.status}")
            break
        result = (response.data.get("results") or [{}])[0]
        hits, found = extract_hits(result)
        exploration["lastFound"] = found

        if not hits:
            if mode == "page" and found and (int(exploration.get("page") or 1) - 1) * page_size < found:
                # Search-key result cap. Try keyset pagination from the oldest createdAt we have seen.
                exploration["blockedAtResultCap"] = True
                cursor = exploration.get("lastCreatedAt")
                if cursor is not None:
                    mode = exploration["mode"] = "cursor"
                    exploration["cursorCreatedAt"] = cursor
                    continue
            # Completed a pass (or cursor strategy exhausted). Start over next run to discover newly-added bots.
            exploration["seenThisPass"] = sorted(seen_this_pass)
            missing_queued += complete_exploration_pass(state, at)
            seen_this_pass.clear()
            exploration["pass"] = int(exploration.get("pass") or 0) + 1
            exploration["page"] = 1
            exploration["mode"] = mode = "page"
            exploration["cursorCreatedAt"] = None
            exploration["lastCreatedAt"] = None
            break

        new, changed, seen_ids = ingest_documents(hits, source="typesense:explore", at=at, state=state)
        seen_this_pass.update(seen_ids)
        exploration["seenThisPass"] = sorted(seen_this_pass)
        total_new += new
        total_changed += changed
        total_hits += len(hits)
        last_created = hits[-1].get("createdAt")
        if last_created is not None:
            exploration["lastCreatedAt"] = last_created

        if mode == "cursor":
            if last_created is None or last_created == exploration.get("cursorCreatedAt"):
                errors.append("cursor discovery could not advance createdAt")
                break
            exploration["cursorCreatedAt"] = last_created
        else:
            exploration["page"] = int(exploration.get("page") or 1) + 1

        if len(hits) < page_size:
            exploration["seenThisPass"] = sorted(seen_this_pass)
            missing_queued += complete_exploration_pass(state, at)
            seen_this_pass.clear()
            exploration["pass"] = int(exploration.get("pass") or 0) + 1
            exploration["page"] = 1
            exploration["mode"] = mode = "page"
            exploration["cursorCreatedAt"] = None
            exploration["lastCreatedAt"] = None
            break

    exploration["seenThisPass"] = sorted(seen_this_pass)
    return {"hits": total_hits, "new": total_new, "changed": total_changed, "mode": mode, "missingQueued": missing_queued, "errors": errors}


def unwrap_character_payload(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    for key in ("character", "data", "result"):
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return data


def enrich_queue(client: Client, config: dict[str, Any], at: str, state: dict[str, Any]) -> dict[str, int]:
    queue = state.setdefault("enrichmentQueue", [])
    budget = int(config["crawler"].get("enrichment_budget", 200))
    processed = enriched = missing = restricted = 0
    keep: list[str] = []

    for bot_id in queue:
        if processed >= budget:
            keep.append(bot_id)
            continue
        processed += 1
        response = client.character(bot_id)
        if response.ok:
            payload = unwrap_character_payload(response.data)
            if payload:
                payload.setdefault("character_id", bot_id)
                record = load_bot(bot_id)
                record, changed = observe_bot(record, payload, source="character-api", at=at)
                if changed:
                    save_bot(record)
                enriched += 1
            continue
        if response.status == 404:
            missing += 1
            # One enrichment 404 does not delete a freshly discovered bot; queue a later verification.
            state.setdefault("missingChecks", {}).setdefault(bot_id, {"count": 0, "lastAt": None, "lastStatus": None})
            continue
        if response.status in {401, 403}:
            restricted += 1
            record = load_bot(bot_id)
            if record:
                current = record.get("status", {}).get("current")
                if current == "public":
                    # Do not demote an indexed public bot just because richer endpoint access is restricted.
                    record.setdefault("sources", {})["character-api"] = {"lastAttemptAt": at, "httpStatus": response.status}
                    save_bot(record)
            continue
        keep.append(bot_id)  # transient error; retry later.

    state["enrichmentQueue"] = keep
    return {"processed": processed, "enriched": enriched, "missing": missing, "restricted": restricted}


def archive_images(client: Client, config: dict[str, Any], at: str, state: dict[str, Any]) -> dict[str, int]:
    if not config["crawler"].get("image_archive_enabled", True):
        return {"processed": 0, "saved": 0, "failed": 0}
    queue = state.setdefault("imageQueue", [])
    budget = int(config["crawler"].get("image_budget", 100))
    keep: list[str] = []
    processed = saved = failed = 0

    for bot_id in queue:
        if processed >= budget:
            keep.append(bot_id)
            continue
        processed += 1
        record = load_bot(bot_id)
        if not record:
            continue
        last = record.get("lastKnown") or {}
        url = normalize_avatar_url(last.get("avatar_url") or last.get("avatar") or last.get("image"))
        if not url:
            continue
        existing = record.get("avatarArchive") or {}
        if existing.get("originalUrl") == url and existing.get("path") and (ROOT / existing["path"]).exists():
            continue
        result = client.image(url)
        if not result:
            failed += 1
            keep.append(bot_id)
            continue
        content, ctype = result
        digest = hashlib.sha256(content).hexdigest()
        ext = mimetypes.guess_extension(ctype) or Path(urlparse(url).path).suffix or ".img"
        if ext == ".jpe":
            ext = ".jpg"
        rel = Path("archive") / "media" / digest[:2] / f"{digest}{ext}"
        target = ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
        record["avatarArchive"] = {
            "originalUrl": url,
            "sha256": digest,
            "contentType": ctype,
            "bytes": len(content),
            "path": rel.as_posix(),
            "archivedAt": at,
        }
        save_bot(record)
        saved += 1

    state["imageQueue"] = keep
    return {"processed": processed, "saved": saved, "failed": failed}


def verify_missing(client: Client, config: dict[str, Any], at: str, state: dict[str, Any], seen_public: set[str]) -> dict[str, int]:
    """Verify only already-suspect bots. Missing from a listing alone never means deleted."""
    checks = state.setdefault("missingChecks", {})
    required = max(2, int(config["crawler"].get("deleted_confirmations_required", 2)))
    verified = deleted = restored = 0

    for bot_id in list(checks)[:100]:
        info = checks[bot_id]
        if bot_id in seen_public:
            checks.pop(bot_id, None)
            restored += 1
            continue
        response = client.character(bot_id)
        verified += 1
        if response.ok:
            payload = unwrap_character_payload(response.data)
            if payload:
                payload.setdefault("character_id", bot_id)
                record = load_bot(bot_id)
                record, _ = observe_bot(record, payload, source="character-api", at=at)
                save_bot(record)
            checks.pop(bot_id, None)
            restored += 1
            continue
        if response.status == 404:
            info["count"] = int(info.get("count") or 0) + 1
            info["lastAt"] = at
            info["lastStatus"] = 404
            if info["count"] >= required:
                record = load_bot(bot_id)
                if record and record.get("status", {}).get("current") != "deleted":
                    record.setdefault("availabilityHistory", []).append({"status": "deleted", "from": at, "source": "character-api:repeated-404"})
                    record["status"] = {"current": "deleted", "since": at, "lastVerifiedAt": at, "evidence": f"{info['count']} repeated public character API 404s"}
                    save_bot(record)
                    deleted += 1
                checks.pop(bot_id, None)
            continue
        info["lastAt"] = at
        info["lastStatus"] = response.status

    return {"verified": verified, "deleted": deleted, "restored": restored}


def compact_summary(record: dict[str, Any]) -> dict[str, Any]:
    lk = record.get("lastKnown") or {}
    status = record.get("status") or {}
    metrics = (record.get("metrics") or {}).get("latest") or {}
    avatar = record.get("avatarArchive") or {}
    archived_rel = avatar.get("path")
    site_media = None
    if archived_rel:
        # Root media/ mirrors archive/media for the public website.
        parts = Path(archived_rel).parts
        try:
            idx = parts.index("media")
            site_media = "/media/" + "/".join(parts[idx + 1:])
        except ValueError:
            site_media = None
    return {
        "id": record["id"],
        "name": lk.get("name") or lk.get("title") or "Unknown bot",
        "title": lk.get("title") or "",
        "creator": lk.get("creator_username") or lk.get("creator") or "",
        "tags": lk.get("tags") if isinstance(lk.get("tags"), list) else [],
        "status": status.get("current") or "unknown",
        "statusSince": status.get("since"),
        "firstSeenAt": record.get("firstSeenAt"),
        "lastSeenAt": record.get("lastSeenAt"),
        "isNsfw": bool(lk.get("is_nsfw") or lk.get("avatar_is_nsfw")),
        "avatar": site_media or normalize_avatar_url(lk.get("avatar_url") or lk.get("avatar") or lk.get("image")),
        "avatarArchived": bool(site_media),
        "messages": metrics.get("num_messages"),
        "messages24h": metrics.get("num_messages_24h"),
        "rating": metrics.get("rating_score"),
        "createdAt": lk.get("createdAt"),
        "updatedAt": lk.get("updatedAt"),
        "listings": {k: v.get("rank") for k, v in (record.get("listings") or {}).items() if isinstance(v, dict) and v.get("rank") is not None},
    }


def build_site_data(at: str, listings: dict[str, Any] | None = None) -> dict[str, Any]:
    SITE_CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    SITE_BOTS_DIR.mkdir(parents=True, exist_ok=True)
    # Clear generated catalog shards; keep deterministic output.
    for p in SITE_CATALOG_DIR.glob("*.json"):
        p.unlink()

    shards: dict[str, list[dict[str, Any]]] = {}
    counts = {"public": 0, "deleted": 0, "restricted": 0, "missing": 0, "unknown": 0}
    tags: dict[str, int] = {}
    total = 0

    for path in iter_bot_paths():
        record = read_json(path, {})
        if not record.get("id"):
            continue
        total += 1
        summary = compact_summary(record)
        shards.setdefault(shard_for(record["id"]), []).append(summary)
        counts[summary["status"]] = counts.get(summary["status"], 0) + 1
        for tag in summary.get("tags") or []:
            text = str(tag).strip()
            if text:
                tags[text] = tags.get(text, 0) + 1

        # Detailed page data. Copy the full archival record.
        write_json_if_changed(SITE_BOTS_DIR / f"{record['id']}.json", record, indent=None)

    shard_names = []
    for name in sorted(shards):
        items = sorted(shards[name], key=lambda x: ((x.get("name") or "").casefold(), x["id"]))
        write_json_if_changed(SITE_CATALOG_DIR / f"{name}.json", {"bots": items}, indent=None)
        shard_names.append(name)

    # Mirror archived media into the deployed site tree. Hardlink where possible to avoid local duplication.
    site_media = SITE_DIR / "media"
    if site_media.exists():
        shutil.rmtree(site_media)
    source_media = MEDIA_DIR
    if source_media.exists():
        shutil.copytree(source_media, site_media, copy_function=os.link if hasattr(os, "link") else shutil.copy2, dirs_exist_ok=True)

    ranking_latest = read_json(RANKINGS_DIR / "latest.json", {})
    manifest = {
        "schemaVersion": 1,
        "generatedAt": at,
        "totalBots": total,
        "activeBots": counts.get("public", 0),
        "deletedBots": counts.get("deleted", 0),
        "restrictedBots": counts.get("restricted", 0),
        "missingBots": counts.get("missing", 0),
        "shards": shard_names,
        "tagCount": len(tags),
        "topTags": sorted(({"tag": k, "count": v} for k, v in tags.items()), key=lambda x: (-x["count"], x["tag"].casefold()))[:1000],
        "listings": {k: v.get("ids", []) for k, v in (listings or {}).items() if v.get("ok")} or ranking_latest.get("listings", {}),
        "lastScan": at,
    }
    write_json_if_changed(SITE_DATA_DIR / "manifest.json", manifest)
    return manifest


def seed_missing_checks_from_full_pass(state: dict[str, Any], all_seen: set[str]) -> None:
    """After a completed/near-complete discovery pass, queue known public bots absent from it for verification.

    This intentionally requires the caller to provide a broad wildcard seen set. Listing absence alone is ignored.
    """
    if not all_seen:
        return
    for path in iter_bot_paths():
        record = read_json(path, {})
        bot_id = record.get("id")
        if not bot_id or bot_id in all_seen:
            continue
        if record.get("status", {}).get("current") == "public":
            state.setdefault("missingChecks", {}).setdefault(bot_id, {"count": 0, "lastAt": None, "lastStatus": None})


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-only", action="store_true", help="Do not mutate archive; use scripts/probe.py instead for detailed probe")
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--summary-file")
    args = parser.parse_args()

    config = load_config()
    if args.no_images:
        config["crawler"]["image_archive_enabled"] = False
    at = utc_now()
    state = read_json(STATE_PATH, {"schemaVersion": 1})
    client = Client(config)

    listing_results: dict[str, Any] = {}
    public_seen: set[str] = set()
    for name, sort_by in config.get("listings", {}).items():
        info = scan_listing(client, config, name, sort_by, at, state)
        listing_results[name] = info
        public_seen.update(info.get("ids") or [])
        print(f"{name}: ok={info.get('ok')} count={info.get('count')} new={info.get('new', 0)} changed={info.get('changed', 0)}")

    ranking_changed = save_ranking_snapshot(listing_results, at)
    exploration = explore_more(client, config, at, state)
    enrichment = enrich_queue(client, config, at, state)
    images = archive_images(client, config, at, state)
    missing = verify_missing(client, config, at, state, public_seen)

    state["lastRunAt"] = at
    state["lastRunSummary"] = {
        "listings": {k: {x: v.get(x) for x in ("ok", "found", "count", "new", "changed", "error") if x in v} for k, v in listing_results.items()},
        "rankingChanged": ranking_changed,
        "exploration": exploration,
        "enrichment": enrichment,
        "images": images,
        "missingVerification": missing,
    }
    write_json_if_changed(STATE_PATH, state)
    manifest = build_site_data(at, listing_results)

    summary = [
        f"# SpicyChat Archive run — {at}",
        "",
        f"- Catalog: **{manifest['totalBots']:,}** bots ({manifest['activeBots']:,} public, {manifest['deletedBots']:,} deleted)",
        f"- Exploration: **{exploration['hits']:,}** hits, **{exploration['new']:,}** new, mode `{exploration['mode']}`",
        f"- Enrichment: **{enrichment['enriched']:,}** enriched / {enrichment['processed']:,} attempted",
        f"- Images: **{images['saved']:,}** archived / {images['processed']:,} attempted",
        f"- Deleted confirmations this run: **{missing['deleted']:,}**",
        f"- Ranking snapshot changed: **{'yes' if ranking_changed else 'no'}**",
    ]
    if exploration.get("errors"):
        summary += ["", "## Exploration warnings", *[f"- {e}" for e in exploration["errors"]]]
    text = "\n".join(summary) + "\n"
    print(text)
    if args.summary_file:
        Path(args.summary_file).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
