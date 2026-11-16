import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import archive_sweep_watchdog as watchdog

UTC = timezone.utc
MON = datetime(2026, 11, 16, 13, 47, tzinfo=UTC)
SLOT = datetime(2026, 11, 16, 2, 17, tzinfo=UTC)


def row(at, pages=2500, done=2500):
    return {
        "kind": "archive-run",
        "at": at.isoformat().replace("+00:00", "Z"),
        "exploration": {"pageBudget": pages, "pagesCompleted": done},
    }


def safe_usage(now=MON):
    return {
        "checkedAt": now.isoformat().replace("+00:00", "Z"),
        "selectedMode": "r2",
        "r2ReadAllowed": True,
        "usage": {"classA": 350000, "classB": 900000, "storageBytes": 5500000000},
        "limits": {"classAPause": 950000, "classBPause": 6000000,
                   "storageResumeBytes": 9200000000},
    }


class MissedSweepWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.stats = {"runs": [row(SLOT - timedelta(hours=30))]}
        self.usage = safe_usage()
        self.now = MON

    def decide(self, *, stats=None, usage=None, runs=None, now=None):
        return watchdog.decide(stats if stats is not None else self.stats,
                               usage if usage is not None else self.usage,
                               runs if runs is not None else [],
                               now if now is not None else self.now)

    def test_due_slot_is_previous_monday_wednesday_or_friday(self):
        self.assertEqual(watchdog.latest_slot(MON), SLOT)
        self.assertEqual(watchdog.latest_slot(MON + timedelta(days=1)), SLOT)
        wed = datetime(2026, 11, 18, 2, 18, tzinfo=UTC)
        self.assertEqual(watchdog.latest_slot(wed),
                         datetime(2026, 11, 18, 2, 17, tzinfo=UTC))

    def test_missed_slot_launches_one_recovery_when_safe(self):
        allowed, reason = self.decide()
        self.assertTrue(allowed, reason)
        self.assertIn("missing", reason)

    def test_too_early_does_not_double_launch_scheduled_sweep(self):
        self.assertFalse(self.decide(now=SLOT + timedelta(hours=3))[0])

    def test_successful_deep_sweep_after_due_suppresses_recovery(self):
        stats = {"runs": [*self.stats["runs"], row(SLOT + timedelta(hours=2))]}
        self.assertFalse(self.decide(stats=stats)[0])

    def test_partial_deep_sweep_attempt_does_not_start_another(self):
        stats = {"runs": [*self.stats["runs"], row(SLOT + timedelta(hours=2),
                                                   pages=2500, done=23)]}
        self.assertFalse(self.decide(stats=stats)[0])

    def test_maintenance_only_after_slot_does_not_hide_missed_sweep(self):
        stats = {"runs": [*self.stats["runs"], row(SLOT + timedelta(hours=2),
                                                   pages=0, done=0)]}
        self.assertTrue(self.decide(stats=stats)[0])

    def test_cooldown_is_enforced(self):
        stats = {"runs": [row(SLOT - timedelta(hours=5))]}
        self.assertFalse(self.decide(stats=stats)[0])

    def test_queued_or_running_writer_is_not_duplicated(self):
        for status in ("queued", "pending", "in_progress"):
            with self.subTest(status=status):
                runs = [{"created_at": SLOT.isoformat(), "status": status,
                         "display_title": "Update SpicyChat archive"}]
                self.assertFalse(self.decide(runs=runs)[0])

    def test_recovery_attempt_is_not_automatically_dispatched_twice(self):
        run = {"created_at": SLOT.isoformat(), "status": "completed",
               "conclusion": "failure", "display_title":
               "Missed deep sweep recovery · 2,500 pages"}
        allowed, why = self.decide(runs=[run])
        self.assertFalse(allowed)
        self.assertIn("already attempted", why)

    def test_unsafe_and_stale_quotas_fail_closed(self):
        values = [
            {**safe_usage(), "selectedMode": "github"},
            {**safe_usage(), "checkedAt": (MON - timedelta(days=2)).isoformat()},
            {**safe_usage(), "usage": {**safe_usage()["usage"], "classA": 920000}},
            {**safe_usage(), "usage": {**safe_usage()["usage"], "classB": 6100000}},
            {**safe_usage(), "usage": {**safe_usage()["usage"], "storageBytes": 9250000000}},
            {},
        ]
        for index, value in enumerate(values):
            with self.subTest(index=index):
                self.assertFalse(self.decide(usage=value)[0])

    def test_config_preserves_existing_maintenance_and_uses_distinct_recovery_mode(self):
        workflow = (SCRIPTS.parent / ".github" / "workflows" / "archive.yml").read_text()
        watchdog_yml = (SCRIPTS.parent / ".github" / "workflows" /
                        "archive-sweep-watchdog.yml").read_text()
        self.assertIn("run_mode == 'recover-catalog'", workflow)
        self.assertIn('          - "recover-catalog"', workflow)
        self.assertIn("47 13 * * 1,2,3,4,5,6", watchdog_yml)
        self.assertIn('workflows: ["Update SpicyChat archive"]', watchdog_yml)
        self.assertIn("actions: write", watchdog_yml)
        self.assertIn("SPICYCHAT_ARCHIVE_VERIFY_MISSING", workflow)


if __name__ == "__main__":
    unittest.main()
