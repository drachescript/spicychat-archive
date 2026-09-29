#!/usr/bin/env python3
"""Probe which rich SpicyChat Typesense fields can be searched directly."""
from __future__ import annotations

import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
TS = CONFIG["typesense"]

FIELDS = ["name", "title", "description", "greeting", "scenario"]


def main() -> int:
    searches = []
    for field in FIELDS:
        searches.append(
            {
                "collection": TS["collection"],
                "q": "the",
                "query_by": field,
                "page": 1,
                "per_page": 1,
                "filter_by": TS["application_filter"],
                "include_fields": "character_id,name,title,description,greeting,scenario",
            }
        )

    payload = {"searches": searches}
    urls = [TS["primary_url"], *(TS.get("fallback_urls") or [])]
    last_error = None
    data = None
    used_url = None
    for url in urls:
        try:
            response = requests.post(
                url,
                headers={
                    "X-TYPESENSE-API-KEY": TS["public_search_key"],
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30,
            )
            if response.ok:
                data = response.json()
                used_url = url
                break
            last_error = f"HTTP {response.status_code}: {response.text[:500]}"
        except Exception as exc:
            last_error = str(exc)

    if data is None:
        print(f"Probe failed: {last_error or 'all endpoints failed'}")
        return 1

    print(f"Typesense endpoint: {used_url}")
    results = data.get("results") or []
    supported = []
    unsupported = []
    for field, result in zip(FIELDS, results):
        error = result.get("error") if isinstance(result, dict) else "invalid result"
        if error:
            unsupported.append(field)
            print(f"{field}: UNSUPPORTED/ERROR: {error}")
            continue
        supported.append(field)
        found = result.get("found")
        hits = result.get("hits") or []
        doc = hits[0].get("document", {}) if hits else {}
        present = [key for key in ("description", "greeting", "scenario") if key in doc]
        print(f"{field}: OK found={found} returned-rich-fields={present}")

    print("SUPPORTED=" + ",".join(supported))
    print("UNSUPPORTED=" + ",".join(unsupported))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
