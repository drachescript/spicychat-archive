"""Safe Archive blocking preset discovery regression tests."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("block_presets",ROOT/"scripts"/"publish_block_presets.py")
pub=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=pub
spec.loader.exec_module(pub)
catalog=json.loads((ROOT/"data"/"block-presets"/"catalog.json").read_text(encoding="utf-8"))
class BlockingPresetTests(unittest.TestCase):
    def test_catalog_integrity(self):
        pub.validate(catalog)
        self.assertEqual(len([p for p in catalog["presets"] if p["kind"]=="franchise"]),31)
        self.assertEqual(len(catalog["characters"]),187)
        self.assertTrue(all(p["enabledByDefault"] is False for p in catalog["presets"]))
        self.assertNotIn("theory",json.dumps(catalog).lower())
    def test_normalization_and_boundaries(self):
        self.assertEqual(pub.norm("König"),"konig")
        self.assertEqual(pub.norm("TaskForce 141"),"task force 141")
        self.assertTrue(pub.phrase_in(pub.norm("Captain John Price"),"John Price"))
        self.assertFalse(pub.phrase_in(pub.norm("Gas prices"),"Price"))
    def test_archived_titles_are_candidates_not_active_aliases(self):
        shard={"creator":{"bots":{
            "1":{"title":"Call of Duty - Captain John Price","status":"public"},
            "2":{"title":"König","status":"deleted"},
            "3":{"title":"Soap dishes","status":"public"},
            "4":{"title":"Pacific Rim — Mako Mori","status":"public"},
            "5":{"title":"Call of Duty: UnknownName","status":"public"},
            "6":{"title":"Call of Duty: UnknownName","status":"deleted"},
            "7":{"title":"Call of Duty: UnknownName","status":"public"}
        }}}
        result=pub.collect([shard],catalog)
        self.assertEqual(result["coverage"]["creatorIndexedBotsExamined"],7)
        self.assertGreaterEqual(result["presetMentions"]["franchise:call-of-duty"],5)
        self.assertEqual(result["characterMentions"]["cod-price"],1)
        self.assertEqual(result["characterMentions"]["pacific-mako"],1)
        self.assertEqual(len([x for x in result["reviewCandidates"]
                              if x["candidateText"]=="unknown name"]),1)
        self.assertNotIn("activeAliases",result)
    def test_review_queue_examples_and_suppression(self):
        ids = ["11111111-1111-4111-8111-%012d" % i for i in (1, 2, 3)]
        shard = {"creator": {"bots": {
            id_: {"title": "Call of Duty: Unlisted Character", "status": "public"}
            for id_ in ids
        }}}
        before = pub.collect([shard], catalog)
        row = next(item for item in before["reviewCandidates"]
                   if item["candidateText"] == "unlisted character")
        self.assertEqual(row["status"], "review-needed")
        self.assertEqual(row["confidence"], "unverified-pattern")
        self.assertEqual(len(row["examples"]), 3)
        self.assertEqual(row["examples"][0]["botId"], ids[0])
        decision = {"schemaVersion": 1, "decisions": [{
            "presetId": "franchise:call-of-duty",
            "candidateText": "Unlisted Character",
            "decision": "rejected",
            "reviewedAt": "2026-11-22T00:00:00Z",
            "evidenceUrl": "https://spicychatarchive.drache.uk/bot/?id="+ids[0],
            "reason": "Generic story title, not a character alias"
        }]}
        after = pub.collect([shard], catalog, decision)
        self.assertEqual(after["reviewCandidates"], [])
        self.assertEqual(after["reviewLog"]["rejected"], 1)
        invalid = {"schemaVersion":1,"decisions":[{**decision["decisions"][0],
                    "decision":"accepted"}]}
        with self.assertRaisesRegex(ValueError, "promoted"):
            pub.collect([shard], catalog, invalid)

    def test_unchanged_snapshot_not_rewritten(self):
        store=Mock()
        store.key.side_effect=lambda *parts:"/".join(parts)
        store.get_json.return_value=None
        p={"schemaVersion":1,"catalogVersion":1,"coverage":{"creatorIndexedBotsExamined":1}}
        first=pub.publish(store,p)
        store.get_json.return_value=first
        store.put_json.reset_mock()
        self.assertEqual(pub.publish(store,p),first)
        store.put_json.assert_not_called()
if __name__=="__main__":unittest.main()
