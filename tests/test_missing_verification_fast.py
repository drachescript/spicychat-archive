"""Fast missing verification is rate-limited, fair, and conservative."""
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import archive
import missing_verification_fast as fast


def suspect(status=None, count=0, at=None):
    return {"lastStatus": status, "count": count, "lastAt": at}


class MockClient:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.calls = []
        self.lock = threading.Lock()

    def character(self, bot_id):
        with self.lock:
            self.calls.append(bot_id)
        return self.response_factory(bot_id)


class FastVerificationTests(unittest.TestCase):
    def test_prioritizes_first_404_without_starving_new_or_transient(self):
        checks = {
            "first-a": suspect(404, 1, "previous-run"),
            "first-b": suspect(404, 1, "previous-run"),
            "first-c": suspect(404, 1, "previous-run"),
            "new-a": suspect(),
            "new-b": suspect(),
            "new-c": suspect(),
            "error-a": suspect(503, 0, "previous-run"),
            "error-b": suspect(0, 0, "previous-run"),
        }
        selected = fast.ordered_suspects(checks, "current-run", 5)
        self.assertEqual(len(selected), 5)
        self.assertEqual(len(set(selected)), 5)
        self.assertIn("first-a", selected)
        self.assertIn("new-a", selected)
        self.assertIn("error-a", selected)
        self.assertEqual(selected[0], "first-a")

    def test_independent_run_rule_remains_unchanged(self):
        checks = {
            "same-run-first": suspect(404, 1, "now"),
            "old-first": suspect(404, 1, "yesterday"),
        }
        self.assertFalse(archive.missing_check_due(checks["same-run-first"], "now"))
        self.assertEqual(fast.ordered_suspects(checks, "now", 10), ["old-first"])
        self.assertEqual(len(fast.ordered_suspects(checks, "tomorrow", 10)), 2)

    def test_work_budget_is_a_hard_ceiling(self):
        checks = {str(i): suspect() for i in range(100)}
        self.assertEqual(len(fast.ordered_suspects(checks, "run", 10)), 10)
        self.assertEqual(fast.ordered_suspects(checks, "run", 0), [])

    def test_parallel_fetches_each_selected_bot_at_most_once(self):
        active = 0
        maximum = 0
        lock = threading.Lock()

        def character(_id):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return archive.HTTPResult(True, 200, data={"character_id": _id})

        client = MockClient(character)
        ids = [f"bot-{n}" for n in range(12)]
        result = list(fast.verified_responses(
            ids, client, {}, workers=4, rate_per_sec=16, sleep=lambda _: None,
        ))
        self.assertEqual([item[0] for item in result], ids)
        self.assertEqual(len(client.calls), len(set(client.calls)))
        self.assertEqual(len(client.calls), 12)
        self.assertGreater(maximum, 1)

    def test_api_rate_limit_and_5xx_trigger_fail_closed_circuit_breaker(self):
        client = MockClient(lambda _: archive.HTTPResult(False, 429))
        ids = [f"bot-{n}" for n in range(100)]
        pauses = []
        results = list(fast.verified_responses(
            ids, client, {}, workers=4, rate_per_sec=16, sleep=pauses.append,
        ))
        self.assertEqual(len(results), 12)
        self.assertEqual(len(client.calls), 12)
        self.assertEqual([p for p in pauses if p >= 1], [1, 2, 4])
        self.assertTrue(all(response.status == 429 for _, response in results))

    def test_isolated_error_does_not_stop_the_pass(self):
        def one_error(bot_id):
            if bot_id == "bot-0":
                return archive.HTTPResult(False, 503)
            return archive.HTTPResult(True, 200, data={"character_id": bot_id})

        client = MockClient(one_error)
        ids = [f"bot-{n}" for n in range(24)]
        got = list(fast.verified_responses(
            ids, client, {}, workers=8, rate_per_sec=16, sleep=lambda _: None,
        ))
        self.assertEqual(len(got), len(ids))
        self.assertEqual([item[0] for item in got], ids)

    def test_network_exception_remains_transient(self):
        def fail(_):
            raise OSError("network failed")

        client = MockClient(fail)
        got = list(fast.verified_responses(
            ["bot-1"], client, {}, workers=1, rate_per_sec=8,
            sleep=lambda _: None,
        ))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1].status, 0)

    def test_cloud_verifier_retains_404_confirmation_and_checkpoint(self):
        source = (SCRIPTS / "archive_cloud.py").read_text(encoding="utf-8")
        self.assertIn("fast_missing.ordered_suspects", source)
        self.assertIn("fast_missing.verified_responses", source)
        self.assertIn("store.save_state(state)", source)
        self.assertIn("info[\"count\"] >= required", source)
        self.assertIn("if response.status == 404:", source)
        self.assertIn("if response.ok:", source)
        self.assertNotIn("response = client.character(bot_id)\n            verified", source)


if __name__ == "__main__":
    unittest.main()
