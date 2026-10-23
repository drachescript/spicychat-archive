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
        self.assertIn("inventory?.materializedIndexIds", home)
        self.assertNotIn("arithmetic public/archive", app)
        self.assertNotIn("is an arithmetic gap", home)


if __name__ == "__main__":
    unittest.main()
