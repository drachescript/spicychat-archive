"""Guard regressions for Archive 8 reporting and safe repairs."""
import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import r2_guard
from repair_text_search_inventory import plan


class Archive8IntegrityTests(unittest.TestCase):
    def test_billing_period_before_and_after_reset(self):
        date = lambda day: datetime(2026, 10, day, tzinfo=timezone.utc)
        self.assertEqual(r2_guard.billing_period_start(date(23), 28), "2026-09-28T00:00:00Z")
        self.assertEqual(r2_guard.billing_period_start(date(28), 28), "2026-10-28T00:00:00Z")
        self.assertEqual(r2_guard.billing_period_start(date(31), 28), "2026-10-28T00:00:00Z")
        self.assertEqual(r2_guard.billing_period_start(datetime(2027, 1, 1, tzinfo=timezone.utc), 28),
                         "2026-12-28T00:00:00Z")

    def test_billing_day_is_restricted_to_unambiguous_monthly_days(self):
        with self.assertRaises(ValueError):
            r2_guard.billing_period_start(day=29)

    def test_targeted_repair_set_comparison(self):
        add, remove = plan({"saved", "stale"}, {"saved", "missing"})
        self.assertEqual(add, {"missing"})
        self.assertEqual(remove, {"stale"})

    def test_site_uses_v2_full_inventory(self):
        app = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        home = (ROOT / "assets" / "home.js").read_text(encoding="utf-8")
        self.assertIn("idAudit?.schemaVersion===2", app)
        self.assertIn("inventory.materializedIndexIds", home)
        self.assertNotIn("arithmetic public/archive", app)
        self.assertNotIn("is an arithmetic gap", home)

    def test_repair_keeps_search_only_metadata(self):
        from unittest.mock import patch
        import repair_text_search_inventory as repair_script
        from site_indexes import SCHEMA_VERSION, text_search_bucket
        stale_id = "old-history"
        new_id = next("newly-saved-" + str(i) for i in range(2000)
                      if text_search_bucket("newly-saved-" + str(i)) == text_search_bucket(stale_id))
        shard_names = [f"{i:02x}" for i in range(64)]
        paths = {
            ("indexes", "text-search.json"): {
                "schemaVersion": SCHEMA_VERSION, "shards": shard_names, "botCount": 1
            }
        }
        for bucket in shard_names:
            paths[("indexes", "text-search", bucket + ".json")] = {
                "schemaVersion": SCHEMA_VERSION,
                "bots": {stale_id: ["Last saved name", "creator"]} if bucket == text_search_bucket(stale_id) else {}
            }
        class Store:
            def __init__(self):
                self.writes = []
                self.discovery_order = [stale_id, new_id]
            def load_discovery_order(self):
                return self.discovery_order
            def key(self, *parts):
                return "/".join(parts)
            def bot_key(self, bot_id):
                return "bots/" + bot_id + ".json"
            def get_json(self, key, default=None):
                if key == self.bot_key(new_id):
                    return {"id": new_id, "lastKnown": {"name": "New bot"}}
                for parts, value in paths.items():
                    if self.key(*parts) == key:
                        return value
                return default
            def put_json(self, key, value, **kwargs):
                self.writes.append((key, value))
            def flush_usage(self, **kwargs):
                pass

        store = Store()
        with patch.object(repair_script, "inventory_bot_file_ids", return_value=({new_id}, {"r2ListPages": 1})):
            preview = repair_script.repair(store)
            self.assertEqual(preview["missingFromSearch"], 1)
            self.assertEqual(preview["preservedSearchOnlyEntries"], 1)
            self.assertFalse(store.writes)
            result = repair_script.repair(store, apply=True)
            self.assertEqual(result["indexedAfter"], 2)
            self.assertEqual(result["remainingSearchOnlyEntries"], 1)
            self.assertTrue(any(key.endswith("discovery-id-audit.json") for key, _ in store.writes))
            shard_writes = [data for key, data in store.writes if key.endswith("/" + text_search_bucket(stale_id) + ".json")]
            self.assertTrue(shard_writes)
            self.assertIn(stale_id, shard_writes[-1]["bots"])



if __name__ == "__main__":
    unittest.main()
