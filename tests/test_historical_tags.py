import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_historical_tags as oldtags


class HistoricalTagsTests(unittest.TestCase):
    def rows(self):
        return [
            {
                "id": "old-bot",
                "name": "Original Bot",
                "creator": "Creator One",
                "path": "tags",
                "from": '["Step-Family","Fantasy","Romantic"]',
                "to": '["Fantasy","Romantic"]',
                "at": "2026-10-21T12:00:00Z",
            },
            {
                "id": "deleted-bot",
                "name": "Missing Bot",
                "creator": "Creator Two",
                "path": "lastKnown.tags",
                "from": '["Step-Family","Vampire"]',
                "to": '["Vampire"]',
                "at": "2026-10-22T12:00:00Z",
            },
        ]

    def test_serialized_changes_and_deduplicated_memberships(self):
        membership, skipped = oldtags.from_changes(self.rows() + [self.rows()[0]])
        self.assertEqual(skipped, 0)
        self.assertEqual(set(membership["step-family"]), {"old-bot", "deleted-bot"})
        self.assertEqual(len(membership["fantasy"]), 1)

    def test_truncated_or_malformed_history_does_not_invent_tags(self):
        values, good = oldtags.tags_from_change_value('["Step-Family","Something else"…')
        self.assertFalse(good)
        self.assertEqual(values, [])
        values, good = oldtags.tags_from_change_value('["Step-Family","Fantasy"]')
        self.assertTrue(good)
        self.assertEqual(values, ["Step-Family", "Fantasy"])
        values, good = oldtags.tags_from_change_value("plain text tag")
        self.assertFalse(good)
        self.assertEqual(values, [])

    def test_previous_tags_match_current_and_deleted_archived_bots(self):
        creators = [{
            "one": {"display": "Creator One", "bots": {
                "old-bot": {
                    "name": "Original Bot", "title": "Updated bot",
                    "tags": ["Fantasy", "Romantic"],
                    "historicalTags": ["Step-Family"],
                    "status": "public",
                },
                "still-tagged": {
                    "name": "Unchanged Bot", "tags": ["Vampire"],
                    "historicalTags": ["Vampire"],
                    "status": "public",
                },
                "new-observed": {
                    "name": "Another Bot", "tags": ["Fantasy"],
                    "historicalTags": ["Watersport"],
                    "status": "deleted",
                },
            }},
            "two": {"display": "Creator Two", "bots": {
                "deleted-bot": {
                    "name": "Missing Bot", "tags": ["Vampire"],
                    "status": "deleted",
                },
            }},
        }]
        overview, lookup = oldtags.collect(self.rows(), creators, "2026-10-21T12:00:00Z")
        def members(tag):
            return lookup[oldtags.bucket_for(tag)].get(tag, {}).get("bots", [])
        self.assertEqual(
            {r["id"] for r in members("step-family")},
            {"old-bot", "deleted-bot"},
        )
        self.assertEqual(len(members("fantasy")), 0)
        self.assertEqual(len(members("vampire")), 0)
        self.assertEqual([r["id"] for r in members("watersport")], ["new-observed"])
        self.assertEqual(members("step-family")[0]["status"], "deleted")
        self.assertIn("step-family", overview["formerTagLabels"])
        self.assertEqual(overview["formerTagMemberships"], 3)

    def test_history_index_refuses_missing_or_corrupt_month(self):
        store = MagicMock()
        store.key.side_effect = lambda *parts: "/".join(parts)
        store.get_json.side_effect = [
            {"schemaVersion": oldtags.SITE_SCHEMA, "months": {"2026-10": 2}},
            None
        ]
        with self.assertRaisesRegex(RuntimeError, "Missing Archive changes"):
            oldtags.load_changes(store)

    def test_index_publication_writes_shards_before_manifest(self):
        store = MagicMock()
        store.key.side_effect = lambda *parts: "/".join(parts)
        overview, shards = oldtags.collect(self.rows(), [], "2026-10-21T12:00:00Z")
        oldtags.publish(store, overview, shards)
        self.assertEqual(store.put_json.call_count, 17)
        self.assertEqual(
            store.put_json.call_args_list[-1].args[0],
            "indexes/historical-tags.json",
        )
        store.flush_usage.assert_called_once_with(force=True)


if __name__ == "__main__":
    unittest.main()
