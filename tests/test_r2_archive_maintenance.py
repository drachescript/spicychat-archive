import sys
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
                "maintenance_verification_time_limit_seconds": 60,
            }
        }

        records = {
            bot_id: {
                "schemaVersion": 1,
                "id": bot_id,
                "firstSeenAt": "2026-09-29T00:00:00Z",
                "lastSeenAt": "2026-09-29T00:00:00Z",
                "status": {"current": "public", "since": "2026-09-29T00:00:00Z"},
                "sources": {},
                "current": {},
                "lastKnown": {},
                "fieldHistory": [],
                "availabilityHistory": [],
                "listings": {},
                "metrics": {"latest": {}, "history": []},
                "avatarArchive": {},
            }
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


if __name__ == "__main__":
    unittest.main()
