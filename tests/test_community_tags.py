"""Regressions for Archive-to-QoL theme evidence. No R2 credentials needed."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("community_tags", ROOT / "scripts" / "publish_community_tags.py")
ct = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ct
spec.loader.exec_module(ct)
ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class CommunityThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tax = ct.load_json("taxonomy.json")
        cls.rules = ct.load_json("rules.json")
        cls.reviews = ct.load_json("reviews.json")

    def test_catalog_is_all_scopes_and_historical(self):
        ids = ct.validate(self.tax, self.rules, self.reviews)
        self.assertIn("cheating", ids)
        self.assertIn("ntr", ids)
        self.assertIn("step-family", ids)
        self.assertIn("emotional-affair", ids)
        self.assertEqual(self.tax["coverage"]["bot"]["catalogEntries"], 91)
        self.assertGreaterEqual(self.tax["coverage"]["lorebook"]["catalogEntries"], 227)
        self.assertGreaterEqual(self.tax["coverage"]["rulebook"]["catalogEntries"], 60)
        self.assertGreaterEqual(len(ids), 300)

    def scan(self, title, *, current=None, former=None, decisions=None):
        meta = {"name": "Sample", "title": title, "tags": current or [],
                "historicalTags": former or [], "status": "deleted"}
        return ct.scan_meta(ID, meta, self.rules,
                            {x["id"] for x in self.tax["tags"] if "bot" in x["scopes"]},
                            decisions or {})

    def test_clear_untagged_title_yields_only_candidate(self):
        row = self.scan("Your cheating wife")
        self.assertEqual(row["status"], "deleted")
        self.assertEqual([s["tagId"] for s in row["signals"]], ["cheating"])
        self.assertEqual(row["signals"][0]["confidence"], "candidate")

    def test_title_denials_and_unrelated_uses_do_not_accuse(self):
        for title in ("Your wife did not cheat at chess",
                      "Not cheating, it's a misunderstanding",
                      "Political affairs and the elections",
                      "Your girlfriend's secret birthday surprise"):
            with self.subTest(title=title):
                self.assertIsNone(self.scan(title))

    def test_current_official_tag_is_not_inferred_twice(self):
        self.assertIsNone(self.scan("Your cheating wife", current=["Cheating"]))

    def test_historic_removed_tag_is_not_current_verification(self):
        result = self.scan("A quiet story", former=["Cheating", "NTR"],
                           current=["Romantic"])
        self.assertEqual({s["tagId"] for s in result["signals"]}, {"cheating", "ntr"})
        self.assertTrue(all(s["confidence"] == "historical-only" for s in result["signals"]))

    def test_verified_approval_requires_matching_public_revision(self):
        meta = {"name": "Sample", "title": "Your cheating wife", "tags": []}
        fingerprint = ct.metadata_fingerprint(meta)
        d = {"botId": ID, "tagId": "cheating", "decision": "approve",
             "fingerprint": fingerprint, "reviewedAt": "2026-11-17T00:00:00Z",
             "evidenceUrl": "https://spicychatarchive.drache.uk/bot/?id=" + ID}
        row = self.scan(meta["title"], decisions={(ID, "cheating"): d})
        self.assertEqual(row["signals"][0]["kind"], "reviewed")
        self.assertEqual(row["signals"][0]["confidence"], "verified")
        changed = self.scan("Changed title", decisions={(ID, "cheating"): d})
        self.assertIsNone(changed)

    def test_reject_suppresses_candidate_not_historical_evidence(self):
        meta = {"name": "Sample", "title": "Your cheating wife", "tags": []}
        d = {"botId": ID, "tagId": "cheating", "decision": "reject",
             "fingerprint": ct.metadata_fingerprint(meta)}
        row = self.scan(meta["title"], former=["Cheating"],
                        decisions={(ID, "cheating"): d})
        self.assertEqual(len(row["signals"]), 1)
        self.assertEqual(row["signals"][0]["kind"], "historical-tag")

    def test_sparse_build_and_no_raw_title_leaks(self):
        creator = {"sample": {"bots": {
            ID: {"title": "Your cheating wife", "name": "Sample", "tags": [],
                 "status": "public"},
            OTHER: {"title": "A calm picnic", "name": "Sample", "tags": [],
                    "status": "deleted"}}}}
        records, coverage = ct.build([creator], self.tax, self.rules, self.reviews)
        self.assertEqual(coverage["indexedBots"], 2)
        self.assertEqual(coverage["signalBots"], 1)
        self.assertIn(ID, records)
        self.assertNotIn(OTHER, records)
        self.assertNotIn("Your cheating wife", json.dumps(records))
        shards = ct.partitions(records)
        self.assertIn(ID, shards["a"])
        self.assertEqual(len(shards), 16)

    def test_snapshot_write_order_and_skip_unchanged(self):
        store = Mock()
        store.key.side_effect = lambda *parts: "/".join(parts)
        store.get_json.return_value = None
        items = {ID: {"status": "public", "signals": [
            {"tagId": "cheating", "kind": "rule-candidate", "confidence": "candidate"}]}}
        first = ct.publish(store, items, {"indexedBots": 1}, self.rules)
        writes = [call.args[0] for call in store.put_json.call_args_list]
        self.assertEqual(len(writes), 17)
        self.assertEqual(writes[-1], "indexes/community-tags/manifest.json")
        store.get_json.return_value = first
        store.put_json.reset_mock()
        second = ct.publish(store, items, {"indexedBots": 1}, self.rules)
        self.assertEqual(first, second)
        store.put_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
