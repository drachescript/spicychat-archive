"""Regression tests for transient same-object Cloudflare R2 PUT throttling."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from storage_r2 import R2ArchiveStore
from r2_runtime_accel import _async_put_bytes, _retryable_r2_put_error, flush_bot_writes, close_store


def r2_error(code, status, message="R2 request failed"):
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "PutObject",
    )


def fake_store(*put_results):
    store = object.__new__(R2ArchiveStore)
    store.bucket = "test"
    store.s3 = Mock()
    store.s3.put_object.side_effect = list(put_results)
    store._reserve_usage = Mock(return_value=(100, 0, True))
    store._rollback_usage = Mock()
    store.flush_usage = Mock()
    return store


class R2PutThrottleTests(unittest.TestCase):
    @patch("r2_runtime_accel.time.sleep")
    def test_same_object_service_unavailable_retries_and_keeps_usage_once(self, sleep):
        err = r2_error("ServiceUnavailable", 503, "Reduce your concurrent request rate for the same object.")
        store = fake_store(err, err, {"ETag": '"ok"'})
        try:
            _async_put_bytes(store, "bots/abc.json", b'{"id":"abc"}', category="bot", known_new=True)
            flush_bot_writes(store)
            self.assertEqual(store.s3.put_object.call_count, 3)
            self.assertEqual(store._reserve_usage.call_count, 1)
            store._rollback_usage.assert_not_called()
            self.assertEqual(sleep.call_count, 2)
            args = store.s3.put_object.call_args_list
            self.assertEqual(args[0].kwargs["Body"], args[1].kwargs["Body"])
            self.assertEqual(args[1].kwargs["Body"], args[2].kwargs["Body"])
        finally:
            close_store(store)

    @patch("r2_runtime_accel.time.sleep")
    def test_exhausted_throttle_rolls_back_only_once(self, sleep):
        err = r2_error("ServiceUnavailable", 503)
        store = fake_store(*([err] * 4))
        try:
            _async_put_bytes(store, "bots/abc.json", b"data", category="bot", known_new=True)
            with self.assertRaises(ClientError):
                flush_bot_writes(store)
            self.assertEqual(store.s3.put_object.call_count, 4)
            store._rollback_usage.assert_called_once_with(100, 0, True)
            self.assertEqual(sleep.call_count, 3)
        finally:
            close_store(store)

    @patch("r2_runtime_accel.time.sleep")
    def test_nontransient_error_fails_without_retry(self, sleep):
        err = r2_error("AccessDenied", 403)
        store = fake_store(err)
        try:
            _async_put_bytes(store, "bots/abc.json", b"data", category="bot", known_new=True)
            with self.assertRaises(ClientError):
                flush_bot_writes(store)
            self.assertEqual(store.s3.put_object.call_count, 1)
            store._rollback_usage.assert_called_once()
            sleep.assert_not_called()
        finally:
            close_store(store)

    def test_retry_classification_is_specific(self):
        self.assertTrue(_retryable_r2_put_error(r2_error("ServiceUnavailable", 503)))
        self.assertTrue(_retryable_r2_put_error(r2_error("SlowDown", 503)))
        self.assertTrue(_retryable_r2_put_error(r2_error("TooManyRequests", 429)))
        self.assertFalse(_retryable_r2_put_error(r2_error("AccessDenied", 403)))
        self.assertFalse(_retryable_r2_put_error(r2_error("NoSuchBucket", 404)))


if __name__ == "__main__":
    unittest.main()
