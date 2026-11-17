"""Metadata-only Rulebooks retain public listing history, never private rule bodies."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rulebook_archive as rb


class CatalogOnlyRunTests(unittest.TestCase):
    def test_listing_only_preserves_unlisted_metadata_without_rule_files(self):
        public_id = "63416959-f266-593d-830d-37bc1c474ef9"
        old_id = "d3cfa67b-1390-54b9-aab9-4bf9f475a130"
        doc = {
            "rulebook_id": public_id,
            "name": "Dante",
            "creator_username": "sample",
            "num_rules": 173,
            "rules": [{"content": "A private field must never be archived"}],
            "tags": ["Relationship"],
        }
        state = {"schemaVersion": 1, "known": {
            old_id: {"status": "public", "fingerprint": "old",
                     "contentSaved": False, "catalogOnly": True,
                     "firstSeenAt": "2026-11-01T00:00:00Z",
                     "lastSeenAt": "2026-11-02T00:00:00Z",
                     "summary": {"id": old_id, "status": "public",
                                 "name": "Former public Rulebook",
                                 "description": "Historical public metadata"}},
        }}
        store = Mock()
        store.is_migrated.return_value = True
        store.meta_prefix = "_meta"
        store.key.side_effect = lambda *parts: "/".join(map(str, parts))

        def read(key, default=None):
            if key == "_meta/rulebooks-state.json":
                return state
            raise AssertionError("Unexpected per-Rulebook read: " + key)

        store.get_json.side_effect = read
        client = Mock()
        response = Mock()
        response.ok = True
        response.data = {"results": [{}]}
        client.multi_search.return_value = response

        with patch.object(rb.archive, "load_config",
                          return_value={"rulebooks": {"page_size": 48}}), \
             patch.object(rb.R2ArchiveStore, "credentials_present", return_value=True), \
             patch.object(rb, "R2ArchiveStore", return_value=store), \
             patch.object(rb.archive, "Client", return_value=client), \
             patch.object(rb, "configure_public_key", return_value="rulebooks_public"), \
             patch.object(rb.archive, "extract_hits", return_value=([doc], 1)), \
             patch.object(rb, "now_iso", return_value="2026-11-05T00:00:00Z"):
            self.assertEqual(rb.run(), 0)

        writes = {call.args[0]: call.args[1] for call in store.put_json.call_args_list}
        self.assertEqual(set(writes), {"indexes/rulebooks.json", "_meta/rulebooks-state.json"})
        index = writes["indexes/rulebooks.json"]
        self.assertEqual(index["publicNow"], 1)
        self.assertEqual(index["totalArchived"], 0)
        self.assertEqual(index["notPublic"], 0)  # No full rule definitions saved.
        self.assertEqual(index["notPublicListings"], 1)
        self.assertEqual(index["catalogNow"], 1)
        self.assertEqual(index["totalListingsObserved"], 2)
        self.assertTrue(all(row["contentSaved"] is False for row in index["rulebooks"]))
        historic = next(row for row in index["rulebooks"] if row["id"] == old_id)
        self.assertEqual(historic["status"], "not-public")
        self.assertEqual(historic["name"], "Former public Rulebook")
        self.assertIn(old_id, writes["_meta/rulebooks-state.json"]["known"])
        self.assertNotIn("A private field", str(writes))


if __name__ == "__main__":
    unittest.main()
