import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import manual_discovery_cooldown as mod


class ManualDiscoveryCooldownTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 7, 18, tzinfo=timezone.utc)

    def row(self, hours_ago, pages, kind="archive-run"):
        return {
            "kind": kind,
            "at": (self.now - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z"),
            "exploration": {"pageBudget": pages, "pagesCompleted": min(pages, 701),
                            "naturalEnd": pages > 701},
        }

    def test_large_manual_tier_cooldowns(self):
        stats = {"runs": [self.row(3, 1000)]}
        for pages, hours in [(1500, 12), (2500, 24)]:
            with self.subTest(pages=pages):
                result = mod.check_cooldown(pages, stats, self.now)
                self.assertFalse(result["allowed"])
                self.assertEqual(result["hours"], hours)
                self.assertIn("nextAt", result)
        self.assertTrue(mod.check_cooldown(1000, stats, self.now)["allowed"])

    def test_previous_large_sweep_blocks_smaller_manual_request(self):
        previous = {"runs": [self.row(8, 2500)]}
        self.assertFalse(mod.check_cooldown(1000, previous, self.now)["allowed"])
        self.assertFalse(mod.check_cooldown(1500, previous, self.now)["allowed"])
        self.assertEqual(mod.check_cooldown(1000, previous, self.now)["hours"], 24)
        later = self.now + timedelta(hours=17)
        self.assertTrue(mod.check_cooldown(1000, previous, later)["allowed"])

    def test_1500_waits_12_hours_even_after_natural_end(self):
        state = {"runs": [self.row(11, 1500)]}
        self.assertFalse(mod.check_cooldown(1500, state, self.now)["allowed"])
        self.assertTrue(mod.check_cooldown(1500, state, self.now + timedelta(hours=1))["allowed"])

    def test_scheduled_maintenance_is_not_a_manual_sweep(self):
        stats = {"runs": [self.row(7, 2500), self.row(0.5, 0)]}
        previous = mod.last_manual_sweep(stats)
        self.assertEqual(previous[1], 2500)
        self.assertFalse(mod.check_cooldown(1500, stats, self.now)["allowed"])

    def test_no_sweeps_and_clean_expired_history_allow_run(self):
        self.assertTrue(mod.check_cooldown(2500, {"runs": []}, self.now)["allowed"])
        older = {"runs": [self.row(30, 2500)]}
        self.assertTrue(mod.check_cooldown(2500, older, self.now)["allowed"])

    def test_invalid_selection_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid discovery target"):
            mod.check_cooldown(10000, {"runs": []}, self.now)


if __name__ == "__main__":
    unittest.main()
