import os
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import r2_accel_runner as runner
import r2_guard_adaptive as guard


class ManualArchiveProfileTests(unittest.TestCase):
    def base_config(self):
        return {
            "crawler": {
                "explore_pages_per_run": 500,
                "explore_pages_max": 1000,
                "maintenance_verification_enabled": True,
                "image_archive_enabled": True,
            }
        }

    def test_manual_profile_defaults_to_discovery_only(self):
        config, target, configured_max = runner._apply_manual_archive_profile(
            self.base_config(),
            1000,
        )

        self.assertEqual(target, 1000)
        self.assertEqual(configured_max, 1000)
        self.assertTrue(config["crawler"]["manual_discovery_run"])
        self.assertFalse(config["crawler"]["maintenance_verification_enabled"])
        self.assertFalse(config["crawler"]["image_archive_enabled"])

    def test_manual_profile_can_keep_full_maintenance(self):
        config, target, configured_max = runner._apply_manual_archive_profile(
            self.base_config(),
            750,
            full_maintenance=True,
        )

        self.assertEqual(target, 750)
        self.assertEqual(configured_max, 1000)
        self.assertTrue(config["crawler"]["maintenance_verification_enabled"])
        self.assertTrue(config["crawler"]["image_archive_enabled"])

    def test_manual_profile_fifty_pages_stays_fifty(self):
        config, target, _ = runner._apply_manual_archive_profile(
            self.base_config(),
            50,
        )
        self.assertEqual(target, 50)
        self.assertEqual(config["crawler"]["explore_pages_per_run"], 50)

    def test_large_sweeps_increase_only_manual_time_and_pacing(self):
        base = self.base_config()
        base["crawler"]["explore_pages_max"] = 2500
        base["crawler"]["explore_time_limit_seconds"] = 4200
        smaller, _, _ = runner._apply_manual_archive_profile(base, 750)
        thousand, _, _ = runner._apply_manual_archive_profile(base, 1000)
        middle, middle_target, _ = runner._apply_manual_archive_profile(base, 1500)
        large, large_target, cap = runner._apply_manual_archive_profile(base, 2500)
        self.assertEqual((middle_target, large_target, cap), (1500, 2500, 2500))
        self.assertEqual(smaller["crawler"]["explore_time_limit_seconds"], 4200)
        self.assertEqual(thousand["crawler"]["explore_time_limit_seconds"], 5400)
        self.assertEqual(middle["crawler"]["explore_time_limit_seconds"], 7200)
        self.assertEqual(large["crawler"]["explore_time_limit_seconds"], 12600)
        self.assertEqual(middle["crawler"]["manual_request_delay_seconds"], 0.15)
        self.assertEqual(large["crawler"]["manual_request_delay_seconds"], 0.30)
        self.assertNotIn("manual_request_delay_seconds", thousand["crawler"])
        self.assertNotIn("manual_request_delay_seconds", smaller["crawler"])
        self.assertEqual(base["crawler"]["explore_time_limit_seconds"], 4200)


    def test_long_2500_timer_is_manual_only_and_preserves_maintenance_choice(self):
        config = self.base_config()
        config["crawler"]["explore_pages_max"] = 2500
        manual, _, _ = runner._apply_manual_archive_profile(config, 2500)
        manual_with_maintenance, _, _ = runner._apply_manual_archive_profile(
            config, 2500, full_maintenance=True,
        )
        self.assertEqual(manual["crawler"]["explore_time_limit_seconds"], 12600)
        self.assertEqual(manual_with_maintenance["crawler"]["explore_time_limit_seconds"], 12600)
        self.assertFalse(manual["crawler"]["maintenance_verification_enabled"])
        self.assertTrue(manual_with_maintenance["crawler"]["maintenance_verification_enabled"])
        self.assertNotIn("explore_time_limit_seconds", config["crawler"])

    def test_large_manual_guard_targets(self):
        for selected in (1500, 2500):
            with self.subTest(selected=selected):
                with patch.dict(os.environ, {"SPICYCHAT_ARCHIVE_MANUAL_PAGES": str(selected)}):
                    self.assertEqual(guard._manual_pages(500, 2500), selected)

    def test_manual_profile_never_exceeds_configured_max(self):
        config, target, _ = runner._apply_manual_archive_profile(
            self.base_config(),
            5000,
        )

        self.assertEqual(target, 1000)
        self.assertEqual(config["crawler"]["explore_pages_per_run"], 1000)


class ScheduledArchiveProfileTests(unittest.TestCase):
    def test_latest_only_keeps_verification_and_images(self):
        original = {
            "crawler": {
                "explore_pages_per_run": 500,
                "explore_pages_max": 1000,
                "maintenance_verification_budget": 5000,
                "image_archive_enabled": True,
            },
            "listings": {
                "latest": "createdAt:desc",
                "trending": "num_messages_24h:desc",
                "popular": "num_messages:desc",
            },
        }
        scheduled = runner._apply_scheduled_archive_profile(original)
        self.assertTrue(scheduled["crawler"]["scheduled_latest_only"])
        self.assertEqual(scheduled["listings"], {"latest": "createdAt:desc"})
        self.assertEqual(scheduled["crawler"]["maintenance_verification_budget"], 5000)
        self.assertTrue(scheduled["crawler"]["image_archive_enabled"])
        self.assertEqual(scheduled["crawler"]["explore_pages_per_run"], 500)
        self.assertEqual(original["listings"].keys(), {"latest", "trending", "popular"})
        self.assertNotIn("scheduled_latest_only", original["crawler"])
        self.assertNotIn("manual_discovery_run", scheduled["crawler"])
        self.assertNotIn("manual_request_delay_seconds", scheduled["crawler"])
        self.assertNotIn("explore_time_limit_seconds", scheduled["crawler"])

    def test_requires_latest_listing_to_avoid_silent_data_loss(self):
        with self.assertRaisesRegex(ValueError, "latest"):
            runner._apply_scheduled_archive_profile({"crawler": {}, "listings": {"popular": "rating_score:desc"}})


class ManualGuardPageTests(unittest.TestCase):
    def test_manual_guard_respects_small_and_large_targets(self):
        for selected in (50, 100, 250, 500, 750, 1000):
            with self.subTest(selected=selected):
                with patch.dict(os.environ, {"SPICYCHAT_ARCHIVE_MANUAL_PAGES": str(selected)}):
                    self.assertEqual(guard._manual_pages(500, 1000), selected)

    def test_manual_guard_caps_only_at_configured_max(self):
        with patch.dict(os.environ, {"SPICYCHAT_ARCHIVE_MANUAL_PAGES": "1200"}):
            self.assertEqual(guard._manual_pages(500, 1000), 1000)


if __name__ == "__main__":
    unittest.main()
