"""Complete per-ID inventory: audit actual storage, not the arithmetic public gap."""
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_discovery_ids as audit


class FakeS3:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def list_objects_v2(self, **kwargs):
        self.calls.append(kwargs)
        if not self.pages:
            raise AssertionError("Called beyond final inventory page")
        return self.pages.pop(0)


class FakeStore:
    bucket = "spicychat-archive"
    bot_prefix = "bots"
    def __init__(self, pages):
        self.s3 = FakeS3(pages)
    def bot_key(self, bot_id):
        compact = bot_id.replace("-", "").lower()
        return f"bots/{compact[:2] if len(compact) >= 2 else '__'}/{bot_id}.json"


def botkey(bot_id):
    return f"bots/{bot_id[:2]}/{bot_id}.json"


class CompleteIDInventoryTests(unittest.TestCase):
    def test_full_paginated_r2_inventory_requires_all_pages(self):
        store = FakeStore([
            {"Contents": [{"Key": botkey("aa00")}, {"Key": botkey("bb11")}],
             "IsTruncated": True, "NextContinuationToken": "NEXT"},
            {"Contents": [{"Key": botkey("cc22")}, {"Key": "bots/xx/not-a-bot.tmp"},
                          {"Key": "bots/xx/not-canonical.json"}], "IsTruncated": False},
        ])
        ids, summary = audit.inventory_bot_file_ids(store, max_pages=2)
        self.assertEqual(ids, {"aa00", "bb11", "cc22"})
        self.assertEqual(summary["r2ListPages"], 2)
        self.assertEqual(summary["r2KeysVisitedUnderBotPrefix"], 5)
        self.assertEqual(summary["ignoredNoncanonicalBotKeys"], 2)
        self.assertEqual(store.s3.calls[0]["Prefix"], "bots/")
        self.assertEqual(store.s3.calls[1]["ContinuationToken"], "NEXT")

    def test_fail_closed_when_listing_incomplete(self):
        for first in [
            {"Contents": [], "IsTruncated": True},
            {"Contents": [], "IsTruncated": True, "NextContinuationToken": "stuck"},
        ]:
            with self.subTest(first=first):
                store = FakeStore([first])
                with self.assertRaises(RuntimeError):
                    audit.inventory_bot_file_ids(store)
        store = FakeStore([
            {"Contents": [{"Key": botkey("aa00")}], "IsTruncated": True, "NextContinuationToken": "MORE"},
        ])
        with self.assertRaisesRegex(RuntimeError, "refusing to publish partial audit"):
            audit.inventory_bot_file_ids(store, max_pages=1)

    def test_three_source_classification_counts_membership_without_guessing_deletion(self):
        # "a" present everywhere; "b" observed+indexed but missing file;
        # "c" observed+file but not indexed; "d" observed only;
        # "e" indexed+stored but never discovered;
        # "f" stored only; "g" indexed only.
        report = audit.build_audit(
            ["a", "b", "c", "d"], ["a", "b", "e", "g"], ["a", "c", "e", "f"],
            at="2026-10-23T12:00:00Z",
            listing={"r2ListPages": 10},
            archive_snapshot_at="2026-10-23T09:00:00Z",
        )
        expected = {
            "observedDiscoveryIds": 4,
            "materializedIndexIds": 4,
            "storedBotFileIds": 4,
            "presentInAllThree": 1,
            "observedWithStoredFile": 2,
            "observedWithoutStoredFile": 2,
            "observedAndIndexedWithoutStoredFile": 1,
            "observedWithoutIndexButWithStoredFile": 1,
            "observedWithoutIndexAndWithoutStoredFile": 1,
            "indexedWithoutDiscovery": 2,
            "indexedWithoutStoredFile": 2,
            "indexedWithoutDiscoveryButStored": 1,
            "storedFileWithoutDiscovery": 2,
            "storedFileWithoutIndex": 2,
        }
        for key, value in expected.items():
            self.assertEqual(report[key], value, key)
        self.assertEqual(report["observedWithoutStoredFileExamples"], ["b", "d"])
        self.assertEqual(report["storedFileWithoutDiscoveryExamples"], ["e", "f"])
        self.assertEqual(report["indexedWithoutStoredFileExamples"], ["b", "g"])
        self.assertTrue(report["complete"])
        self.assertTrue(report["readOnly"])
        self.assertTrue(report["notLiveStatusVerification"])
        self.assertTrue(report["notAnExplanationOfPublicCountGap"])

    def test_empty_complete_inventory_is_valid_and_not_mislabeled(self):
        store = FakeStore([{"Contents": [], "IsTruncated": False}])
        ids, listing = audit.inventory_bot_file_ids(store)
        self.assertEqual(ids, set())
        self.assertEqual(listing["r2ListPages"], 1)
        report = audit.build_audit([], [], [], at="2026-10-23T12:00:00Z")
        self.assertEqual(report["storedBotFileIds"], 0)
        self.assertEqual(report["observedWithoutStoredFile"], 0)

    def test_case_deduplication_keeps_one_record_per_id(self):
        report = audit.build_audit(["ABC", "abc", "xyz"], ["ABC"], ["AbC", "XYZ"],
                                   at="2026-10-23T12:00:00Z")
        self.assertEqual(report["observedDiscoveryIds"], 2)
        self.assertEqual(report["storedBotFileIds"], 2)
        self.assertEqual(report["presentInAllThree"], 1)

    def test_no_character_writes_or_deletions(self):
        source = (ROOT / "scripts" / "audit_discovery_ids.py").read_text(encoding="utf-8")
        self.assertNotIn("save_bot(", source)
        self.assertNotIn("delete_objects(", source)
        self.assertNotIn("save_discovery_order(", source)
        self.assertNotIn("mark_deleted(", source)
        self.assertIn('store.key("indexes", "discovery-id-audit.json")', source)


if __name__ == "__main__":
    unittest.main()
