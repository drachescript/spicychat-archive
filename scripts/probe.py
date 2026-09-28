#!/usr/bin/env python3
"""Non-destructive SpicyChat public API probe.

Writes probe-report.json with:
- raw Typesense field inventory
- total `q=*` hit count
- pagination behavior around the 2,500 boundary
- four listing sort tests
- public character endpoint samples
- known deleted bot + old CDN avatar tests
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests

from archive import Client, extract_hits, load_config, typesense_search, utc_now

ROOT = Path(__file__).resolve().parents[1]
KNOWN_DELETED_ID = "978e6fae-ff58-4abc-b849-321c09833843"
KNOWN_DELETED_IMAGE = "https://cdn.nd-api.com/avatars/c1e89c2d-4f5b-446e-86dd-f453501e99f7.webp?class=avatar256x256"


def sanitize_character_result(result) -> dict[str, Any]:
    return {
        "status": result.status,
        "ok": result.ok,
        "url": result.url,
        "keys": sorted(result.data.keys()) if isinstance(result.data, dict) else [],
        "data": result.data if isinstance(result.data, dict) else None,
        "error": result.error,
    }


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "probe-report.json"))
    args = parser.parse_args()

    config = load_config()
    client = Client(config)
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "capturedAt": utc_now(),
        "typesense": {},
        "listings": {},
        "characterApi": {},
        "deletedFixture": {},
    }

    # Raw global sample: intentionally no include_fields restriction.
    raw_req = typesense_search(config, page=1, per_page=10, sort_by="createdAt:desc")
    raw = client.multi_search([raw_req])
    if raw.ok:
        result = (raw.data.get("results") or [{}])[0]
        hits, found = extract_hits(result)
        all_fields = sorted({key for doc in hits for key in doc.keys()})
        report["typesense"].update({
            "ok": True,
            "endpointUsed": raw.url,
            "found": found,
            "sampleCount": len(hits),
            "fields": all_fields,
            "samples": hits[:3],
        })
    else:
        report["typesense"] = {"ok": False, "status": raw.status, "error": raw.error}
        Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return 2

    # Explicitly test common boundary pages. A public key can report a large `found`
    # while `limit_hits` prevents retrieval past a smaller number.
    boundary = {}
    for page in (1, 10, 11, 20, 100):
        req = typesense_search(config, page=page, per_page=250, sort_by="createdAt:desc")
        res = client.multi_search([req])
        if res.ok:
            rr = (res.data.get("results") or [{}])[0]
            hits, found = extract_hits(rr)
            boundary[str(page)] = {
                "ok": True,
                "count": len(hits),
                "found": found,
                "firstId": (hits and (hits[0].get("character_id") or hits[0].get("id"))) or None,
                "lastId": (hits and (hits[-1].get("character_id") or hits[-1].get("id"))) or None,
                "lastCreatedAt": (hits and hits[-1].get("createdAt")) or None,
            }
        else:
            boundary[str(page)] = {"ok": False, "status": res.status, "error": res.error}
    report["typesense"]["boundaryPages"] = boundary

    # Test the configured listing definitions.
    for name, sort_by in config.get("listings", {}).items():
        req = typesense_search(config, page=1, per_page=20, sort_by=sort_by)
        res = client.multi_search([req])
        if res.ok:
            rr = (res.data.get("results") or [{}])[0]
            hits, found = extract_hits(rr)
            report["listings"][name] = {
                "ok": True,
                "sortBy": sort_by,
                "found": found,
                "ids": [(d.get("character_id") or d.get("id")) for d in hits],
                "metricPreview": [
                    {
                        "id": d.get("character_id") or d.get("id"),
                        "name": d.get("name"),
                        "num_messages": d.get("num_messages"),
                        "num_messages_24h": d.get("num_messages_24h"),
                        "rating_score": d.get("rating_score"),
                        "createdAt": d.get("createdAt"),
                    }
                    for d in hits[:5]
                ],
            }
        else:
            report["listings"][name] = {"ok": False, "sortBy": sort_by, "status": res.status, "error": res.error}

    # Character endpoint samples from the raw Typesense hits.
    samples = report["typesense"].get("samples") or []
    sample_ids = []
    for doc in samples:
        bot_id = doc.get("character_id") or doc.get("id")
        if bot_id and bot_id not in sample_ids:
            sample_ids.append(str(bot_id))
    for bot_id in sample_ids[:3]:
        report["characterApi"][bot_id] = sanitize_character_result(client.character(bot_id))

    # Known deleted fixture from spicychat.drache.uk/archive.
    deleted = client.character(KNOWN_DELETED_ID)
    report["deletedFixture"]["character"] = sanitize_character_result(deleted)
    try:
        r = requests.get(KNOWN_DELETED_IMAGE, timeout=20)
        report["deletedFixture"]["avatar"] = {
            "url": KNOWN_DELETED_IMAGE,
            "status": r.status_code,
            "ok": r.ok,
            "contentType": r.headers.get("content-type"),
            "bytes": len(r.content),
        }
    except Exception as exc:
        report["deletedFixture"]["avatar"] = {"url": KNOWN_DELETED_IMAGE, "ok": False, "error": str(exc)}

    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": args.output,
        "found": report["typesense"].get("found"),
        "fields": len(report["typesense"].get("fields") or []),
        "boundaryPages": report["typesense"].get("boundaryPages"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
