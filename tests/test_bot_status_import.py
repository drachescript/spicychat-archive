import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
