import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rulebook_archive as rules


class RulebookArchiveTests(unittest.TestCase):
    def setUp(self):
        self.id = "a21710ec-3a34-4e45-aa62-d291bbee5744"
        self.doc = {
            "rulebook_id": self.id, "name": "Public sample",
            "description": "Public listing text", "creator_username": "example",
            "tags": ["Focus", "Slow Burn"], "num_rules": 12,
            "createdAt": 1793586900,
            "rules": [{"id": "secret", "content": "private rule text"}],
            "variables": [{"name": "hidden", "value": "secret"}],
            "private_config": {"token": "should never appear"},
        }

    def test_uuid_only_public_rulebook_ids(self):
        self.assertEqual(rules.rulebook_id(self.doc), self.id)
        self.assertIsNone(rules.rulebook_id({"id": "../../private"}))
        self.assertIsNone(rules.rulebook_id({"rulebook_id": "secret"}))

    def test_public_whitelist_never_saves_rule_bodies_or_secret_variables(self):
        value = rules.public_metadata(self.doc)
        self.assertEqual(value["name"], "Public sample")
        self.assertEqual(value["num_rules"], 12)
        self.assertNotIn("rules", value)
        self.assertNotIn("variables", value)
        self.assertNotIn("private_config", value)
        record, changed = rules.build_record(None, self.doc, "2026-11-01T00:00:00Z")
        self.assertTrue(changed)
        self.assertNotIn("private rule text", str(record))
        self.assertEqual(len(record["versions"]), 1)
        again, changed = rules.build_record(record, self.doc, "2026-11-02T00:00:00Z")
        self.assertFalse(changed)
        self.assertEqual(len(again["versions"]), 1)

    def test_status_change_only_means_no_longer_public(self):
        record, _ = rules.build_record(None, self.doc, "2026-11-01T00:00:00Z")
        self.assertTrue(rules.apply_disappearance(record, "2026-11-02T00:00:00Z"))
        self.assertEqual(record["status"]["current"], "not-public")
        self.assertFalse(rules.apply_disappearance(record, "2026-11-02T00:00:01Z"))
        restored, change = rules.build_record(record, self.doc, "2026-11-03T00:00:00Z")
        self.assertTrue(change)
        self.assertEqual(restored["status"]["current"], "public")
        self.assertEqual(len(restored["versions"]), 1)

    def test_uses_separate_public_collection(self):
        search = rules.query(page=2, per_page=48)
        self.assertEqual(search["collection"], "rulebooks_public")
        self.assertEqual(search["filter_by"], "application_ids:=spicychat")
        self.assertNotIn("creator_user_id", search["filter_by"])

    def test_public_key_must_not_fall_back_to_bot_or_lorebook(self):
        client = Mock()
        client.guest_user_id = "guest"
        client.timeout = 5
        client.session.get.return_value.ok = True
        client.session.get.return_value.content = b"{}"
        client.session.get.return_value.json.return_value = {
            "typesenseConfig": {
                "apiKeyLorebook": "lorebook-only",
                "apiKey": "character-only",
                "collectionNameRulebook": "rulebooks_public",
            }
        }
        config = {"typesense": {"api_key": "character-only"}}
        self.assertIsNone(rules.configure_public_key(client, config))
        self.assertNotEqual(client.typesense_key, "character-only")
        client.session.get.return_value.json.return_value = {
            "typesenseConfig": {
                "apiKeyRulebook": "rulebook-public-scoped",
                "collectionNameRulebook": "rulebooks_public",
            }
        }
        self.assertEqual(rules.configure_public_key(client, config), "rulebooks_public")
        self.assertEqual(client.typesense_key, "rulebook-public-scoped")

    def test_index_summary_is_listing_only(self):
        record, _ = rules.build_record(None, self.doc, "2026-11-01T00:00:00Z")
        s = rules.summary(record)
        self.assertEqual(s["numRules"], 12)
        self.assertEqual(s["creator"], "example")
        self.assertNotIn("rules", s)


if __name__ == "__main__":
    unittest.main()
