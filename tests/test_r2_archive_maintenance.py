import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import archive
import r2_archive_maintenance as maintenance


class FakeStore:
    def __init__(self, ids):
        self.discovery_order = list(ids)
        self.deleted_index = set()


class FakeClient:
    def character(self, bot_id):
        return archive.HTTPResult(
            True,
            200,
            data={"character": {"character_id": bot_id, "greeting": f"hello {bot_id}"}},
        )


class MetricOnlyClient:
    def character(self, bot_id):
        return archive.HTTPResult(
            True,
            200,
            data={
                "character": {
                    "character_id": bot_id,
                    "greeting": "same greeting",
                    "num_messages": 11,
                }
            },
        )


class ConcurrentFakeClient:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def character(self, bot_id):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.02)
            return archive.HTTPResult(
                True,
                200,
                data={"character": {"character_id": bot_id, "greeting": f"hello {bot_id}"}},
            )
        finally:
            with self.lock:
                self.active -= 1


def make_record(bot_id, *, greeting=None, num_messages=None):
    current = {}
    last_known = {}
    metrics = {"latest": {}, "history": []}
    if greeting is not None or num_messages is not None:
        character_api = {"character_id": bot_id}
        last_known["character_id"] = bot_id
        if greeting is not None:
            character_api["greeting"] = greeting
            last_known["greeting"] = greeting
        if num_messages is not None:
            character_api["num_messages"] = num_messages
            last_known["num_messages"] = num_messages
            metrics["latest"] = {"num_messages": num_messages}
        current["character-api"] = character_api

    return {
        "schemaVersion": 1,
        "id": bot_id,
        "firstSeenAt": "2026-09-29T00:00:00Z",
        "lastSeenAt": "2026-09-29T00:00:00Z",
        "status": {"current": "public", "since": "2026-09-29T00:00:00Z"},
        "sources": {},
        "current": current,
        "lastKnown": last_known,
        "fieldHistory": [],
        "availabilityHistory": [],
        "listings": {},
        "metrics": metrics,
        "avatarArchive": {},
    }


class MaintenanceSweepTests(unittest.TestCase):
    def test_first_404_is_recorded_once(self):
        state = {}
        maintenance._seed_missing_404(state, "bot-a", "2026-09-30T00:00:00Z")
        info = state["missingChecks"]["bot-a"]
        self.assertEqual(info["count"], 1)
        self.assertEqual(info["lastStatus"], 404)

        maintenance._seed_missing_404(state, "bot-a", "2026-09-30T03:00:00Z")
        info = state["missingChecks"]["bot-a"]
        self.assertEqual(info["count"], 1)
        self.assertEqual(info["lastAt"], "2026-09-30T00:00:00Z")

    def test_character_refresh_resumes_and_wraps_cursor(self):
        store = FakeStore(["bot-a", "bot-b", "bot-c"])
        state = {"priorityEnrichment": []}
        config = {
            "crawler": {
                "maintenance_verification_budget": 2,
                "maintenance_priority_budget": 1,
                "maintenance_verification_workers": 2,
                "maintenance_verification_time_limit_seconds": 60,
            }
        }

        records = {
            bot_id: make_record(bot_id)
            for bot_id in store.discovery_order
        }

        with patch.object(archive, "load_bot", side_effect=lambda bot_id: records[bot_id]), patch.object(
            archive, "save_bot", return_value=True
        ):
            first = maintenance._maintenance_character_refresh(
                FakeClient(),
                config,
                "2026-09-30T00:00:00Z",
                state,
                store=store,
            )
            self.assertEqual(first["processed"], 2)
            self.assertEqual(first["verificationCursor"], 2)
            self.assertEqual(first["verificationCompletedPasses"], 0)

            second = maintenance._maintenance_character_refresh(
                FakeClient(),
                config,
                "2026-09-30T03:00:00Z",
                state,
                store=store,
            )
            self.assertEqual(second["processed"], 2)
            self.assertEqual(second["verificationCursor"], 1)
            self.assertEqual(second["verificationCompletedPasses"], 1)

    def test_metric_only_updates_are_not_content_changes(self):
        store = FakeStore(["bot-a"])
        state = {"priorityEnrichment": []}
        config = {
            "crawler": {
                "maintenance_verification_budget": 1,
                "maintenance_priority_budget": 1,
                "maintenance_verification_workers": 1,
                "maintenance_verification_time_limit_seconds": 60,
            }
        }
        record = make_record("bot-a", greeting="same greeting", num_messages=10)

        with patch.object(archive, "load_bot", return_value=record), patch.object(
            archive, "save_bot", return_value=True
        ):
            result = maintenance._maintenance_character_refresh(
                MetricOnlyClient(),
                config,
                "2026-09-30T06:00:00Z",
                state,
                store=store,
            )

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["contentChanged"], 0)
        self.assertEqual(result["metricUpdates"], 0)
        self.assertEqual(record["metrics"]["latest"]["num_messages"], 10)

    def test_character_requests_run_concurrently(self):
        ids = [f"bot-{index}" for index in range(8)]
        store = FakeStore(ids)
        state = {"priorityEnrichment": []}
        config = {
            "crawler": {
                "maintenance_verification_budget": 8,
                "maintenance_priority_budget": 1,
                "maintenance_verification_workers": 4,
                "maintenance_verification_time_limit_seconds": 60,
            }
        }
        records = {bot_id: make_record(bot_id) for bot_id in ids}
        client = ConcurrentFakeClient()

        with patch.object(archive, "load_bot", side_effect=lambda bot_id: records[bot_id]), patch.object(
            archive, "save_bot", return_value=True
        ):
            result = maintenance._maintenance_character_refresh(
                client,
                config,
                "2026-09-30T06:00:00Z",
                state,
                store=store,
            )

        self.assertEqual(result["processed"], 8)
        self.assertEqual(result["verificationWorkers"], 4)
        self.assertGreaterEqual(client.max_active, 2)


if __name__ == "__main__":
    unittest.main()
