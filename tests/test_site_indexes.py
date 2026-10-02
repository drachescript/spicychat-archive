import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import site_indexes as site


class SiteIndexTests(unittest.TestCase):
    def sample(self):
        return {
            "id": "11111111-1111-1111-1111-111111111111",
            "firstSeenAt": "2026-09-01T00:00:00Z",
            "lastSeenAt": "2026-10-02T00:00:00Z",
            "status": {"current": "public"},
            "lastKnown": {
                "name": "Test Bot",
                "title": "A title",
                "creator_username": "CreatorName",
                "persona": "Personality",
            },
            "fieldHistory": [
                {
                    "at": "2026-10-01T00:00:00Z",
                    "source": "character-api",
                    "path": "title",
                    "kind": "value",
                    "from": "Old title",
                    "to": "A title",
                },
                {
                    "at": "2026-10-01T00:00:01Z",
                    "source": "character-api",
                    "path": "num_messages",
                    "kind": "value",
                    "from": 1,
                    "to": 2,
                },
            ],
            "availabilityHistory": [
                {"status": "public", "from": "2026-09-01T00:00:00Z"},
                {"status": "deleted", "from": "2026-09-15T00:00:00Z"},
                {"status": "public", "from": "2026-09-16T00:00:00Z", "source": "character-api"},
            ],
        }

    def test_change_rows_ignore_metrics(self):
        rows = site.change_rows(self.sample())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], "title")

    def test_restored_detection(self):
        row = site.restored_row(self.sample())
        self.assertIsNotNone(row)
        self.assertEqual(row["restoreCount"], 1)
        self.assertEqual(row["restoredAt"], "2026-09-16T00:00:00Z")

    def test_unverified_restoration_candidate_is_not_restored(self):
        record = self.sample()
        record["availabilityHistory"][-1]["source"] = "typesense"
        self.assertEqual(len(site.restoration_candidates(record)), 1)
        self.assertEqual(site.restored_events(record), [])
        self.assertIsNone(site.restored_row(record))
        activity = site.activity_rows(record)
        self.assertTrue(any(row["type"] == "restore-candidate" for row in activity))
        self.assertFalse(any(row["type"] == "restored" for row in activity))

    def test_activity_rows_include_new_deleted_and_verified_restored(self):
        rows = site.activity_rows(self.sample())
        self.assertEqual([row["type"] for row in rows], ["new", "deleted", "restored"])

    def test_current_deleted_restore_candidate_is_unverified_activity(self):
        record = self.sample()
        record["availabilityHistory"] = record["availabilityHistory"][:2]
        record["status"] = {
            "current": "deleted",
            "restoreCandidate": {
                "at": "2026-10-02T00:00:00Z",
                "source": "typesense",
            },
        }
        self.assertEqual(site.restored_events(record), [])
        self.assertEqual(len(site.restoration_candidates(record)), 1)
        rows = site.activity_rows(record)
        self.assertEqual(rows[-1]["type"], "restore-candidate")
        self.assertEqual(rows[-1]["source"], "typesense")

    def test_month_key(self):
        self.assertEqual(site.month_key("2026-10-02T00:00:00Z"), "2026-10")
        self.assertEqual(site.month_key(None), "unknown")

    def test_creator_bucket_is_stable(self):
        self.assertEqual(site.creator_bucket("CreatorName"), site.creator_bucket("creatorname"))
        self.assertEqual(len(site.creator_bucket("creatorname")), 2)

    def test_creator_meta_has_archive_times_and_card_data(self):
        meta = site.creator_bot_meta(self.sample())
        self.assertEqual(meta["status"], "public")
        self.assertTrue(meta["firstSeenAt"])
        self.assertTrue(meta["lastSeenAt"])
        self.assertEqual(meta["restoreCount"], 1)
        self.assertEqual(meta["name"], "Test Bot")
        self.assertEqual(meta["title"], "A title")
        self.assertEqual(meta["savedMask"], 1)


if __name__ == "__main__":
    unittest.main()
