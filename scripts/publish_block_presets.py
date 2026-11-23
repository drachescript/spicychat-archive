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
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "block-presets" / "catalog.json"
REVIEW_LOG = ROOT / "data" / "block-presets" / "review-decisions.json"
BOT_UUID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.I)
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


def reviewed_decisions(catalog: dict[str, Any],
                       log: dict[str, Any]) -> dict[tuple[str, str], str]:
    """Only a manually reviewed catalog version can activate new aliases."""
    if log.get("schemaVersion") != 1 or not isinstance(log.get("decisions"), list):
        raise ValueError("Invalid blocking preset review log")
    franchises = {p["id"]: p for p in catalog["presets"] if p["kind"] == "franchise"}
    result = {}
    for item in log["decisions"]:
        if not isinstance(item, dict) or item.get("presetId") not in franchises:
            raise ValueError("Review refers to unknown franchise")
        text = norm(item.get("candidateText"))
        verdict = item.get("decision")
        if not text or len(text) > 60 or verdict not in {"accepted", "rejected"}:
            raise ValueError("Invalid review candidate or decision")
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                            str(item.get("reviewedAt") or "")):
            raise ValueError("Review timestamp must be UTC ISO format")
        url = urlsplit(str(item.get("evidenceUrl") or ""))
        if (url.scheme != "https" or url.hostname not in
                {"spicychat.ai", "spicychatarchive.drache.uk"} or
                url.username or url.password):
            raise ValueError("Review must reference public Archive/SpicyChat evidence")
        if not str(item.get("reason") or "").strip():
            raise ValueError("Review must explain approval or rejection")
        key = (item["presetId"], text)
        if key in result:
            raise ValueError("Duplicate review decision")
        if verdict == "accepted":
            active = {norm(alias["text"]) for alias in
                      franchises[item["presetId"]]["aliases"]}
            if text not in active:
                raise ValueError("Accepted alias must first be promoted to versioned catalog")
        result[key] = verdict
    return result


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


def collect(shards: Iterable[dict[str, Any]], catalog: dict[str, Any],
            review_log: dict[str, Any] | None = None) -> dict[str, Any]:
    validate(catalog)
    reviewed = reviewed_decisions(catalog, review_log or
                                  {"schemaVersion": 1, "decisions": []})
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
    examples: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
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
                            key = (p["id"], possible)
                            # Only publicly indexed title + bot UUID are
                            # eligible for evidence, never definitions/chats.
                            if BOT_UUID.fullmatch(str(bot_id)) and len(examples[key]) < 3:
                                examples[key].append({"botId": str(bot_id).lower(),
                                                      "title": raw[:150]})
    suggestions = [
        {"presetId": preset, "candidateText": candidate,
         "observedTitleCount": count, "status": "review-needed",
         "confidence": "unverified-pattern",
         "examples": examples[(preset, candidate)]}
        for preset, counts in candidates.items()
        for candidate, count in counts.items()
        if count >= 3 and (preset, candidate) not in reviewed
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
        "reviewLog": {"decisions": len(reviewed),
                      "accepted": sum(v == "accepted" for v in reviewed.values()),
                      "rejected": sum(v == "rejected" for v in reviewed.values())},
        "reviewNotice": "Candidates are not active. Acceptance also requires a manually reviewed, versioned catalog update.",
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
    reviews = json.loads(REVIEW_LOG.read_text(encoding="utf-8"))
    validate(catalog)
    reviewed_decisions(catalog, reviews)
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
    payload = collect(_read_shards(store, "creators", CREATOR_SHARDS),
                      catalog, reviews)
    print("Block presets: " + format(payload["coverage"]["creatorIndexedBotsExamined"], ",") +
          " indexed bots, " + format(payload["coverage"]["franchiseMentions"], ",") +
          " franchise mentions", flush=True)
    if not args.dry_run:
        publish(store, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
