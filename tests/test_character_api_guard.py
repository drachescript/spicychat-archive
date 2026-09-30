import unittest

import archive as legacy
import character_api_guard as guard


class CharacterApiGuardTests(unittest.TestCase):
    def setUp(self):
        self.original_character = legacy.Client.character
        self.original_observe = legacy.observe_bot
        self.original_save = legacy.save_bot
        self.original_volatile = set(legacy.VOLATILE_FIELDS)
        self.guard_installed = guard._INSTALLED
        self.guard_original_character = guard._ORIGINAL_CHARACTER
        self.guard_original_observe = guard._ORIGINAL_OBSERVE
        guard._INSTALLED = False
        guard._ORIGINAL_CHARACTER = None
        guard._ORIGINAL_OBSERVE = None

    def tearDown(self):
        legacy.Client.character = self.original_character
        legacy.observe_bot = self.original_observe
        legacy.save_bot = self.original_save
        legacy.VOLATILE_FIELDS.clear()
        legacy.VOLATILE_FIELDS.update(self.original_volatile)
        guard._INSTALLED = self.guard_installed
        guard._ORIGINAL_CHARACTER = self.guard_original_character
        guard._ORIGINAL_OBSERVE = self.guard_original_observe

    def test_http_200_empty_object_becomes_missing_candidate(self):
        def fake_character(_self, _bot_id):
            return legacy.HTTPResult(True, 200, data={}, url="https://example.invalid/bot")

        legacy.Client.character = fake_character
        guard.install_character_api_guard()
        client = object.__new__(legacy.Client)
        result = client.character("bot-id")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, 404)
        self.assertIn("HTTP 200 empty", result.error)

    def test_nonempty_character_payload_stays_available(self):
        def fake_character(_self, _bot_id):
            return legacy.HTTPResult(True, 200, data={"id": "bot-id", "name": "Bot"})

        legacy.Client.character = fake_character
        guard.install_character_api_guard()
        client = object.__new__(legacy.Client)
        result = client.character("bot-id")

        self.assertTrue(result.ok)
        self.assertEqual(result.status, 200)

    def test_first_character_snapshot_is_saved_as_baseline_not_edit(self):
        saved = []
        legacy.save_bot = lambda record: saved.append(record) or True
        guard.install_character_api_guard()

        record = {
            "schemaVersion": 1,
            "id": "bot-id",
            "firstSeenAt": "2026-09-30T00:00:00Z",
            "lastSeenAt": "2026-09-30T00:00:00Z",
            "status": {
                "current": "public",
                "since": "2026-09-30T00:00:00Z",
                "lastVerifiedAt": "2026-09-30T00:00:00Z",
            },
            "sources": {},
            "current": {"typesense": {"character_id": "bot-id", "name": "Bot"}},
            "lastKnown": {"character_id": "bot-id", "name": "Bot"},
            "fieldHistory": [],
            "availabilityHistory": [],
            "listings": {},
            "metrics": {"latest": {}, "history": []},
            "avatarArchive": {},
        }
        incoming = {
            "character_id": "bot-id",
            "name": "Bot",
            "personality": "rich personality that Typesense did not expose",
        }

        updated, changed = legacy.observe_bot(
            record,
            incoming,
            source="character-api",
            at="2026-09-30T01:00:00Z",
        )

        self.assertFalse(changed)
        self.assertEqual(updated["lastKnown"]["personality"], incoming["personality"])
        self.assertEqual(updated["fieldHistory"], [])
        self.assertEqual(len(saved), 1)

    def test_activity_timestamps_are_volatile(self):
        guard.install_character_api_guard()
        self.assertIn("updatedAt", legacy.VOLATILE_FIELDS)
        self.assertIn("updated_at", legacy.VOLATILE_FIELDS)
        self.assertIn("last_activity_at", legacy.VOLATILE_FIELDS)


if __name__ == "__main__":
    unittest.main()
