import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_import_queues as check


class ImportOnlyQueueCheckTests(unittest.TestCase):
    def test_both_queues_empty(self):
        store = MagicMock()
        with patch.object(check, "list_inbox", return_value=[]), \
             patch.object(check, "_list_pending_meta", return_value=[]):
            self.assertEqual(check.check_queues(store), (False, False))

    def test_private_queue_has_work(self):
        store = MagicMock()
        with patch.object(check, "list_inbox", return_value=["bundle.json.gz"]), \
             patch.object(check, "_list_pending_meta", return_value=[]):
            self.assertEqual(check.check_queues(store), (True, False))

    def test_public_queue_has_work(self):
        store = MagicMock()
        with patch.object(check, "list_inbox", return_value=[]), \
             patch.object(check, "_list_pending_meta", return_value=[("id", {})]):
            self.assertEqual(check.check_queues(store), (False, True))

    def test_queue_request_error_must_not_look_empty(self):
        store = MagicMock()
        with patch.object(check, "list_inbox", side_effect=RuntimeError("R2 unavailable")):
            with self.assertRaisesRegex(RuntimeError, "R2 unavailable"):
                check.check_queues(store)


if __name__ == "__main__":
    unittest.main()
