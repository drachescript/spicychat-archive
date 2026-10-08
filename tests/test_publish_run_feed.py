import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "sca_publish_run_feed",
    ROOT / "scripts" / "publish_run_feed.py",
)
mod = importlib.util.module_from_spec(spec)
sys.modules["sca_publish_run_feed"] = mod
spec.loader.exec_module(mod)


class PublishRunFeedTests(unittest.TestCase):
    def test_success_when_discovery_completed_without_warning(self):
        exploration = {
            "pageBudget": 1000,
            "pagesCompleted": 1000,
            "errors": [],
            "timeLimited": False,
        }
        self.assertEqual(mod.classify_run_status("success", exploration), "success")
        self.assertIsNone(mod.stop_reason(exploration))
        self.assertTrue(mod.exploration_is_successful(exploration))

    def test_typesense_error_is_partial_not_success(self):
        exploration = {
            "pageBudget": 1000,
            "pagesCompleted": 168,
            "errors": ["Typesense timed out after retries"],
            "timeLimited": False,
        }
        self.assertEqual(mod.classify_run_status("success", exploration), "partial")
        self.assertEqual(
            mod.stop_reason(exploration),
            "Typesense timed out after retries",
        )
        self.assertFalse(mod.exploration_is_successful(exploration))

    def test_short_clean_pass_is_success_when_natural_end_is_known(self):
        exploration = {
            "pageBudget": 1000,
            "pagesCompleted": 168,
            "errors": [],
            "timeLimited": False,
            "partial": False,
            "naturalEnd": True,
        }
        self.assertEqual(mod.classify_run_status("success", exploration), "success")
        self.assertIsNone(mod.stop_reason(exploration))
        self.assertTrue(mod.exploration_is_successful(exploration))

    def test_clean_result_cap_switch_is_successful_without_natural_end(self):
        exploration = {
            "pageBudget": 100,
            "pagesCompleted": 99,
            "errors": [],
            "timeLimited": False,
            "partial": False,
            "naturalEnd": False,
            "resultCapReached": True,
            "switchedToCursor": True,
        }
        self.assertEqual(mod.classify_run_status("success", exploration), "success")
        self.assertIsNone(mod.stop_reason(exploration))
        self.assertTrue(mod.exploration_is_successful(exploration))

    def test_legacy_incomplete_run_is_not_assumed_successful(self):
        exploration = {
            "pageBudget": 1000,
            "pagesCompleted": 168,
            "errors": [],
            "timeLimited": False,
        }
        self.assertFalse(mod.exploration_is_successful(exploration))

    def test_time_limit_is_partial(self):
        exploration = {
            "pageBudget": 1000,
            "pagesCompleted": 960,
            "errors": [],
            "timeLimited": True,
        }
        self.assertEqual(mod.classify_run_status("success", exploration), "partial")
        self.assertIn("time limit", mod.stop_reason(exploration).lower())
        self.assertFalse(mod.exploration_is_successful(exploration))

    def test_current_outcome_restores_exact_error_and_natural_end(self):
        latest = {"at": "2026-09-29T23:38:50Z"}
        exploration = {
            "pageBudget": 1000,
            "pagesCompleted": 168,
            "timeLimited": False,
        }
        outcome = {
            "at": "2026-09-29T23:38:50Z",
            "pageBudget": 1000,
            "pagesCompleted": 168,
            "timeLimited": False,
            "errors": ["read timeout after retries"],
            "partial": True,
            "naturalEnd": False,
            "resultCapReached": True,
            "switchedToCursor": True,
        }
        merged = mod.apply_current_outcome(latest, exploration, outcome)
        self.assertEqual(merged["errors"], ["read timeout after retries"])
        self.assertTrue(merged["partial"])
        self.assertFalse(merged["naturalEnd"])
        self.assertTrue(merged["resultCapReached"])
        self.assertTrue(merged["switchedToCursor"])

    def test_stale_outcome_is_ignored(self):
        latest = {"at": "new-run"}
        exploration = {"pageBudget": 500, "pagesCompleted": 500}
        outcome = {
            "at": "old-run",
            "pageBudget": 1000,
            "pagesCompleted": 168,
            "errors": ["old timeout"],
            "partial": True,
        }
        merged = mod.apply_current_outcome(latest, exploration, outcome)
        self.assertEqual(merged, exploration)

    def test_last_successful_run_skips_ambiguous_incomplete_history(self):
        runs = [
            {
                "at": "750-run",
                "kind": "archive-run",
                "runDurationSeconds": 3275,
                "addedSincePrevious": 186288,
                "exploration": {
                    "pageBudget": 750,
                    "pagesCompleted": 750,
                    "timeLimited": False,
                },
            },
            {
                "at": "168-run",
                "kind": "archive-run",
                "runDurationSeconds": 1241,
                "addedSincePrevious": 41652,
                "exploration": {
                    "pageBudget": 1000,
                    "pagesCompleted": 168,
                    "timeLimited": False,
                },
            },
            {
                "at": "960-run",
                "kind": "archive-run",
                "runDurationSeconds": 3946,
                "addedSincePrevious": 238747,
                "exploration": {
                    "pageBudget": 1000,
                    "pagesCompleted": 960,
                    "timeLimited": True,
                },
            },
        ]
        result = mod.last_successful_run(runs, "partial")
        self.assertEqual(result["finishedAt"], "750-run")
        self.assertEqual(result["pagesCompleted"], 750)


    def test_listing_and_deep_discovery_counts_can_reconcile_batch_total(self):
        listings = {
            "latest": {"new": 1334},
            "trending": {"new": 0},
            "top-rated": {"new": 0},
        }
        listing_new = sum(
            int((info or {}).get("new") or 0)
            for info in listings.values()
            if isinstance(info, dict)
        )
        added = 1334
        deep_new = 0
        other = max(0, added - listing_new - deep_new)
        self.assertEqual(listing_new, 1334)
        self.assertEqual(other, 0)

    def test_hard_workflow_failure_stays_failure(self):
        exploration = {
            "errors": ["warning from previous summary"],
            "timeLimited": False,
        }
        self.assertEqual(mod.classify_run_status("failure", exploration), "failure")


if __name__ == "__main__":
    unittest.main()
