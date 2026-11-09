import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import archive_cloud


class PublicCountCarryForwardTests(unittest.TestCase):
    def test_skips_unmeasured_final_page_count_from_cursor(self):
        stats = {"runs": [
            {"kind": "archive-run", "at": "2026-11-09T05:39:28Z",
             "publicIndexBots": 1434392,
             "listings": {"latest": {"found": 1434392}}},
            {"kind": "archive-run", "at": "2026-11-09T08:03:31Z",
             "publicIndexBots": 145, "listings": {"latest": {"found": 0}}},
        ]}
        self.assertEqual(archive_cloud.observed_public_count(stats, {"publicIndexBots": 145}),
                         (1434392, "2026-11-09T05:39:28Z"))

    def test_uses_previous_manifest_as_fallback_if_no_history(self):
        count, when = archive_cloud.observed_public_count(
            {"runs": []}, {"publicIndexBots": 12345, "lastScan": "2026-11-09T00:00:00Z"}
        )
        self.assertEqual(count, 12345)
        self.assertEqual(when, "2026-11-09T00:00:00Z")

    def test_newer_valid_measurement_wins(self):
        stats = {"runs": [
            {"kind": "archive-run", "at": "2026-11-09T05:00:00Z",
             "listings": {"latest": {"found": 1000}}},
            {"kind": "archive-run", "at": "2026-11-09T07:00:00Z",
             "listings": {"latest": {"found": 980}}},
        ]}
        self.assertEqual(archive_cloud.observed_public_count(stats, {}),
                         (980, "2026-11-09T07:00:00Z"))


if __name__ == "__main__":
    unittest.main()
