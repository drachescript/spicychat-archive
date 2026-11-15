"""Guardrails for the automated catalog/verification schedule."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import r2_archive_maintenance as maintenance
import r2_accel_runner as runner
import manual_discovery_cooldown as cooldown


class AutomatedArchiveScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/archive.yml").read_text(encoding="utf-8")

    def test_three_distinct_schedules_for_maintenance_deep_and_verification(self):
        workflow = self.workflow
        for schedule in ('37 */6 * * *', '17 2 * * 1,3,5', '17 10 * * *'):
            self.assertEqual(workflow.count(f'cron: "{schedule}"'), 1)
        self.assertIn("github.event.schedule == '17 2 * * 1,3,5'", workflow)
        self.assertIn("github.event.schedule == '17 10 * * *'", workflow)
        self.assertIn("github.event.schedule == '37 */6 * * *'", workflow)

    def test_deep_sweep_respects_cooldown_and_uses_bounded_catalog_profile(self):
        workflow = self.workflow
        self.assertIn('scripts/manual_discovery_cooldown.py', workflow)
        self.assertIn('SPICYCHAT_ARCHIVE_CATALOG_AUDIT:', workflow)
        self.assertIn('SPICYCHAT_ARCHIVE_MANUAL_PAGES:', workflow)
        self.assertIn('SPICYCHAT_ARCHIVE_EXPLORE_GUARD_CAP:', workflow)
        self.assertIn('scripts/r2_guard_adaptive.py', workflow)
        self.assertEqual(cooldown.tier_cooldown_hours(2500), 24)
        base = {"crawler": {"explore_pages_per_run": 500, "explore_pages_max": 2500}}
        profile, target, maximum = runner._apply_manual_archive_profile(
            base, 2500, catalog_audit=True,
        )
        self.assertEqual((target, maximum), (2500, 2500))
        self.assertTrue(profile["crawler"]["catalog_audit_run"])
        self.assertFalse(profile["crawler"]["maintenance_verification_enabled"])
        self.assertFalse(profile["crawler"]["image_archive_enabled"])
        self.assertEqual(profile["crawler"]["missing_verification_budget"], 2000)

    def test_verification_only_never_sweeps_catalog(self):
        workflow = self.workflow
        self.assertIn('SPICYCHAT_ARCHIVE_VERIFY_MISSING:', workflow)
        profile = runner._apply_missing_verification_profile({"crawler": {}}, budget=10000)
        self.assertTrue(profile["crawler"]["verify_missing_only"])
        self.assertTrue(profile["crawler"]["scheduled_latest_only"])
        self.assertFalse(profile["crawler"]["maintenance_verification_enabled"])
        self.assertFalse(profile["crawler"]["image_archive_enabled"])
        self.assertEqual(profile["crawler"]["missing_verification_budget"], 10000)

    def test_automatic_catchup_starts_and_restarts_without_manual_kickoff(self):
        should = maintenance.should_start_missing_catchup
        self.assertFalse(should(0, None))
        self.assertFalse(should(999, None))
        self.assertTrue(should(1000, None))
        self.assertTrue(should(13000, {"enabled": False}))
        self.assertTrue(should(13000, {}))
        self.assertFalse(should(13000, {"enabled": True, "perRunBudget": 5000}))


if __name__ == "__main__":
    unittest.main()
