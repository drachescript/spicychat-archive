import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "sca_public_growth",
    ROOT / "scripts" / "public_growth.py",
)
mod = importlib.util.module_from_spec(spec)
sys.modules["sca_public_growth"] = mod
spec.loader.exec_module(mod)


class PublicGrowthTests(unittest.TestCase):
    def test_uses_public_index_not_archive_backfill(self):
        stats = {
            "generatedAt": "2026-09-30T03:30:00Z",
            "runs": [
                {
                    "kind": "archive-run",
                    "at": "2026-09-29T03:00:00Z",
                    "totalBots": 100_000,
                    "publicIndexBots": 1_390_000,
                },
                {
                    "kind": "archive-run",
                    "at": "2026-09-30T03:30:00Z",
                    "totalBots": 1_390_000,
                    "publicIndexBots": 1_390_800,
                },
            ],
        }
        growth = mod.build_public_growth(stats)
        self.assertEqual(growth["periods"]["24h"]["net"], 800)
        self.assertLess(growth["averagePerDay"], 1_000)

    def test_negative_public_growth_is_preserved(self):
        stats = {
            "runs": [
                {"kind": "archive-run", "at": "2026-09-29T00:00:00Z", "publicIndexBots": 1_000},
                {"kind": "archive-run", "at": "2026-09-30T00:00:00Z", "publicIndexBots": 950},
            ]
        }
        growth = mod.build_public_growth(stats)
        self.assertEqual(growth["periods"]["24h"]["net"], -50)
        self.assertEqual(growth["averagePerDay"], -50.0)

    def test_short_history_does_not_claim_seven_day_coverage(self):
        stats = {
            "runs": [
                {"kind": "archive-run", "at": "2026-09-29T00:00:00Z", "publicIndexBots": 1_000},
                {"kind": "archive-run", "at": "2026-09-30T00:00:00Z", "publicIndexBots": 1_100},
            ]
        }
        growth = mod.build_public_growth(stats)
        self.assertTrue(growth["periods"]["24h"]["complete"])
        self.assertFalse(growth["periods"]["7d"]["complete"])
        self.assertEqual(growth["observedNet"], 100)


if __name__ == "__main__":
    unittest.main()
