import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import review_bot_status_submissions as review


class PublicSubmissionReviewTests(unittest.TestCase):
    def test_new_record_is_safe_but_unverified(self):
        row = review.classify_record(
            bot_id="abc",
            saved_at="2026-09-20T00:00:00Z",
            snapshot={"character_id": "abc", "name": "Bot"},
            observed_status="public",
            problems=[],
            existing=None,
        )
        self.assertEqual(row["classification"], "new")
        self.assertTrue(row["safe"])

    def test_exact_import_duplicate_is_not_safe(self):
        snapshot = {"character_id": "abc", "name": "Bot"}
        digest = review._snapshot_hash(snapshot)
        existing = {
            "status": {"current": "public"},
            "lastKnown": snapshot.copy(),
            "imports": {"qolBotStatus": {"snapshots": [{"sha256": digest}]}},
        }
        row = review.classify_record(
            bot_id="abc",
            saved_at="2026-09-20T00:00:00Z",
            snapshot=snapshot,
            observed_status="public",
            problems=[],
            existing=existing,
        )
        self.assertEqual(row["classification"], "exact_duplicate")
        self.assertFalse(row["safe"])

    def test_changed_record_reports_fields(self):
        existing = {
            "status": {"current": "public"},
            "lastKnown": {"character_id": "abc", "name": "Old", "tags": ["Fantasy"]},
            "imports": {},
        }
        row = review.classify_record(
            bot_id="abc",
            saved_at="2026-09-20T00:00:00Z",
            snapshot={"character_id": "abc", "name": "New", "tags": ["Fantasy"]},
            observed_status="public",
            problems=[],
            existing=existing,
        )
        self.assertEqual(row["classification"], "changed")
        self.assertIn("name", row["diffFields"])
        self.assertTrue(row["safe"])

    def test_updatedAt_and_metrics_do_not_create_changed_classification(self):
        existing = {
            "status": {"current": "public"},
            "lastKnown": {
                "character_id": "abc",
                "name": "Bot",
                "updatedAt": "old",
                "num_messages": 10,
            },
            "imports": {},
        }
        row = review.classify_record(
            bot_id="abc",
            saved_at="2026-09-20T00:00:00Z",
            snapshot={
                "character_id": "abc",
                "name": "Bot",
                "updatedAt": "new",
                "num_messages": 9999,
            },
            observed_status="public",
            problems=[],
            existing=existing,
        )
        self.assertEqual(row["classification"], "known")

    def test_submitted_deleted_status_is_warning_only(self):
        existing = {
            "status": {"current": "public"},
            "lastKnown": {"character_id": "abc", "name": "Bot"},
            "imports": {},
        }
        row = review.classify_record(
            bot_id="abc",
            saved_at="2026-09-20T00:00:00Z",
            snapshot={"character_id": "abc", "name": "Bot"},
            observed_status="deleted",
            problems=[],
            existing=existing,
        )
        self.assertEqual(row["classification"], "known")
        self.assertTrue(row["unavailableHistory"])
        self.assertTrue(row["safe"])
        self.assertEqual(row["existingStatus"], "public")

    def test_problem_record_cannot_be_approve_safe(self):
        row = review.classify_record(
            bot_id="abc",
            saved_at="2026-09-20T00:00:00Z",
            snapshot={"character_id": "abc", "name": "Bot"},
            observed_status="public",
            problems=["id_mismatch"],
            existing=None,
        )
        self.assertEqual(row["classification"], "problem")
        self.assertFalse(row["safe"])


if __name__ == "__main__":
    unittest.main()
