import sys
import unittest
from copy import deepcopy
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import import_bot_status as imp


class BotStatusImportTests(unittest.TestCase):
    def test_sanitizer_drops_chat_and_extension_private_fields(self):
        snapshot = {
            "id": "abc",
            "name": "Test Bot",
            "personality": "public-ish saved character data",
            "messages": [{"role": "user", "content": "private chat"}],
            "cookies": "secret",
            "extensionSettings": {"x": True},
        }
        clean = imp.sanitize_snapshot(snapshot, "abc")
        self.assertEqual(clean["name"], "Test Bot")
        self.assertIn("personality", clean)
        self.assertNotIn("messages", clean)
        self.assertNotIn("cookies", clean)
        self.assertNotIn("extensionSettings", clean)

    def test_sanitizer_flattens_qol_bot_status_fields(self):
        snapshot = {
            "id": "abc",
            "snapshotAt": 123,
            "fields": {
                "name": "QoL Bot",
                "description": "Saved description",
                "greeting": "Hello",
                "personality": "Saved personality",
                "scenario": "Saved scenario",
                "exampleDialogues": "Example",
                "creator": "creator_name",
                "image": "https://cdn.nd-api.com/avatars/example.webp",
                "messageCount": 12345,
                "rating": 4.8,
                "tokenCount": 987,
            },
            "statusObservation": {
                "status": "unavailable",
                "httpStatus": 404,
            },
            "messages": [{"role": "user", "content": "must never import"}],
        }
        clean = imp.sanitize_snapshot(snapshot, "abc")
        self.assertEqual(clean["character_id"], "abc")
        self.assertEqual(clean["name"], "QoL Bot")
        self.assertEqual(clean["description"], "Saved description")
        self.assertEqual(clean["greeting"], "Hello")
        self.assertEqual(clean["personality"], "Saved personality")
        self.assertEqual(clean["scenario"], "Saved scenario")
        self.assertEqual(clean["example_dialogues"], "Example")
        self.assertEqual(clean["creator"], "creator_name")
        self.assertEqual(clean["image"], "https://cdn.nd-api.com/avatars/example.webp")
        self.assertEqual(clean["num_messages"], 12345)
        self.assertEqual(clean["rating_score"], 4.8)
        self.assertEqual(clean["token_count"], 987)
        self.assertNotIn("statusObservation", clean)
        self.assertNotIn("messages", clean)

    def test_new_import_is_not_marked_public(self):
        snapshot = {"character_id": "abc", "name": "Saved Bot"}
        record, changed, duplicate = imp.merge_snapshot(
            None, bot_id="abc", saved_at="2026-09-01T00:00:00Z", snapshot=snapshot
        )
        self.assertTrue(changed)
        self.assertFalse(duplicate)
        self.assertEqual(record["status"]["current"], "unverified-import")
        self.assertIsNone(record["status"]["lastVerifiedAt"])

    def test_existing_status_is_never_changed_by_import(self):
        existing = {
            "schemaVersion": 1,
            "id": "abc",
            "firstSeenAt": "2026-09-20T00:00:00Z",
            "lastSeenAt": "2026-09-28T00:00:00Z",
            "status": {"current": "deleted", "since": "2026-09-27T00:00:00Z", "lastVerifiedAt": "2026-09-28T00:00:00Z"},
            "sources": {},
            "current": {},
            "lastKnown": {"character_id": "abc", "name": "Current Name"},
            "fieldHistory": [],
            "availabilityHistory": [],
            "listings": {},
            "metrics": {"latest": {}, "history": []},
            "avatarArchive": {},
        }
        snapshot = {"character_id": "abc", "name": "Old Name", "personality": "Recovered personality"}
        record, changed, duplicate = imp.merge_snapshot(
            existing, bot_id="abc", saved_at="2026-09-10T00:00:00Z", snapshot=snapshot
        )
        self.assertTrue(changed)
        self.assertFalse(duplicate)
        self.assertEqual(record["status"]["current"], "deleted")
        self.assertEqual(record["lastKnown"]["name"], "Current Name")
        self.assertEqual(record["lastKnown"]["personality"], "Recovered personality")
        self.assertEqual(record["firstSeenAt"], "2026-09-10T00:00:00Z")

    def test_duplicate_snapshot_is_idempotent(self):
        snapshot = {"character_id": "abc", "name": "Saved Bot"}
        record, _, _ = imp.merge_snapshot(
            None, bot_id="abc", saved_at="2026-09-01T00:00:00Z", snapshot=snapshot
        )
        again, changed, duplicate = imp.merge_snapshot(
            record, bot_id="abc", saved_at="2026-09-01T00:00:00Z", snapshot=snapshot
        )
        self.assertFalse(changed)
        self.assertTrue(duplicate)
        self.assertEqual(
            len(again["imports"]["qolBotStatus"]["snapshots"]), 1
        )

    def test_extracts_expected_extension_contract(self):
        payload = {
            "schemaVersion": 1,
            "kind": "spicychat-qol-bot-status-export",
            "exportedAt": "2026-09-28T18:00:00Z",
            "savedCopies": [
                {
                    "botId": "ABC",
                    "savedAt": "2026-09-20T12:00:00Z",
                    "snapshot": {"name": "Bot", "personality": "P"},
                }
            ],
        }
        rows = list(imp.extract_entries(payload))
        self.assertEqual(len(rows), 1)
        bot_id, saved_at, snapshot = rows[0]
        self.assertEqual(bot_id, "abc")
        self.assertEqual(saved_at, "2026-09-20T12:00:00Z")
        self.assertEqual(snapshot["character_id"], "abc")
        self.assertEqual(snapshot["personality"], "P")

    def test_preload_uses_one_r2_read_per_unique_uncached_bot(self):
        class FakeStore:
            known_ids = {"a", "b"}
            _records = {"a": {"id": "a", "status": {"current": "deleted"}}}

            def __init__(self):
                self.get_calls = []

            def load_bot(self, bot_id):
                return deepcopy(self._records[bot_id])

            def bot_key(self, bot_id):
                return f"bots/{bot_id}.json"

            def get_json(self, key, default):
                self.get_calls.append(key)
                return {"id": key.split("/")[-1][:-5]}

        store = FakeStore()
        rows = [
            ("a", "2026-10-01", {}),
            ("b", "2026-10-01", {}),
            ("b", "2026-10-02", {}),
            ("missing", "2026-10-01", {}),
        ]
        loaded = imp._preload_existing_records(store, rows)
        self.assertEqual(store.get_calls, ["bots/b.json"])
        self.assertEqual(loaded["a"]["status"]["current"], "deleted")
        self.assertEqual(loaded["b"]["id"], "b")
        self.assertNotIn("missing", loaded)

    def test_multiple_snapshots_for_one_bot_merge_in_original_order(self):
        earlier = {"character_id": "abc", "name": "First"}
        current, _, _ = imp.merge_snapshot(
            None, bot_id="abc", saved_at="2026-10-01T00:00:00Z", snapshot=earlier
        )
        current["status"]["current"] = "deleted"

        class FakeStore:
            known_ids = {"abc"}
            _records = {}

            def __init__(self, record):
                self.record = deepcopy(record)
                self.reads = 0
                self.writes = 0

            def bot_key(self, bot_id):
                return f"bots/{bot_id}.json"

            def get_json(self, key, default):
                self.reads += 1
                return deepcopy(self.record)

            def save_bot(self, record):
                self.record = deepcopy(record)
                self.writes += 1

        store = FakeStore(current)
        payload = {"savedCopies": [
            {"botId": "abc", "savedAt": "2026-10-02T00:00:00Z", "snapshot": {"name": "Second"}},
            {"botId": "abc", "savedAt": "2026-10-03T00:00:00Z", "snapshot": {"name": "Third"}},
        ]}
        state = {}
        with patch.object(imp, "note_rich_field_record"), patch.object(imp, "note_site_record"):
            result = imp.process_payload(
                store, state, payload, received_at="2026-10-04T00:00:00Z"
            )
        self.assertEqual(store.reads, 1)
        self.assertEqual(store.writes, 2)
        self.assertEqual(result["updatedRecords"], 2)
        self.assertEqual(result["duplicates"], 0)
        self.assertEqual(store.record["status"]["current"], "deleted")
        self.assertEqual(
            len(store.record["imports"]["qolBotStatus"]["snapshots"]), 3
        )
        self.assertEqual(
            store.record["current"][imp.SOURCE]["name"], "Third"
        )


if __name__ == "__main__":
    unittest.main()
