#!/usr/bin/env python3
"""Publish compact, auditable Archive name + title-pattern statistics.

Uses already-published site-index shards, never individual bot files.
The name index covers bots represented in 64 text-search shards; titles and
premise words cover bots represented in 256 creator shards. The title patterns
are NOT interpretations of private/exact scenario text.
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

import archive as legacy
from site_indexes import SCHEMA_VERSION as SOURCE_SCHEMA, _fnv1a32
from storage_r2 import R2ArchiveStore

NAME_SHARDS = 64
CREATOR_SHARDS = 256
SCHEMA_VERSION = 1
MINIMUM_TITLE_LENGTH = 4
# Clearly labelled TITLE clues: categories overlap by design.
TITLE_SITUATIONS: dict[str, str] = {
    "Roommates / living together": r"\broom\s?mates?\b|\bliving together\b|\bshared apartment\b",
    "Best friends / longtime friends": r"\bbest friends?\b|\bchildhood friends?\b|\bbff\b",
    "Ex-partners": r"\bex[\s-]?(?:girlfriend|boyfriend|wife|husband|partner|fianc[eé]e?)\b|\byour ex\b",
    "Rivals / enemies": r"\brivals?\b|\benem(?:y|ies)\b|\benemies to lovers\b",
    "Bullying": r"\bbull(?:y|ies|ied|ying)\b",
    "Arranged / forced marriage": r"\barranged marriage\b|\bforced marriage\b|\bmarriage of convenience\b",
    "One-night encounter": r"\bone[\s-]night stand\b|\bone night\b|\bone[\s-]night fling\b",
    "Dating / blind date": r"\bblind date\b|\bfirst date\b|\bspeed dating\b",
    "Coworkers / the boss": r"\bco[\s-]?workers?\b|\boffice romance\b|\byour boss\b|\bceo\b",
    "Neighbors": r"\bneighbou?rs?\b|\bnext door\b",
    "School / university": r"\bcollege\b|\buniversity\b|\bcampus\b|\bschool\b|\bclassmate\b|\bstudents?\b",
    "Stranded / trapped": r"\bstranded\b|\btrapped\b|\bstuck together\b|\bdeserted island\b",
    "Royalty": r"\bprincess\b|\bprince\b|\bqueen\b|\bking\b|\broyalt(?:y|ies)\b",
    "Vampires": r"\bvampires?\b",
    "Werewolves": r"\bwerewolves?\b|\bwerewolf\b|\bwolf pack\b",
    "Omegaverse": r"\bomegaverse\b|\balpha[\/\s-]omega\b|\benigma\b",
    "Mafia / crime": r"\bmafia\b|\bcrime boss\b|\bgangster\b",
    "Apocalypse": r"\bapocalypse\b|\bpost[\s-]?apocalyptic\b|\bzombie\b",
    "Time travel / another world": r"\btime travel\b|\breincarnat\w*\b|\bisekai\b|\btransported to\b",
}
COMPILED_SITUATIONS = {label: re.compile(expr, re.IGNORECASE) for label, expr in TITLE_SITUATIONS.items()}


def normal_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(unicodedata.normalize("NFKC", value).split()).strip()
    if not text or text.casefold() in {"unknown bot", "unknown"}:
        return ""
    return text.lower()


def name_bucket(key: str) -> str:
    return f"{_fnv1a32(key) & (NAME_SHARDS - 1):02x}"


def normalized_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).split()).strip()


def top_rows(counter: Counter[str], display: dict[str, str], limit: int) -> list[dict[str, Any]]:
    return [
        {"value": display.get(key, key), "count": count}
        for key, count in sorted(counter.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]
    ]


def aggregate(
    text_shards: Iterable[dict[str, Any]],
    creator_shards: Iterable[dict[str, Any]],
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    names: Counter[str] = Counter()
    name_display: dict[str, str] = {}
    indexed = 0
    for shard in text_shards:
        for meta in shard.values():
            indexed += 1
            if not isinstance(meta, list) or not meta:
                continue
            key = normal_name(meta[0])
            if not key:
                continue
            names[key] += 1
            if key not in name_display:
                name_display[key] = " ".join(str(meta[0]).split()).strip()

    titles: Counter[str] = Counter()
    title_display: dict[str, str] = {}
    situation_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    title_bots = 0
    with_titles = 0
    seen_title_ids: set[str] = set()
    for shard in creator_shards:
        for creator_row in shard.values():
            if not isinstance(creator_row, dict):
                continue
            for bot_id, meta in (creator_row.get("bots") or {}).items():
                if not isinstance(meta, dict) or bot_id in seen_title_ids:
                    continue
                seen_title_ids.add(bot_id)
                title_bots += 1
                title = normalized_title(meta.get("title"))
                if title:
                    with_titles += 1
                if len(title) >= MINIMUM_TITLE_LENGTH:
                    key = title.lower()
                    titles[key] += 1
                    title_display.setdefault(key, title)
                    for label, pattern in COMPILED_SITUATIONS.items():
                        if pattern.search(title):
                            situation_counts[label] += 1
                seen_tags: set[str] = set()
                for tag in meta.get("tags") or []:
                    clean = normalized_title(tag)
                    normalized = clean.lower()
                    if normalized and normalized not in seen_tags:
                        seen_tags.add(normalized)
                        tag_counts[clean] += 1

    lookup: dict[str, dict[str, Any]] = {f"{i:02x}": {} for i in range(NAME_SHARDS)}
    for key, count in names.items():
        lookup[name_bucket(key)][key] = [name_display[key], count]

    overview = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "coverage": {
            "nameIndexedBots": indexed,
            "namedBots": sum(names.values()),
            "titleIndexedBots": title_bots,
            "botsWithTitles": with_titles,
            "nameShards": NAME_SHARDS,
            "creatorShards": CREATOR_SHARDS,
            "source": "Archive last-known bot names and creator-indexed titles",
        },
        "distinctNames": len(names),
        "oneUseNames": sum(1 for count in names.values() if count == 1),
        "topNames": top_rows(names, name_display, 100),
        "topRepeatedTitles": [
            row for row in top_rows(titles, title_display, 100) if row["count"] >= 2
        ],
        "titleSituations": [
            {"label": label, "count": int(situation_counts[label])}
            for label in TITLE_SITUATIONS
        ],
        "topTagsFromCreatorIndex": [
            {"value": value, "count": count}
            for value, count in tag_counts.most_common(40)
        ],
    }
    return overview, lookup


def _read_shards(store: R2ArchiveStore, category: str, count: int) -> Iterator[dict[str, Any]]:
    """Read small groups in parallel, releasing large shard payloads promptly."""
    for first in range(0, count, 8):
        numbers = range(first, min(first + 8, count))
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(
                lambda i: store.get_json(store.key("indexes", category, f"{i:02x}.json"), None),
                numbers,
            ))
        for i, shard in zip(numbers, rows):
            if not isinstance(shard, dict) or int(shard.get("schemaVersion") or 0) != SOURCE_SCHEMA:
                raise RuntimeError(f"Missing/incompatible {category} shard {i:02x}; stats not published")
            field = "bots" if category == "text-search" else "creators"
            data = shard.get(field)
            if not isinstance(data, dict):
                raise RuntimeError(f"Malformed {category} shard {i:02x}; stats not published")
            yield data


def publish(store: R2ArchiveStore, overview: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> None:
    # Publish shards before the manifest so clients never see an overview for
    # which a newly created lookup shard is missing.
    for bucket, names in sorted(lookup.items()):
        store.put_json(
            store.key("indexes", "name-trends", f"{bucket}.json"),
            {"schemaVersion": SCHEMA_VERSION, "names": names},
            public=True,
        )
    store.put_json(store.key("indexes", "name-trends.json"), overview, public=True)
    store.flush_usage(force=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Compute but do not write R2 statistics")
    args = ap.parse_args()
    config = legacy.load_config()
    if not R2ArchiveStore.credentials_present():
        raise RuntimeError("R2 credentials are missing")
    store = R2ArchiveStore(config)
    if not store.is_migrated():
        raise RuntimeError("R2 migration incomplete")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    overview, names = aggregate(
        _read_shards(store, "text-search", NAME_SHARDS),
        _read_shards(store, "creators", CREATOR_SHARDS),
        now,
    )
    print(
        f"Name stats: {overview['coverage']['nameIndexedBots']:,} bots indexed, "
        f"{overview['distinctNames']:,} distinct names, "
        f"{overview['coverage']['titleIndexedBots']:,} bots with creator metadata.",
        flush=True,
    )
    if not args.dry_run:
        publish(store, overview, names)
        print("Name and title-pattern stats published to public R2 indexes.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
