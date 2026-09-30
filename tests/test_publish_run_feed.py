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

    def test_time_limit_is_partial(self):
        exploration = {
            "pageBudget": 1000,
            "pagesCompleted": 700,
            "errors": [],
            "timeLimited": True,
        }
        self.assertEqual(mod.classify_run_status("success", exploration), "partial")
        self.assertIn("time limit", mod.stop_reason(exploration).lower())

    def test_hard_workflow_failure_stays_failure(self):
        exploration = {
            "errors": ["warning from previous summary"],
            "timeLimited": False,
        }
        self.assertEqual(mod.classify_run_status("failure", exploration), "failure")


if __name__ == "__main__":
    unittest.main()
