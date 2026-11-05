import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prune_rulebook_listing_only as prune
from storage_r2 import R2ArchiveStore


class MetadataPruneTests(unittest.TestCase):
    def setUp(self):
        self.id = "d3cfa67b-1390-54b9-aab9-4bf9f475a130"
        self.key = "rulebooks/d3/" + self.id + ".json"
        self.record = {
            "schemaVersion": 1, "id": self.id,
            "firstSeenAt": "2026-11-02T00:00:00Z",
            "lastObservedAt": "2026-11-03T00:00:00Z",
            "fingerprint": "hash", "status": {"current": "not-public"},
            "statusHistory": [{"at": "2026-11-03", "status": "not-public"}],
            "listing": {"name": "War of Races", "num_rules": 173,
                        "description": "Listing, not the rules"},
            "versions": [{"at": "2026-11-02", "metadata": {
                "name": "War of Races", "num_rules": 173}}],
        }

    def test_strict_whitelist_rejects_real_data_and_unknown_fields(self):
        self.assertTrue(prune.listing_only_record(self.record, self.key))
        self.assertFalse(prune.listing_only_record(
            {**self.record, "publicDefinition": {"visibility": "shared", "meters": []}}, self.key))
        self.assertFalse(prune.listing_only_record(
            {**self.record, "rules": [{"content": "meaningful"}]}, self.key))
        self.assertFalse(prune.listing_only_record(
            {**self.record, "listing": {"rules": [{"content": "valuable"}]}}, self.key))
        self.assertFalse(prune.listing_only_record(self.record, "bots/d3/" + self.id + ".json"))
        self.assertFalse(prune.listing_only_record(self.record, "rulebooks/a0/" + self.id + ".json"))
        self.assertFalse(prune.listing_only_record(
            {**self.record, "versions": [{"at": "2026-11-02", "metadata": {
                "publicRules": ["important"]}}]}, self.key))

    def test_prune_previews_then_only_removes_verified_listing_object(self):
        other_id = "a21710ec-3a34-4e45-aa62-d291bbee5744"
        other_key = "rulebooks/a2/" + other_id + ".json"
        store = Mock()
        store.bucket = "example"
        store.key.side_effect = lambda *args: "/".join(args)
        store.get_json.side_effect = lambda key, default=None: (
            {"lastScanComplete": True, "catalogNow": 539, "rulebooks": [
                {"id": self.id, "contentSaved": False},
                {"id": other_id, "contentSaved": True},
            ]} if key == "indexes/rulebooks.json" else
            self.record if key == self.key else {"id": other_id})
        store.s3.list_objects_v2.return_value = {
            "Contents": [{"Key": self.key, "Size": 450},
                         {"Key": other_key, "Size": 900}],
            "IsTruncated": False,
        }
        preview = prune.prune(store, apply=False, max_objects=10)
        self.assertEqual(preview["listingOnly"], 1)
        self.assertEqual(preview["protected"], 1)
        store.delete_bytes.assert_not_called()
        actual = prune.prune(store, apply=True, max_objects=10)
        self.assertEqual(actual["removed"], 1)
        store.delete_bytes.assert_called_once_with(self.key)

    def test_cap_fails_before_deleting_anything(self):
        store = Mock()
        store.bucket = "example"
        store.key.side_effect = lambda *parts: "/".join(parts)
        store.get_json.return_value = {
            "lastScanComplete": True, "catalogNow": 1, "rulebooks": []}
        store.s3.list_objects_v2.return_value = {
            "Contents": [{"Key": self.key, "Size": 120}], "IsTruncated": False}
        with self.assertRaisesRegex(RuntimeError, "safety cap"):
            prune.prune(store, apply=True, max_objects=0)
        store.delete_bytes.assert_not_called()

    def test_delete_uses_successful_s3_response_before_usage_change(self):
        store = R2ArchiveStore.__new__(R2ArchiveStore)
        store.bucket = "example"
        store.meta_prefix = "_meta"
        store._usage_lock = threading.Lock()
        store._usage = {"totalBytes": 1000, "mediaBytes": 0, "objects": 6,
                        "month": "2026-11", "writesThisMonth": 12}
        store._usage_dirty_writes = 0
        store.s3 = Mock()
        store._head_size = Mock(return_value=450)
        store.storage_usage = Mock(return_value=store._usage)
        store.flush_usage = Mock()
        self.assertTrue(store.delete_bytes(self.key))
        self.assertEqual(store._usage["totalBytes"], 550)
        self.assertEqual(store._usage["objects"], 5)
        self.assertEqual(store._usage["writesThisMonth"], 13)
        store.s3.delete_object.assert_called_once_with(
            Bucket="example", Key=self.key)


if __name__ == "__main__":
    unittest.main()
