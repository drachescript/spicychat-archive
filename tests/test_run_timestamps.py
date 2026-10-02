import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import r2_archive_batched as batched


class FakeStore:
    meta_prefix = "meta"

    def __init__(self, history):
        self.history = history
        self.saved_state = None
        self.flushed = False

    def key(self, *parts):
        return "/".join(parts)

    def get_json(self, key, default):
        return self.history

    def put_json(self, key, value):
        self.history = value

    def save_state(self, state):
        self.saved_state = dict(state)

    def flush_usage(self, force=False):
        self.flushed = bool(force)


class RunTimestampTests(unittest.TestCase):
    def test_cursor_cap_switch_is_not_mislabeled_as_natural_end(self):
        state = {
            "lastRunAt": "2026-10-02T01:40:00Z",
            "lastRunSummary": {
                "exploration": {
                    "pageBudget": 100,
                    "pagesCompleted": 99,
                    "timeLimited": False,
                    "errors": [],
                    "mode": "cursor",
                    "naturalEnd": False,
                    "resultCapReached": True,
                    "switchedToCursor": True,
                }
            },
        }

        with patch.object(batched.optimized, "_active_state", state):
            outcome = batched._exploration_outcome()

        self.assertIsNotNone(outcome)
        self.assertFalse(outcome["partial"])
        self.assertFalse(outcome["naturalEnd"])
        self.assertTrue(outcome["resultCapReached"])
        self.assertTrue(outcome["switchedToCursor"])

    def test_successful_run_is_restamped_at_completion_everywhere(self):
        started = "2026-10-01T20:58:36Z"
        finished = "2026-10-01T21:57:10Z"
        state = {"lastRunAt": started}
        history = {
            "schemaVersion": 1,
            "runs": [
                {"at": started, "kind": "archive-run", "totalBots": 123},
            ],
        }
        store = FakeStore(history)

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            stats_path = data_dir / "stats.json"
            stats_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "generatedAt": started,
                        "runs": [
                            {
                                "at": started,
                                "kind": "archive-run",
                                "totalBots": 123,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(batched.optimized, "_active_state", state),
                patch.object(batched.optimized, "_active_store", store),
                patch.object(batched.optimized.legacy, "SITE_DATA_DIR", data_dir),
                patch.object(batched.optimized.legacy, "utc_now", return_value=finished),
            ):
                batched._stamp_run_finished()

            saved_stats = json.loads(stats_path.read_text(encoding="utf-8"))

        self.assertEqual(state["lastRunAt"], finished)
        self.assertEqual(saved_stats["generatedAt"], finished)
        self.assertEqual(saved_stats["runs"][-1]["at"], finished)
        self.assertEqual(store.history["runs"][-1]["at"], finished)
        self.assertEqual(store.saved_state["lastRunAt"], finished)
        self.assertTrue(store.flushed)


if __name__ == "__main__":
    unittest.main()
