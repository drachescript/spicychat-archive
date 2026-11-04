import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import repair_text_search_inventory as repair_mod


class IndexOnlyRepairTests(unittest.TestCase):
    def test_plan_preserves_search_entries_without_stored_file(self):
        add, preserve = repair_mod.plan({"a", "b"}, {"a", "c"})
        self.assertEqual(add, {"c"})
        self.assertEqual(preserve, {"b"})

    def test_dry_run_never_writes_and_apply_never_removes_search_history(self):
        version = repair_mod.SCHEMA_VERSION
        expected = [f"{i:02x}" for i in range(repair_mod.TEXT_SEARCH_SHARDS)]
        existing = "a" * 32
        missing = "b" * 32

        class Store:
            discovery_order = [existing, missing]

            def __init__(self):
                self.writes = []
                self.records = {}

            def key(self, *parts):
                return "/".join(parts)

            def bot_key(self, bot_id):
                return "bots/" + bot_id

            def get_json(self, key, default=None):
                if key == "indexes/text-search.json":
                    return {"schemaVersion": version, "shards": expected}
                if key.startswith("indexes/text-search/"):
                    bucket = key.rsplit("/", 1)[-1][:-5]
                    bots = {existing: {"name": "Historic"}} if bucket == repair_mod.text_search_bucket(existing) else {}
                    return {"schemaVersion": version, "bots": bots}
                if key == self.bot_key(missing):
                    return {"id": missing, "lastKnown": {"name": "Recovered"}}
                return default

            def put_json(self, key, value, **kwargs):
                self.writes.append((key, value))

            def load_discovery_order(self):
                pass

            def flush_usage(self, force=False):
                pass

        store = Store()
        with patch.object(repair_mod, "inventory_bot_file_ids",
                          return_value=({existing, missing}, {"r2ListPages": 1})), \
             patch.object(repair_mod, "text_search_meta", return_value={"name": "Recovered"}), \
             patch.object(repair_mod, "build_audit", return_value={"complete": True}):
            preview = repair_mod.repair(store, apply=False, max_changes=5)
            self.assertTrue(preview["dryRun"])
            self.assertEqual(preview["missingFromSearch"], 1)
            self.assertEqual(len(store.writes), 0)
            result = repair_mod.repair(store, apply=True, max_changes=5)
            self.assertEqual(result["addedToSearch"], 1)
            self.assertEqual(result["remainingSearchOnlyEntries"], 0)
            self.assertEqual(result["indexedAfter"], 2)
            self.assertTrue(any(key.endswith("text-search-repair.json") for key, _ in store.writes))

    def test_safety_cap_rejects_additions_without_any_writes(self):
        version = repair_mod.SCHEMA_VERSION
        class Store:
            writes = []
            def key(self, *parts):
                return "/".join(parts)
            def get_json(self, key, default=None):
                if key == "indexes/text-search.json":
                    return {"schemaVersion": version,
                            "shards": [f"{i:02x}" for i in range(repair_mod.TEXT_SEARCH_SHARDS)]}
                return {"schemaVersion": version, "bots": {}}
            def put_json(self, *args, **kwargs):
                self.writes.append(args)
        store = Store()
        with patch.object(repair_mod, "inventory_bot_file_ids", return_value=({"abc"}, {})):
            with self.assertRaisesRegex(RuntimeError, "safety cap"):
                repair_mod.repair(store, apply=True, max_changes=0)
        self.assertEqual(store.writes, [])


if __name__ == "__main__":
    unittest.main()
