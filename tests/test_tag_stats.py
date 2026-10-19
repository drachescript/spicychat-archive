import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_tag_stats as tags


class TagStatsTests(unittest.TestCase):
    def source(self):
        return [
            {
                "alice": {"display": "Alice", "bots": {
                    "id1": {"tags": ["Fantasy", "romance", " FANTASY "]},
                    "id2": {"tags": ["romance", "NSFW"]},
                    "id3": {"tags": []},
                }},
                "bob": {"display": "Bob", "bots": {
                    "id4": {"tags": ["Romance", "Fantasy"]},
                    "id5": {"tags": ["WRONG", {}, None, "", " "]},
                    "id2": {"tags": ["Unwanted duplicate"]},
                }},
            },
            {"carol": {"display": "Carol", "bots": {
                "id6": {"tags": ["Romance"]},
                "id7": {"tags": "bad json shape"},
            }}},
        ]

    def test_normalization_hash(self):
        self.assertEqual(tags.norm_tag("  ＦＡＮＴＡＳＹ  "), "fantasy")
        self.assertEqual(tags.norm_tag("Big   Boobs"), "big boobs")
        self.assertEqual(tags.norm_tag({}), "")
        self.assertEqual(tags.bucket_for("romance"), tags.bucket_for("romance"))

    def test_counts_distinct_bots_unique_tags_pairs_and_creators(self):
        overview, shards = tags.aggregate(self.source(), "2026-10-19T20:00:00Z")
        self.assertEqual(overview["coverage"]["indexedBots"], 7)
        self.assertEqual(overview["coverage"]["taggedBots"], 5)
        self.assertEqual(overview["coverage"]["tagAssignments"], 8)
        self.assertEqual(overview["distinctTags"], 4)
        def lookup(key):
            return shards[tags.bucket_for(key)][key]
        self.assertEqual(lookup("romance")["count"], 4)
        self.assertEqual(lookup("romance")["creatorCount"], 3)
        self.assertEqual(lookup("romance")["withTags"]["fantasy"], 2)
        self.assertEqual(lookup("fantasy")["withTags"]["romance"], 2)
        self.assertEqual(lookup("fantasy")["count"], 2)
        self.assertEqual(lookup("nsfw")["count"], 1)
        self.assertNotIn("unwanted duplicate", shards[tags.bucket_for("unwanted duplicate")])
        self.assertEqual(lookup("romance")["topCreators"][0], {"name": "Alice", "count": 2})
        self.assertEqual(overview["oneUseTags"], 2)
        self.assertEqual(overview["topPairs"][0]["count"], 2)
        self.assertEqual(overview["rareTags"][0]["count"], 1)

    def test_fresh_history_does_not_invent_older_snapshots(self):
        stats = {"generatedAt": "2026-10-19T20:00:00Z", "coverage": {"indexedBots": 7}}
        current = tags.updated_history(None, stats, {"romance": 4})
        self.assertEqual(len(current["snapshots"]), 1)
        self.assertEqual(current["snapshots"][0]["date"], "2026-10-19")
        same_day = tags.updated_history(current, stats, {"romance": 5})
        self.assertEqual(len(same_day["snapshots"]), 1)
        self.assertEqual(same_day["snapshots"][0]["counts"]["romance"], 5)
        next_day = tags.updated_history(same_day, {**stats, "generatedAt": "2026-10-20T01:00:00Z"}, {"romance": 6})
        self.assertEqual(len(next_day["snapshots"]), 2)

    def test_history_does_not_silently_reset_schema(self):
        stats = {"generatedAt": "2026-10-19", "coverage": {"indexedBots": 1}}
        with self.assertRaises(RuntimeError):
            tags.updated_history({"schemaVersion": 99, "snapshots": []}, stats, {})

    def test_publisher_finishes_with_public_overview(self):
        stats, lookup = tags.aggregate(self.source(), "2026-10-19T20:00:00Z")
        store = MagicMock()
        store.key.side_effect = lambda *parts: "/".join(parts)
        store.get_json.return_value = None
        tags.publish(store, stats, lookup)
        self.assertEqual(store.put_json.call_count, 18)
        self.assertEqual(store.put_json.call_args_list[-2].args[0], "indexes/tag-stats-history.json")
        self.assertEqual(store.put_json.call_args_list[-1].args[0], "indexes/tag-stats.json")
        store.flush_usage.assert_called_once_with(force=True)


if __name__ == "__main__":
    unittest.main()
