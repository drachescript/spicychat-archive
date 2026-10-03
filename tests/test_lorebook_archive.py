import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lorebook_archive as lorebooks


class LorebookArchiveTests(unittest.TestCase):
    def test_normalize_lorebook_id_accepts_known_shapes(self):
        self.assertEqual(lorebooks.normalize_lorebook_id({"id": "ABC-123"}), "abc-123")
        self.assertEqual(lorebooks.normalize_lorebook_id({"lorebookId": "ABC-456"}), "abc-456")
        self.assertIsNone(lorebooks.normalize_lorebook_id({}))

    def test_record_tracks_meaningful_versions_and_entry_changes(self):
        at1 = "2026-10-03T00:00:00Z"
        at2 = "2026-10-03T03:00:00Z"
        listing = {
            "id": "book-1",
            "name": "Book One",
            "description": "First",
            "creator_username": "alice",
            "tags": ["Fantasy"],
            "num_entries": 1,
            "visibility": "public",
            "status": "active",
        }
        detail1 = {
            **listing,
            "entries": [
                {
                    "id": "entry-1",
                    "name": "Dragon",
                    "keywords": ["dragon"],
                    "content": "Old lore",
                    "version": 1,
                }
            ],
        }
        record, changed = lorebooks.build_or_update_record(
            None,
            listing=listing,
            detail=detail1,
            at=at1,
        )
        self.assertTrue(changed)
        self.assertEqual(record["status"]["current"], "public")
        self.assertEqual(len(record["versions"]), 1)

        detail2 = {
            **detail1,
            "description": "Second",
            "entries": [
                {
                    "id": "entry-1",
                    "name": "Dragon",
                    "keywords": ["dragon"],
                    "content": "New lore",
                    "version": 2,
                }
            ],
        }
        record2, changed2 = lorebooks.build_or_update_record(
            record,
            listing={**listing, "description": "Second"},
            detail=detail2,
            at=at2,
        )
        self.assertTrue(changed2)
        self.assertEqual(len(record2["versions"]), 2)
        fields = [row["field"] for row in record2["history"]]
        self.assertIn("description", fields)
        self.assertIn("Entries", fields)

    def test_mark_not_public_is_idempotent_and_reappearance_restores_public(self):
        listing = {"id": "book-1", "name": "Book One", "visibility": "public"}
        record, _ = lorebooks.build_or_update_record(
            None,
            listing=listing,
            detail=listing,
            at="2026-10-03T00:00:00Z",
        )

        missing, changed = lorebooks.mark_not_public(
            record,
            at="2026-10-03T03:00:00Z",
        )
        self.assertTrue(changed)
        self.assertEqual(missing["status"]["current"], "not-public")

        missing2, changed2 = lorebooks.mark_not_public(
            missing,
            at="2026-10-03T06:00:00Z",
        )
        self.assertFalse(changed2)

        restored, restored_changed = lorebooks.build_or_update_record(
            missing2,
            listing=listing,
            detail=listing,
            at="2026-10-03T09:00:00Z",
        )
        self.assertTrue(restored_changed)
        self.assertEqual(restored["status"]["current"], "public")
        self.assertEqual(restored["statusHistory"][-1]["status"], "public")

    def test_summary_uses_detail_fields(self):
        listing = {
            "id": "book-1",
            "name": "Listing name",
            "creator_username": "alice",
            "num_entries": 1,
        }
        detail = {
            **listing,
            "name": "Detail name",
            "description": "Lore description",
            "tags": ["Fantasy", "Dragon"],
            "entries": [{"id": "e1"}, {"id": "e2"}],
            "is_nsfw": True,
        }
        record, _ = lorebooks.build_or_update_record(
            None,
            listing=listing,
            detail=detail,
            at="2026-10-03T00:00:00Z",
        )
        summary = lorebooks.summary_from_record(record)
        self.assertEqual(summary["name"], "Detail name")
        self.assertEqual(summary["creator"], "alice")
        self.assertEqual(summary["numEntries"], 1)
        self.assertTrue(summary["isNsfw"])

    def test_result_cap_detection(self):
        self.assertTrue(lorebooks._typesense_has_more(100000, page=169, per_page=250, received=188))
        self.assertFalse(lorebooks._typesense_has_more(42188, page=169, per_page=250, received=188))


if __name__ == "__main__":
    unittest.main()
