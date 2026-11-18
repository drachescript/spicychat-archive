#!/usr/bin/env python3
"""Read-only smoke test for the public Archive APIs consumed by QoL.

No user credentials, private data, crawling, or R2 writes. A scheduled
workflow catches broken static feed URLs and Worker contract drift.
"""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.request

STATIC = "https://spicychatarchive.drache.uk"
DATA = "https://data.spicychatarchive.drache.uk"
WORKER = "https://spicychat-archive-import.dragongraf.workers.dev"
BOT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def fetch_json(url: str, data: dict | None = None) -> dict:
    headers = {"User-Agent": "Archive-QoL-Feed-Health/1.0",
               "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, headers=headers,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        method="POST" if data is not None else "GET")
    error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=18) as response:
                if not str(response.headers.get("Content-Type", "")).lower().startswith(
                        ("application/json", "text/json")):
                    raise ValueError("Unexpected MIME type: " + url)
                raw = response.read(1200000)
                if len(raw) > 1100000:
                    raise ValueError("JSON response too large: " + url)
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError("Unexpected JSON root: " + url)
                return result
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            error = exc
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError("Could not read " + url + ": " + str(error))


def main() -> int:
    preset_manifest = fetch_json(STATIC + "/data/block-presets/manifest.json")
    preset_catalog = fetch_json(STATIC + "/data/block-presets/catalog.json")
    assert preset_manifest["schemaVersion"] == preset_catalog["schemaVersion"] == 1
    assert preset_manifest["contract"] == preset_catalog["contract"] == (
        "spicychat-archive-block-presets-v1")
    assert preset_manifest["catalogVersion"] == preset_catalog["catalogVersion"]
    assert preset_manifest["catalog"] == STATIC + "/data/block-presets/catalog.json"
    assert isinstance(preset_catalog["presets"], list)
    assert isinstance(preset_catalog["characters"], list)
    assert isinstance(preset_catalog["groups"], list)

    taxonomy = fetch_json(STATIC + "/data/community-tags/taxonomy.json")
    aliases = fetch_json(STATIC + "/data/community-tags/aliases.json")
    assert taxonomy["schemaVersion"] == aliases["schemaVersion"] == 1
    assert len(taxonomy["tags"]) > 300
    assert isinstance(aliases["byTagId"], dict)

    live = fetch_json(DATA + "/indexes/community-tags/manifest.json")
    assert live["schemaVersion"] == 1
    assert live["contract"] == "spicychat-archive-community-tags-v1"
    assert live["complete"] is True
    assert len(live["snapshot"]) == 20

    worker = fetch_json(WORKER + "/api/community-tags/manifest")
    assert worker["ok"] is True and worker["schemaVersion"] == 1
    assert len(worker["snapshot"]) == 20

    result = fetch_json(WORKER + "/api/community-tags/lookup", {"ids": [BOT]})
    assert result["ok"] is True and result["schemaVersion"] == 1
    assert result["complete"] is True
    assert result["checked"] == 1
    assert isinstance(result["matches"], dict)
    assert BOT not in result["matches"] or isinstance(result["matches"][BOT]["signals"], list)
    print("Archive QoL public feeds: OK")
    print("Preset v%d: %d opt-in presets, %d curated characters" %
          (preset_manifest["catalogVersion"], len(preset_catalog["presets"]),
           len(preset_catalog["characters"])))
    print("Community snapshot: %s, %s indexed bot signals" %
          (worker["snapshot"], worker.get("coverage", {}).get("signalBots", "unknown")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
