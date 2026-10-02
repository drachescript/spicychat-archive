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
                {"status": "public", "from": "2026-09-16T00:00:00Z"},
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

    def test_creator_bucket_is_stable(self):
        self.assertEqual(site.creator_bucket("CreatorName"), site.creator_bucket("creatorname"))
        self.assertEqual(len(site.creator_bucket("creatorname")), 2)

    def test_creator_meta_has_archive_times(self):
        meta = site.creator_bot_meta(self.sample())
        self.assertEqual(meta[0], 1)
        self.assertGreater(meta[1], 0)
        self.assertGreater(meta[2], meta[1])
        self.assertEqual(meta[3], 1)


if __name__ == "__main__":
    unittest.main()
