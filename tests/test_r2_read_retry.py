"""A stalled R2 response body should not abort the whole Archive scan."""
import gzip
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError, ReadTimeoutError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from storage_r2 import R2ArchiveStore


def store_with_reads(*responses):
    store = object.__new__(R2ArchiveStore)
    store.bucket = "test-bucket"
    store.s3 = Mock()
    store.s3.get_object.side_effect = list(responses)
    return store


def streaming_response(value, *, gzipped=False, failure=None):
    stream = Mock()
    if failure is not None:
        stream.read.side_effect = failure
    else:
        stream.read.return_value = gzip.compress(value) if gzipped else value
    return {"Body": stream, "ContentEncoding": "gzip" if gzipped else ""}


def timeout():
    return ReadTimeoutError(endpoint_url="https://example.invalid", error="timed out")


class R2ReadRetryTests(unittest.TestCase):
    @patch("storage_r2.time.sleep")
    def test_retries_failed_response_body_without_accepting_partial_data(self, sleep):
        broken = streaming_response(b'{"partial":', failure=timeout())
        complete = streaming_response(b'{"complete":true}', gzipped=True)
        store = store_with_reads(broken, complete)
        self.assertEqual(store.get_json("indexes/creator/ab.json"), {"complete": True})
        self.assertEqual(store.s3.get_object.call_count, 2)
        broken["Body"].close.assert_called_once()
        complete["Body"].close.assert_called_once()
        sleep.assert_called_once()

    @patch("storage_r2.time.sleep")
    def test_repeated_timeouts_fail_closed_after_three_attempts(self, sleep):
        attempts = [streaming_response(b"", failure=timeout()) for _ in range(3)]
        store = store_with_reads(*attempts)
        with self.assertRaises(ReadTimeoutError):
            store.get_json("indexes/creator/ab.json", {"wrong": "default"})
        self.assertEqual(store.s3.get_object.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        for attempt in attempts:
            attempt["Body"].close.assert_called_once()

    @patch("storage_r2.time.sleep")
    def test_get_object_timeouts_are_also_retried(self, sleep):
        store = store_with_reads(timeout(), streaming_response(b"okay"))
        self.assertEqual(store.get_bytes("indexes/test"), b"okay")
        self.assertEqual(store.s3.get_object.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    @patch("storage_r2.time.sleep")
    def test_missing_object_is_not_retried(self, sleep):
        missing = ClientError(
            {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "GetObject",
        )
        store = store_with_reads(missing)
        self.assertEqual(store.get_bytes("missing", b"fallback"), b"fallback")
        self.assertEqual(store.s3.get_object.call_count, 1)
        sleep.assert_not_called()

    @patch("storage_r2.time.sleep")
    def test_invalid_gzip_is_not_silently_replaced(self, sleep):
        bad = streaming_response(b"not gzip")
        bad["ContentEncoding"] = "gzip"
        store = store_with_reads(bad)
        with self.assertRaises(gzip.BadGzipFile):
            store.get_bytes("bad", b"fallback")
        self.assertEqual(store.s3.get_object.call_count, 1)
        bad["Body"].close.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
