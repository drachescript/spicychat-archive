#!/usr/bin/env python3
"""Deterministic Archive franchise preset discovery; never activate guesses."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "block-presets" / "catalog.json"
SKIP = {"roleplay", "story", "the", "your", "chat", "scenario", "bot",
        "oc", "original", "female", "male", "girlfriend", "boyfriend",
        "wife", "husband", "friend", "enemy", "new", "role", "character"}


def norm(value: Any) -> str:
    source = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
    source = unicodedata.normalize("NFKD", source).casefold()
    source = "".join(c for c in source if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", source).split())


@lru_cache(maxsize=2048)
def compiled(value: str) -> re.Pattern[str]:
    v = norm(value)
    if not v:
        raise ValueError("Empty alias")
    return re.compile(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])")


def phrase_in(text: str, value: str) -> bool:
    return bool(compiled(value).search(text))


def validate(catalog: dict[str, Any]) -> None:
    if catalog.get("schemaVersion") != 1 or catalog.get("contract") != "spicychat-archive-block-presets-v1":
        raise ValueError("Unexpected preset catalog version")
    presets, chars, groups = (catalog.get(k) for k in ("presets", "characters", "groups"))
    if not all(isinstance(v, list) for v in (presets, chars, groups)):
        raise ValueError("Missing arrays in preset catalog")
    identities = [item["id"] for entries in (presets, chars, groups) for item in entries]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate catalog IDs")
    preset_ids = {p["id"] for p in presets}
    character_ids = {c["id"] for c in chars}
    group_ids = {g["id"] for g in groups}
    for p in presets:
        if p.get("enabledByDefault") is not False or p.get("kind") not in {"franchise", "theme"}:
            raise ValueError("Presets must remain opt-in")
        if not set(p.get("characterIds") or []) <= character_ids or not set(p.get("groupIds") or []) <= group_ids:
            raise ValueError("Invalid preset references")
        if any(norm(a["text"] if isinstance(a, dict) else a) == "theory"
               for a in p.get("aliases") or []):
            raise ValueError("theory was not a blocked term")
    for c in chars:
        if c.get("gender") not in {"female", "male", "nonbinary", "unknown"}:
            raise ValueError("Unknown canonical gender label")
        if "franchise:" + c["franchiseId"] not in preset_ids:
            raise ValueError("Invalid character franchise")
        if any(a.get("strength") not in {"strong", "contextual"} for a in c["aliases"]):
            raise ValueError("Malformed aliases")
    for g in groups:
        if "franchise:" + g["franchiseId"] not in preset_ids or not set(g["memberIds"]) <= character_ids:
            raise ValueError("Invalid group members")


def candidate_suffix(title: str, preset: dict[str, Any]) -> str | None:
    # Review-only suggestions after explicit multi-word franchise references.
    for a in preset["aliases"]:
        if a["strength"] != "strong" or len(a["text"]) < 7:
            continue
        m = re.search(re.escape(a["text"]) + r"\s*[:|–—-]\s*([^|:–—-]{4,70})",
                      title, flags=re.IGNORECASE)
        if not m:
            continue
        text = norm(m.group(1))
        words = text.split()
        if 1 <= len(words) <= 4 and 5 <= len(text) <= 48 and not any(
                w in SKIP or w.isdecimal() for w in words):
            return text
    return None


def collect(shards: Iterable[dict[str, Any]], catalog: dict[str, Any]) -> dict[str, Any]:
    validate(catalog)
    franchises = [p for p in catalog["presets"] if p["kind"] == "franchise"]
    by_franchise = defaultdict(list)
    group_by_franchise = defaultdict(list)
    for c in catalog["characters"]:
        by_franchise[c["franchiseId"]].append(c)
    for g in catalog["groups"]:
        group_by_franchise[g["franchiseId"]].append(g)
    seen = set()
    totals = Counter()
    characters = Counter()
    candidates = defaultdict(Counter)
    examined = 0
    for shard in shards:
        if not isinstance(shard, dict):
            raise ValueError("Malformed creator shard")
        for creator in shard.values():
            if not isinstance(creator, dict):
                continue
            for bot_id, meta in (creator.get("bots") or {}).items():
                if bot_id in seen or not isinstance(meta, dict):
                    continue
                seen.add(bot_id)
                examined += 1
                raw = str(meta.get("title") or "")[:240]
                title = norm(raw)
                if not title:
                    continue
                for p in franchises:
                    strong = [a["text"] for a in p["aliases"] if a["strength"] == "strong"]
                    explicit = any(phrase_in(title, alias) for alias in strong)
                    c_hits = [c for c in by_franchise[p["franchiseId"]]
                              if any(phrase_in(title, a["text"]) for a in c["aliases"]
                                     if a["strength"] == "strong")]
                    g_hit = any(any(phrase_in(title, alias) for alias in group["aliases"])
                                for group in group_by_franchise[p["franchiseId"]])
                    if not (explicit or c_hits or g_hit):
                        continue
                    totals[p["id"]] += 1
                    for c in c_hits:
                        characters[c["id"]] += 1
                    if explicit:
                        possible = candidate_suffix(raw, p)
                        if possible and not any(phrase_in(possible, a["text"])
                                                for c in by_franchise[p["franchiseId"]]
                                                for a in c["aliases"]):
                            candidates[p["id"]][possible] += 1
    suggestions = [
        {"presetId": preset, "candidateText": candidate,
         "observedTitleCount": count, "status": "review-needed"}
        for preset, counts in candidates.items()
        for candidate, count in counts.items() if count >= 3
    ]
    suggestions.sort(key=lambda x: (-x["observedTitleCount"], x["presetId"], x["candidateText"]))
    return {
        "schemaVersion": 1, "catalogVersion": catalog["catalogVersion"],
        "coverage": {"creatorIndexedBotsExamined": examined,
                     "franchiseMentions": sum(totals.values()),
                     "source": "Public, creator-indexed Archive titles including historical bots",
                     "unknownDoesNotMeanSafe": True},
        "presetMentions": dict(sorted(totals.items())),
        "characterMentions": dict(sorted(characters.items())),
        "reviewCandidates": suggestions[:200],
        "reviewNotice": "Candidates are never automatically trusted or added as active aliases.",
        "method": "Deterministic string rules on archived public titles; no AI or per-bot requests"
    }


def publish(store: Any, payload: dict[str, Any]) -> dict[str, Any]:
    key = store.key("indexes", "block-presets", "discovery.json")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    previous = store.get_json(key, None)
    if isinstance(previous, dict) and previous.get("contentHash") == signature:
        print("Block presets: no observed changes; skipped write", flush=True)
        return previous
    output = {**payload, "contentHash": signature,
              "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    store.put_json(key, output, public=True)
    store.flush_usage(force=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    validate(catalog)
    import archive
    from publish_name_trends import _read_shards, CREATOR_SHARDS
    from storage_r2 import R2ArchiveStore
    if not R2ArchiveStore.credentials_present():
        print("Block presets: missing R2 credentials", flush=True)
        return 1
    store = R2ArchiveStore(archive.load_config())
    if not store.is_migrated():
        print("Block presets: Archive not migrated", flush=True)
        return 1
    payload = collect(_read_shards(store, "creators", CREATOR_SHARDS), catalog)
    print("Block presets: " + format(payload["coverage"]["creatorIndexedBotsExamined"], ",") +
          " indexed bots, " + format(payload["coverage"]["franchiseMentions"], ",") +
          " franchise mentions", flush=True)
    if not args.dry_run:
        publish(store, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
