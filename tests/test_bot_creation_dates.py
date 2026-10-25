"""Creation metadata is preserved without triggering author-edit version changes."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import archive
import r2_archive_optimized as optimized


class BotCreationDateTests(unittest.TestCase):
    def test_dates_survive_observation_but_do_not_change_content_fingerprint(self):
        doc = {
            "character_id": "1234-5678-0000", "name": "Example",
            "createdAt": 1791760920, "publishedAt": "2026-10-13T12:00:00Z",
            "updatedAt": "2026-10-20T00:00:00Z",
        }
        cleaned = optimized._observation_typesense_doc(doc)
        self.assertEqual(cleaned["createdAt"], doc["createdAt"])
        self.assertEqual(cleaned["publishedAt"], doc["publishedAt"])
        self.assertNotIn("updatedAt", cleaned)
        self.assertNotIn("createdAt", optimized._clean_typesense_doc(doc))
        self.assertEqual(
            optimized._content_hash(doc),
            optimized._content_hash({**doc, "createdAt": 1791760980,
                                     "publishedAt": "2026-10-14T12:00:00Z"}),
        )

    def test_metadata_does_not_make_fake_creator_edits(self):
        first = {
            "character_id": "date-preservation-bot", "name": "Example",
            "createdAt": 1791760920,
        }
        record, _ = archive.observe_bot(None, first, source="typesense",
                                        at="2026-10-14T00:00:00Z")
        later = {**first, "createdAt": 1791760980,
                 "publishedAt": "2026-10-14T01:00:00Z"}
        record, _ = archive.observe_bot(record, later, source="typesense",
                                        at="2026-10-15T00:00:00Z")
        self.assertEqual(record["lastKnown"]["createdAt"], 1791760980)
        self.assertEqual(record["lastKnown"]["publishedAt"], later["publishedAt"])
        self.assertFalse(any(x.get("path") in ("createdAt", "publishedAt")
                             for x in record["fieldHistory"]))


if __name__ == "__main__":
    unittest.main()
