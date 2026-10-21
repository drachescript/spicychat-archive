"""Protect the Archive's historical tag catalog from current-picker pruning."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ArchiveTagCatalogTests(unittest.TestCase):
    def test_retired_picker_tag_remains_in_archive_catalog(self):
        payload = json.loads((ROOT / "data" / "tags.json").read_text(encoding="utf-8"))
        tags = payload["tags"]
        archived_only = payload["archivedOnlyTags"]
        self.assertEqual(len(tags), payload["count"])
        self.assertEqual(len(tags), len(set(tags)))
        self.assertIn("Step-Family", tags)
        self.assertEqual(
            archived_only[0]["tag"], "Step-Family",
        )
        self.assertEqual(
            archived_only[0]["notShownInCreatorPickerAsOf"], "2026-10-21",
        )
        self.assertEqual(payload["currentCreatorPickerTagCount"], 90)
        self.assertEqual(payload["count"], 91)
        self.assertEqual(
            payload["count"] - len(archived_only), payload["currentCreatorPickerTagCount"]
        )

    def test_archive_tag_filter_does_not_rename_historical_tag_value(self):
        js = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertIn("archivedOnlyTagLabels", js)
        self.assertIn("historical", js)
        self.assertIn('data-tag="${esc(tag)}"', js)


if __name__ == "__main__":
    unittest.main()
