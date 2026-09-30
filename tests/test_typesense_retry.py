import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import archive as legacy
import typesense_retry as retry


class TypesenseRetryTests(unittest.TestCase):
    def test_transient_failure_retries_until_success(self):
        responses = [
            legacy.HTTPResult(False, 0, error="timeout"),
            legacy.HTTPResult(False, 503, error="busy"),
            legacy.HTTPResult(True, 200, data={"results": []}),
        ]
        calls = []

        def original(client, searches):
            calls.append(searches)
            return responses.pop(0)

        with patch.object(retry.time, "sleep") as sleep:
            result = retry.multi_search_with_retry(
                original,
                object(),
                [{"page": 169}],
                delays=(0.0, 2.0, 5.0, 10.0),
            )

        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 3)
        sleep.assert_called_once_with(2.0)

    def test_non_transient_error_does_not_retry(self):
        calls = 0

        def original(client, searches):
            nonlocal calls
            calls += 1
            return legacy.HTTPResult(False, 400, error="bad request")

        result = retry.multi_search_with_retry(
            original,
            object(),
            [{"page": 1}],
            delays=(0.0, 2.0),
        )

        self.assertFalse(result.ok)
        self.assertEqual(calls, 1)

    def test_exhausted_retries_return_last_error(self):
        calls = 0

        def original(client, searches):
            nonlocal calls
            calls += 1
            return legacy.HTTPResult(False, 0, error=f"timeout-{calls}")

        with patch.object(retry.time, "sleep"):
            result = retry.multi_search_with_retry(
                original,
                object(),
                [{"page": 169}],
                delays=(0.0, 0.0, 0.0, 0.0),
            )

        self.assertFalse(result.ok)
        self.assertEqual(calls, 5)
        self.assertEqual(result.error, "timeout-5")


if __name__ == "__main__":
    unittest.main()
