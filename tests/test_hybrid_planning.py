import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "ops" / "hetzner-mirror"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, OPS / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


popularity = load_module("export-bot-views.py", "popularity_export")
cold = load_module("plan-cold-records.py", "cold_planner")


class HybridPlanningTests(unittest.TestCase):
    def test_sql_uses_sampling_weight_and_fixed_limits(self):
        sql = popularity.query_sql(30, 2000, 0)
        self.assertIn("SUM(_sample_interval)", sql)
        self.assertIn("sca_bot_views_v1", sql)
        self.assertIn("LIMIT 2000 OFFSET 0", sql)
        with self.assertRaises(ValueError):
            popularity.query_sql(0, 1000, 0)

    def test_export_rejects_malformed_or_partial_records(self):
        good = "b376faf0-8c37-4b37-8861-8ca68cdd64b6"
        self.assertEqual(popularity.validate_rows(
            {"data": [{"bot_id": good, "views": "12"}]}
        ), [(good, 12)])
        for response in [{}, {"data": [{"bot_id": good, "views": -1}]},
                         {"data": [{"bot_id": "junk", "views": 10}]}]:
            with self.assertRaises(ValueError):
                popularity.validate_rows(response)

    def test_cold_plan_only_considers_old_individual_bot_objects(self):
        first = "b376faf0-8c37-4b37-8861-8ca68cdd64b6"
        hot = "0004d9d6-473d-4c4c-b536-d25e184d5cec"
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for bot_id in [first, hot]:
                p = root / "bots" / bot_id[:2] / (bot_id + ".json")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps({"id": bot_id}))
                old = (now - timedelta(days=120)).timestamp()
                os.utime(p, (old, old))
            candidates = cold.rank_cold(root, {first: 1, hot: 100}, min_age=90,
                                         max_views=2, now=now)
            self.assertEqual([x["id"] for x in candidates], [first])
            self.assertTrue(all(row["relativePath"].startswith("bots/") for row in candidates))

    def test_policy_is_fail_closed_and_private(self):
        policy = json.loads((OPS / "hybrid-policy.json").read_text())
        self.assertEqual(policy["mode"], "observe-only")
        self.assertEqual(policy["r2TargetBytes"], 8_000_000_000)
        self.assertEqual(policy["mirrorRoot"], "E:\\Bots\\Spicychat_Archive")
        self.assertFalse(policy["offloadEnabled"])
        self.assertFalse(policy["deleteR2Objects"])
        self.assertFalse(policy["mirrorVerified"])
        self.assertTrue(policy["dataSafety"]["requireIndependentBackup"])


if __name__ == "__main__":
    unittest.main()
