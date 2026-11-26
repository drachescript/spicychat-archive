import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from coverage_sweep import select_missing, safe_budget

def uid(n): return "11111111-1111-4111-8111-%012d" % n
class CoverageSweepTests(unittest.TestCase):
    def test_missing_only_rotates_and_excludes_suspects(self):
        a,b,c,d = [uid(i) for i in range(4)]
        rows, positions = select_missing([a,b,c,d], {a}, {c}, 2, 2)
        self.assertEqual(rows,[d,b])
        self.assertEqual(positions,[0,2])
    def test_quota_budget_fails_closed(self):
        self.assertEqual(safe_budget({},{}),0)
        status={"selectedMode":"r2","r2ReadAllowed":True,"usage":{"classA":500000,"classB":1000000,"storageBytes":6000000000}}
        self.assertEqual(safe_budget(status,{}),50000)
        status["usage"]["classA"]=940000
        self.assertEqual(safe_budget(status,{}),0)
    def test_profile_keeps_rolling_maintenance_enabled(self):
        from r2_accel_runner import _apply_coverage_sweep_profile
        p=_apply_coverage_sweep_profile({"crawler":{},"listings":{"latest":"createdAt:desc"}},50000)["crawler"]
        self.assertTrue(p["coverage_sweep_only"])
        self.assertTrue(p["scheduled_latest_only"])
        self.assertTrue(p["maintenance_verification_enabled"])
        self.assertFalse(p["image_archive_enabled"])
        self.assertEqual(p["maintenance_verification_budget"],50000)
