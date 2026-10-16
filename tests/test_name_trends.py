import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_name_trends as trends


class NameTrendsTests(unittest.TestCase):
    def test_case_and_whitespace_normalization(self):
        self.assertEqual(trends.normal_name("  LUNA   "), "luna")
        self.assertEqual(trends.normal_name("Luna"), "luna")
        self.assertEqual(trends.normal_name("Ｌｕｎａ"), "luna")
        self.assertEqual(trends.normal_name(" Unknown Bot "), "")
        self.assertEqual(trends.normal_name(None), "")

    def test_aggregates_names_exact_titles_and_title_clues(self):
        text_shards = [
            {
                "a": ["Luna", "creator", False, 0, "", None],
                "b": ["luna ", "creator2", False, 0, "", None],
                "c": ["Dragon", "", False, 0, "", None],
                "d": ["Unknown bot", "", False, 0, "", None],
            }
        ]
        creator_shards = [
            {"creator": {
                "display": "creator",
                "bots": {
                    "a": {"name": "Luna", "title": "Your Werewolf Roommate", "tags": ["Fantasy", "fantasy"]},
                    "b": {"name": "luna", "title": "Your werewolf roommate", "tags": ["Fantasy"]},
                    "c": {"name": "Dragon", "title": "Arranged Marriage with a Vampire", "tags": ["Vampire"]},
                },
            }},
            {"someone": {"bots": {
                "a": {"title": "Old duplicate creator association"},
                "e": {"title": "Stranded on an Island", "tags": []},
            }}},
        ]
        stats, lookup = trends.aggregate(text_shards, creator_shards, "2026-10-16")
        self.assertEqual(stats["coverage"]["nameIndexedBots"], 4)
        self.assertEqual(stats["coverage"]["namedBots"], 3)
        self.assertEqual(stats["distinctNames"], 2)
        self.assertEqual(stats["oneUseNames"], 1)
        self.assertEqual(stats["topNames"][0]["count"], 2)
        self.assertEqual(lookup[trends.name_bucket("luna")]["luna"][1], 2)
        self.assertEqual(stats["coverage"]["titleIndexedBots"], 4)
        self.assertEqual(stats["topRepeatedTitles"][0]["count"], 2)
        self.assertEqual(stats["topRepeatedTitles"][0]["value"], "Your Werewolf Roommate")
        scenarios = {x["label"]: x["count"] for x in stats["titleSituations"]}
        self.assertEqual(scenarios["Roommates / living together"], 2)
        self.assertEqual(scenarios["Werewolves"], 2)
        self.assertEqual(scenarios["Arranged / forced marriage"], 1)
        self.assertEqual(scenarios["Vampires"], 1)
        self.assertEqual(scenarios["Stranded / trapped"], 1)

    def test_missing_shard_rejected_without_silent_partial_statistics(self):
        store = MagicMock()
        store.get_json.return_value = None
        with self.assertRaisesRegex(RuntimeError, "Missing/incompatible"):
            list(trends._read_shards(store, "text-search", 1))

    def test_publishing_writes_shards_before_summary(self):
        store = MagicMock()
        store.key.side_effect = lambda *parts: "/".join(parts)
        lookup = {f"{i:02x}": {} for i in range(trends.NAME_SHARDS)}
        overview = {"schemaVersion": 1}
        trends.publish(store, overview, lookup)
        self.assertEqual(store.put_json.call_count, 65)
        self.assertEqual(
            store.put_json.call_args_list[-1].args[0],
            "indexes/name-trends.json",
        )
        store.flush_usage.assert_called_once_with(force=True)


if __name__ == "__main__":
    unittest.main()
