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

    def test_natural_end_of_701_of_1000_pages_is_complete(self):
        exploration = {
            "pageBudget": 1000, "pagesCompleted": 701, "naturalEnd": True,
            "timeLimited": False, "errors": [], "partial": False,
            "new": 61,
        }
        self.assertEqual(mod.classify_run_status("success", exploration), "success")
        self.assertEqual(mod.discovery_completion(exploration), "results-exhausted")
        self.assertTrue(mod.exploration_is_successful(exploration))
        self.assertIsNone(mod.stop_reason(exploration))

    def test_scheduled_maintenance_is_success_without_deep_search(self):
        exploration = {"pageBudget": 0, "pagesCompleted": 0, "naturalEnd": False,
                       "timeLimited": False, "errors": []}
        self.assertEqual(mod.classify_run_status("success", exploration), "success")
        self.assertEqual(mod.discovery_completion(exploration), "not-run")

    def test_early_stop_without_natural_end_is_partial(self):
        exploration = {
            "pageBudget": 1000, "pagesCompleted": 701, "naturalEnd": False,
            "errors": [], "timeLimited": False,
        }
        self.assertEqual(mod.classify_run_status("success", exploration), "partial")
        self.assertEqual(mod.discovery_completion(exploration, "partial"), "partial")
        self.assertFalse(mod.exploration_is_successful(exploration))

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

    def test_cursor_switch_does_not_falsely_complete_early_sweep(self):
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
        self.assertEqual(mod.classify_run_status("success", exploration), "partial")
        self.assertIn("verified end", mod.stop_reason(exploration))
        self.assertFalse(mod.exploration_is_successful(exploration))

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

    def test_last_discovery_persists_across_zero_addition_maintenance(self):
        manual = {
            "kind": "archive-run", "at": "2026-11-07T00:49:52Z",
            "addedSincePrevious": 342, "runDurationSeconds": 1377,
            "exploration": {
                "pageBudget": 1000, "pagesCompleted": 701, "new": 61,
                "naturalEnd": True, "errors": [], "partial": False,
            },
        }
        maintenance = {
            "kind": "archive-run", "at": "2026-11-07T06:57:12Z",
            "addedSincePrevious": 0,
            "exploration": {"pageBudget": 0, "pagesCompleted": 0, "new": 0},
        }
        last = mod.last_discovery_run([manual, maintenance])
        self.assertEqual(last["finishedAt"], manual["at"])
        self.assertEqual(last["addedBots"], 342)
        self.assertEqual(last["newFromDiscovery"], 61)
        self.assertEqual(last["pagesCompleted"], 701)
        self.assertEqual(last["completion"], "results-exhausted")
        self.assertEqual(last["status"], "success")
        self.assertIsNone(mod.last_discovery_run([maintenance]))

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

    def test_newest_scan_is_distinct_from_deep_discovery(self):
        listings = {"latest": {"count": 3500, "new": 563}}
        latest = listings["latest"]
        self.assertEqual(latest["count"], 3500)
        self.assertEqual(latest["new"], 563)
        self.assertEqual(
            sum(int((value or {}).get("new") or 0) for value in listings.values()), 563
        )

    def test_hard_workflow_failure_stays_failure(self):
        exploration = {
            "errors": ["warning from previous summary"],
            "timeLimited": False,
        }
        self.assertEqual(mod.classify_run_status("failure", exploration), "failure")


if __name__ == "__main__":
    unittest.main()
