import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import archive as legacy
import r2_runtime_accel as accel
import typesense_retry as retry


class AdaptiveTypesenseBatchTests(unittest.TestCase):
    def request(self, page=25):
        return {"q": "*", "page": page, "per_page": 250, "sort_by": "createdAt:desc"}

    @staticmethod
    def ok_for(searches):
        return legacy.HTTPResult(
            True, 200,
            data={"results": [
                {"hits": [{"document": {"page": row["page"]}}], "found": 10000}
                for row in searches
            ]},
        )

    def test_transient_errors_shrink_prefetch_on_next_retry_not_cursor(self):
        client = SimpleNamespace()
        sizes = []
        pages = []

        def fake_fetch(_client, searches):
            sizes.append(len(searches))
            pages.append(searches[0]["page"])
            if len(searches) > 1:
                return legacy.HTTPResult(False, 0, error="read timed out")
            return self.ok_for(searches)

        with patch.object(accel, "_TYPESENSE_BATCH", 4), \
             patch.object(accel, "_ORIGINAL_MULTI_SEARCH", side_effect=fake_fetch), \
             patch.object(retry.time, "sleep"):
            result = retry.multi_search_with_retry(
                accel._batched_multi_search, client,
                [self.request(113)], delays=(0, 0, 0, 0),
            )
            self.assertTrue(result.ok)
            self.assertEqual(sizes, [4, 2, 1])
            self.assertEqual(pages, [113, 113, 113])
            self.assertEqual(result.data["results"][0]["hits"][0]["document"]["page"], 113)
            self.assertEqual(client._runtime_accel_typesense_batch, 1)

    def test_successful_prefetch_caches_following_pages(self):
        client = SimpleNamespace()
        calls = []

        def fake_fetch(_client, searches):
            calls.append([row["page"] for row in searches])
            return self.ok_for(searches)

        with patch.object(accel, "_TYPESENSE_BATCH", 4), \
             patch.object(accel, "_ORIGINAL_MULTI_SEARCH", side_effect=fake_fetch):
            for page in range(25, 29):
                result = accel._batched_multi_search(client, [self.request(page)])
                self.assertTrue(result.ok)
                self.assertEqual(result.data["results"][0]["hits"][0]["document"]["page"], page)
        self.assertEqual(calls, [[25, 26, 27, 28]])

    def test_nontransient_http_error_does_not_reduce_batch(self):
        client = SimpleNamespace()
        with patch.object(accel, "_TYPESENSE_BATCH", 4), \
             patch.object(accel, "_ORIGINAL_MULTI_SEARCH", return_value=legacy.HTTPResult(False, 400, error="bad request")) as fetch:
            result = accel._batched_multi_search(client, [self.request()])
            self.assertFalse(result.ok)
            self.assertEqual(len(fetch.call_args.args[1]), 4)
            self.assertFalse(hasattr(client, "_runtime_accel_typesense_batch"))

    def test_cursor_search_never_prefetches_future_cursor_pages(self):
        client = SimpleNamespace()
        request = self.request()
        request["filter_by"] = "applicationId:spicychat && createdAt:<1730000000"
        with patch.object(accel, "_TYPESENSE_BATCH", 4), \
             patch.object(accel, "_ORIGINAL_MULTI_SEARCH", side_effect=lambda c, s: self.ok_for(s)) as fetch:
            result = accel._batched_multi_search(client, [request])
            self.assertTrue(result.ok)
            self.assertEqual(len(fetch.call_args.args[1]), 1)


if __name__ == "__main__":
    unittest.main()
