#!/usr/bin/env python3
"""Publish sparse, explainable public-theme signals from existing creator indexes.

This reads 256 compact creator shards, NOT every individual archived bot file.
No AI, user chats, authenticated private definitions or direct SpicyChat calls.
Rule hits are CANDIDATES; only explicit reviewed decisions are verified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "community-tags"
UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")
NIBBLES = "0123456789abcdef"
SCHEMA = 1
CREATOR_SHARDS = 256


def load_json(name: str) -> dict[str, Any]:
    payload = json.loads((DATA / name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA:
        raise ValueError("Unsupported community-tags file: " + name)
    return payload


def canonical(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", " ", "".join(
        ch for ch in text if not unicodedata.combining(ch)
    )).strip()


def tag_id(value: Any) -> str:
    return canonical(value).replace(" ", "-")


def digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def metadata_fingerprint(meta: dict[str, Any]) -> str:
    """Review decisions are scoped to the latest publicly archived metadata."""
    tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
    return digest({
        "title": str(meta.get("title") or ""),
        "name": str(meta.get("name") or ""),
        "tags": sorted({canonical(x) for x in tags if isinstance(x, str)}),
    })[:24]


def phrase_in(text: str, phrase: str) -> bool:
    term = canonical(phrase)
    return bool(term and re.search(r"(?<![a-z0-9])" + re.escape(term)
                                   + r"(?![a-z0-9])", text))


def validate(taxonomy: dict[str, Any], rules: dict[str, Any],
             reviews: dict[str, Any]) -> set[str]:
    tags = taxonomy.get("tags")
    if not isinstance(tags, list) or not tags:
        raise ValueError("Missing tag taxonomy")
    ids = set()
    for item in tags:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("Malformed taxonomy tag")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item["id"]):
            raise ValueError("Invalid tag ID")
        if not set(item.get("scopes") or []) <= {"bot", "lorebook", "rulebook"}:
            raise ValueError("Invalid tag scope")
        if item["id"] in ids:
            raise ValueError("Duplicate taxonomy tag")
        ids.add(item["id"])
    for rule in rules.get("rules") or []:
        if not isinstance(rule, dict) or rule.get("tagId") not in ids:
            raise ValueError("Unknown rule theme")
        groups = rule.get("allOf")
        if rule.get("fields") != ["title"] or not isinstance(groups, list) or not groups:
            raise ValueError("Only explicit title-only deterministic rules are supported")
        if any(not isinstance(group, list) or not group or
               any(not isinstance(term, str) or len(term) > 90 for term in group)
               for group in groups):
            raise ValueError("Invalid rule phrase group")
        if any(not isinstance(term, str) or len(term) > 90
               for term in rule.get("noneOf") or []):
            raise ValueError("Invalid negative rule")
    if not isinstance(reviews.get("decisions"), list):
        raise ValueError("Invalid review decisions")
    for decision in reviews["decisions"]:
        if not isinstance(decision, dict) or decision.get("tagId") not in ids:
            raise ValueError("Invalid reviewed theme")
        if not UUID.fullmatch(str(decision.get("botId") or "")):
            raise ValueError("Invalid reviewed bot ID")
        if decision.get("decision") not in {"approve", "reject"}:
            raise ValueError("Review must approve or reject")
        if not re.fullmatch(r"[0-9a-f]{24}", str(decision.get("fingerprint") or "")):
            raise ValueError("Review must include exact public metadata fingerprint")
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                            str(decision.get("reviewedAt") or "")):
            raise ValueError("Review timestamp must use UTC ISO 8601")
        if not str(decision.get("evidenceUrl") or "").startswith(
                ("https://spicychatarchive.drache.uk/", "https://spicychat.ai/")):
            raise ValueError("Approved evidence must be a public source URL")
    return ids


def scan_meta(bot_id: str, meta: dict[str, Any], rules: dict[str, Any],
              bot_ids: set[str], decisions: dict[tuple[str, str], dict[str, Any]]
              ) -> dict[str, Any] | None:
    if not UUID.fullmatch(bot_id) or not isinstance(meta, dict):
        return None
    title = canonical(meta.get("title"))
    tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
    current = {tag_id(t) for t in tags if isinstance(t, str)}
    old = meta.get("historicalTags") if isinstance(meta.get("historicalTags"), list) else []
    fingerprint = metadata_fingerprint(meta)
    signals: list[dict[str, Any]] = []
    for previous in old:
        former = tag_id(previous)
        if former in bot_ids and former not in current and not any(
            row["tagId"] == former and row["kind"] == "historical-tag" for row in signals
        ):
            signals.append({"tagId": former, "kind": "historical-tag",
                            "confidence": "historical-only",
                            "reason": "Previously observed public tag; not in last-known tags"})
    if title:
        for rule in rules.get("rules") or []:
            tag = rule["tagId"]
            if tag not in bot_ids or tag in current:
                continue
            if not all(any(phrase_in(title, phrase) for phrase in group)
                       for group in rule["allOf"]):
                continue
            if any(phrase_in(title, phrase) for phrase in rule.get("noneOf") or []):
                continue
            signals.append({"tagId": tag, "kind": "rule-candidate",
                            "confidence": "candidate", "ruleId": rule["id"],
                            "reason": rule.get("reason") or "Explicit public title cue"})
    for (id_, theme), decision in decisions.items():
        if id_ != bot_id or decision["fingerprint"] != fingerprint:
            continue
        if decision["decision"] == "reject":
            signals = [s for s in signals if not (
                s["tagId"] == theme and s["kind"] == "rule-candidate")]
        elif decision["decision"] == "approve":
            signals = [s for s in signals if not (
                s["tagId"] == theme and s["kind"] == "rule-candidate")]
            signals.append({"tagId": theme, "kind": "reviewed",
                            "confidence": "verified",
                            "reason": "Reviewed public premise",
                            "reviewedAt": decision["reviewedAt"],
                            "evidenceUrl": decision["evidenceUrl"]})
    if not signals:
        return None
    status = str(meta.get("status") or "unknown").lower()
    if status not in {"public", "deleted", "not-public", "unknown", "unavailable"}:
        status = "unknown"
    return {"status": status, "lastSeenAt": meta.get("lastSeenAt") or None,
            "fingerprint": fingerprint,
            "signals": sorted(signals, key=lambda s: (s["tagId"], s["kind"]))}


def build(creator_shards: Iterable[dict[str, Any]], taxonomy: dict[str, Any],
          rules: dict[str, Any], reviews: dict[str, Any]
          ) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    validate(taxonomy, rules, reviews)
    bot_ids = {t["id"] for t in taxonomy["tags"] if "bot" in t["scopes"]}
    decisions = {(d["botId"], d["tagId"]): d for d in reviews["decisions"]}
    seen: set[str] = set()
    signal_bots: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for shard in creator_shards:
        if not isinstance(shard, dict):
            raise ValueError("Invalid creator shard")
        for creator in shard.values():
            if not isinstance(creator, dict):
                continue
            for id_, meta in (creator.get("bots") or {}).items():
                bot_id = str(id_).lower()
                if not UUID.fullmatch(bot_id) or bot_id in seen:
                    continue
                seen.add(bot_id)
                counts["indexedBots"] += 1
                record = scan_meta(bot_id, meta, rules, bot_ids, decisions)
                if not record:
                    continue
                signal_bots[bot_id] = record
                for row in record["signals"]:
                    counts[row["kind"]] += 1
    counts["signalBots"] = len(signal_bots)
    counts["reviewDecisions"] = len(reviews["decisions"])
    return signal_bots, dict(counts)


def partitions(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    shards: dict[str, dict[str, Any]] = {n: {} for n in NIBBLES}
    for bot_id, record in records.items():
        shards[bot_id[0]][bot_id] = record
    return shards


def publish(store: Any, records: dict[str, dict[str, Any]],
            counts: dict[str, int], rules: dict[str, Any]) -> dict[str, Any]:
    shards = partitions(records)
    hash_payload = {"shards": shards, "rulesVersion": rules["rulesVersion"]}
    signature = digest(hash_payload)
    key = store.key("indexes", "community-tags", "manifest.json")
    existing = store.get_json(key, None)
    if isinstance(existing, dict) and existing.get("contentHash") == signature:
        print("Community tags: no changes; preserving existing immutable snapshot.", flush=True)
        return existing
    snapshot = signature[:20]
    prefix = f"indexes/community-tags/snapshots/{snapshot}"
    # Immutable content-addressed snapshot: no reader can mix old/new shards.
    for nibble, bots in shards.items():
        store.put_json(store.key(prefix, nibble + ".json"), {
            "schemaVersion": SCHEMA, "snapshot": snapshot, "bots": bots,
        }, public=True, category="data")
    manifest = {
        "schemaVersion": SCHEMA,
        "contract": "spicychat-archive-community-tags-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "contentHash": signature,
        "snapshot": snapshot,
        "shardPrefix": prefix,
        "shards": len(NIBBLES),
        "shardMethod": "first hexadecimal character of UUID",
        "complete": True,
        "coverage": counts,
        "notes": "Sparse, title-only candidates and previous public tags. No match means UNKNOWN, not safe.",
    }
    store.put_json(key, manifest, public=True, category="data")
    store.flush_usage(force=True)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    taxonomy, rules, reviews = (load_json(x) for x in
                                ("taxonomy.json", "rules.json", "reviews.json"))
    validate(taxonomy, rules, reviews)
    import archive
    from publish_name_trends import CREATOR_SHARDS, _read_shards
    from storage_r2 import R2ArchiveStore
    if not R2ArchiveStore.credentials_present():
        print("Community tags: R2 credentials absent; nothing published.")
        return 1
    store = R2ArchiveStore(archive.load_config())
    if not store.is_migrated():
        print("Community tags: Archive R2 migration incomplete; nothing published.")
        return 1
    records, coverage = build(
        _read_shards(store, "creators", CREATOR_SHARDS), taxonomy, rules, reviews)
    print(f"Community tags: {coverage.get('indexedBots',0):,} indexed bots, "
          f"{coverage.get('signalBots',0):,} with sparse signals.", flush=True)
    if args.dry_run:
        print("Community tags: dry run only; no R2 writes.")
        return 0
    manifest = publish(store, records, coverage, rules)
    print("Community tags: published snapshot " + manifest["snapshot"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
