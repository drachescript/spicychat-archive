"""Regression tests: archive ID gaps are NOT fabricated missing bots."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_bot_status_audit as status_audit
import audit_discovery_ids as id_audit


class BotAccountabilityTests(unittest.TestCase):
    def test_last_observed_statuses_and_materialized_count_are_separate(self):
        creators = [{
            "alice": {"bots": {
                "public-id": {"status": "public", "lastSeenAt": "2026-10-21T12:00:00Z", "name": "Public"},
                "gone-id": {"status": "deleted", "lastSeenAt": "2026-09-01T12:00:00Z", "name": "Gone"},
                "unknown-id": {"status": "unknown", "name": "Unknown"},
            }},
            "bob": {"bots": {
                "public-id": {"status": "public"},  # duplicate creator mapping
            }},
        }]
        report = status_audit.audit(
            creators, observed_ids=9, indexed_records=5,
            now="2026-10-22T12:00:00Z", index_generated_at="2026-10-22T11:00:00Z",
            pending_checks=1,
        )
        self.assertEqual(report["observedDiscoveryIds"], 9)
        self.assertEqual(report["materializedBotIndexCount"], 5)
        self.assertEqual(report["discoveryMinusIndexedCount"], 4)
        self.assertIs(report["discoveryMinusIndexedIsIdentifiedIds"], False)
        self.assertEqual(report["creatorIndexedIds"], 3)
        self.assertEqual(report["indexedWithoutCreatorMetadataCount"], 2)
        self.assertEqual(report["lastObservedStatus"]["public"], 1)
        self.assertEqual(report["lastObservedStatus"]["deleted"], 1)
        self.assertEqual(report["lastObservedStatus"]["unknown"], 1)
        self.assertEqual(report["lastSeenMoreThan30DaysAgo"], 1)
        self.assertEqual(report["creatorIndexDuplicatedIds"], 1)

    def test_expired_index_is_marked_outdated(self):
        report = status_audit.audit(
            [], observed_ids=0, indexed_records=0,
            now="2026-10-22T12:00:00Z", index_generated_at="2026-09-22T12:00:00Z",
        )
        self.assertTrue(report["materializedIndexOlderThan7Days"])
        self.assertIsNone(report["pendingApiMissingChecks"])

    def test_per_id_diff_does_not_infer_live_deletion(self):
        only_discovered, only_indexed, overlap = id_audit.compare_sets(
            ["a", "b", "c", "a"], ["A", "c", "d"]
        )
        self.assertEqual(only_discovered, ["b"])
        self.assertEqual(only_indexed, ["d"])
        self.assertEqual(overlap, 2)
        report = id_audit.classify(
            observed_count=3, indexed_count=3,
            discovery_only=only_discovered, index_only=only_indexed, overlap=overlap,
            head_results=[("b", False)],
            at="2026-10-22T12:00:00Z", max_checks=100,
        )
        self.assertEqual(report["discoveryOnlyWithNoBotFile"], 1)
        self.assertTrue(report["allDiscoveryOnlyIdsChecked"])
        self.assertTrue(report["doesNotVerifyPresentDaySpicyChat"])
        self.assertEqual(report["discoveryOnlyWithoutBotFileIds"], ["b"])

    def test_incomplete_head_checks_explicitly_stay_unchecked(self):
        report = id_audit.classify(
            observed_count=4, indexed_count=0,
            discovery_only=["a", "b", "c", "d"], index_only=[], overlap=0,
            head_results=[("a", False), ("b", True)],
            at="2026-10-22T12:00:00Z", max_checks=2,
        )
        self.assertEqual(report["discoveryOnlyUnchecked"], 2)
        self.assertFalse(report["allDiscoveryOnlyIdsChecked"])
        self.assertEqual(report["discoveryOnlyWithStoredBotFile"], 1)

    def test_no_destructive_archive_ops_in_auditors(self):
        scripts = [
            ROOT / "scripts" / "publish_bot_status_audit.py",
            ROOT / "scripts" / "audit_discovery_ids.py",
        ]
        for path in scripts:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("delete_objects(", source)
            self.assertNotIn("clear_deleted_summary(", source)
            self.assertNotIn("save_discovery_order(", source)


if __name__ == "__main__":
    unittest.main()
