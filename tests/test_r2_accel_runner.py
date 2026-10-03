import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import r2_accel_runner as runner


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

    def test_manual_profile_never_exceeds_configured_max(self):
        config, target, _ = runner._apply_manual_archive_profile(
            self.base_config(),
            5000,
        )

        self.assertEqual(target, 1000)
        self.assertEqual(config["crawler"]["explore_pages_per_run"], 1000)


if __name__ == "__main__":
    unittest.main()
