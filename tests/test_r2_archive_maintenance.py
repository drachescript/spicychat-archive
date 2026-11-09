import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import archive
import r2_archive_maintenance as maintenance


class ScheduledLatestOnlyExplorationTests(unittest.TestCase):
    def test_automatic_run_skips_deep_crawl_without_advancing_cursor(self):
        checkpoint = {
            "mode": "cursor",
            "cursorCreatedAt": 12345,
            "page": 119,
            "pass": 7,
            "adaptivePages": 1000,
        }
        state = {"exploration": checkpoint.copy()}
        config = {
            "crawler": {"scheduled_latest_only": True},
            "listings": {"latest": "createdAt:desc"},
        }
        # A deep crawler call would be an error in this profile.
        with patch.object(maintenance, "_original_configure_cloud", return_value=(state, object())):
            with patch.object(maintenance.legacy, "explore_more", side_effect=AssertionError("deep crawler invoked")), \
                 patch.object(maintenance.legacy, "scan_listing"), \
                 patch.object(maintenance.legacy, "observe_bot"), \
                 patch.object(maintenance.legacy, "enrich_queue"), \
                 patch.object(maintenance.legacy, "verify_missing"):
                maintenance.maintenance_configure_cloud(config, FakeStore([]))
                result = maintenance.legacy.explore_more(None, config, "2026-10-13T00:00:00Z", state)
        self.assertTrue(result["skipped"])
        self.assertEqual(result["pagesCompleted"], 0)
        self.assertEqual(result["pageBudget"], 0)
        self.assertEqual(state["exploration"], checkpoint)



class FullCatalogAuditOrderTests(unittest.TestCase):
    def test_missing_verification_waits_until_public_pass_natural_end(self):
        from unittest.mock import Mock
        state = {"exploration": {"mode": "cursor", "pass": 3}, "missingChecks": {
            "suspect-id": {"count": 0, "lastAt": None, "reason": "absent-from-complete-typesense-pass"},
        }}
        config = {"crawler": {"catalog_audit_run": True}}
        partial = {"naturalEnd": False, "pagesCompleted": 2500,
                   "changed": 2, "missingQueued": 0, "errors": [], "timeLimited": False}
        with patch.object(maintenance, "_original_configure_cloud",
                          return_value=(state, object())), \
             patch.object(archive, "scan_listing"), \
             patch.object(archive, "explore_more", return_value=partial), \
             patch.object(archive, "observe_bot"), \
             patch.object(archive, "enrich_queue"), \
             patch.object(archive, "verify_missing", return_value={
                 "verified": 5, "deleted": 0, "restored": 0,
             }) as verifier:
            maintenance.maintenance_configure_cloud(config, FakeStore([]))
            progress = archive.explore_more(None, config, "2026-11-09T00:00:00Z", state)
            self.assertFalse(progress["naturalEnd"])
            result = archive.verify_missing(None, config, "2026-11-09T00:00:00Z", state, set())
            self.assertTrue(result["skipped"])
            self.assertEqual(result["verified"], 0)
            self.assertEqual(result["deleted"], 0)
            verifier.assert_not_called()
            self.assertNotIn("_catalogAuditReady", state)
            self.assertIn("suspect-id", state["missingChecks"])

    def test_completed_public_pass_allows_bounded_missing_checks(self):
        from unittest.mock import Mock
        state = {"exploration": {"mode": "page", "pass": 4}, "missingChecks": {
            "suspect-id": {"count": 0, "lastAt": None, "reason": "absent-from-complete-typesense-pass"},
        }}
        config = {"crawler": {"catalog_audit_run": True, "missing_verification_budget": 2000}}
        completed = {"naturalEnd": True, "pagesCompleted": 1300,
                     "changed": 20, "missingQueued": 100, "errors": [], "timeLimited": False}
        with patch.object(maintenance, "_original_configure_cloud",
                          return_value=(state, object())), \
             patch.object(archive, "scan_listing"), \
             patch.object(archive, "explore_more", return_value=completed), \
             patch.object(archive, "observe_bot"), \
             patch.object(archive, "enrich_queue"), \
             patch.object(archive, "verify_missing", return_value={
                 "verified": 12, "deleted": 0, "restored": 0,
             }) as verifier:
            maintenance.maintenance_configure_cloud(config, FakeStore([]))
            progress = archive.explore_more(None, config, "2026-11-09T01:00:00Z", state)
            self.assertTrue(progress["naturalEnd"])
            result = archive.verify_missing(None, config, "2026-11-09T01:00:00Z", state, set())
            self.assertEqual(result["verified"], 12)
            self.assertFalse(result.get("skipped", False))
            verifier.assert_called_once()
            self.assertEqual(state["catalogAudit"]["lastCandidatesQueued"], 100)
            self.assertEqual(state["catalogAudit"]["lastPublicChangesSaved"], 20)
            self.assertTrue(state["catalogAudit"]["lastPassComplete"])
            self.assertEqual(state["catalogAudit"]["lastCompletedPass"], 4)
            self.assertNotIn("_catalogAuditReady", state)


    def test_verify_only_skips_typesense_and_arms_bounded_catchup(self):
        from unittest.mock import Mock
        state = {"missingChecks": {"bot-a": {"count": 0}}}
        config = {"crawler": {"verify_missing_only": True},
                  "listings": {"latest": "createdAt:desc"}}
        with patch.object(maintenance, "_original_configure_cloud",
                          return_value=(state, object())), \
             patch.object(archive, "scan_listing") as scanner, \
             patch.object(archive, "explore_more"), \
             patch.object(archive, "observe_bot"), \
             patch.object(archive, "enrich_queue"), \
             patch.object(archive, "verify_missing"):
            maintenance.maintenance_configure_cloud(config, FakeStore([]))
            result = archive.scan_listing(None, config, "latest", "createdAt:desc", "now", state)
            self.assertTrue(result["skipped"])
            self.assertFalse(result["ok"])
            scanner.assert_not_called()
        self.assertTrue(state["missingVerificationCatchup"]["enabled"])
        self.assertEqual(state["missingVerificationCatchup"]["perRunBudget"], 5000)
        self.assertNotIn("runsRemaining", state["missingVerificationCatchup"])

    def test_existing_catchup_survives_expired_legacy_run_counter(self):
        state = {"missingChecks": {"bot-a": {"count": 0}},
                 "missingVerificationCatchup": {"runsRemaining": 0, "perRunBudget": 5000}}
        for batch in range(3):
            config = {"crawler": {"scheduled_latest_only": True, "missing_verification_budget": 500}}
            with patch.object(maintenance, "_original_configure_cloud",
                              return_value=(state, object())), \
                 patch.object(archive, "scan_listing"), \
                 patch.object(archive, "explore_more"), \
                 patch.object(archive, "observe_bot"), \
                 patch.object(archive, "enrich_queue"), \
                 patch.object(archive, "verify_missing", return_value={
                     "verified": 1, "deleted": 0, "restored": 0,
                 }):
                maintenance.maintenance_configure_cloud(config, FakeStore([]))
                self.assertEqual(config["crawler"]["missing_verification_budget"], 5000)
                archive.verify_missing(None, config, f"run-{batch}", state, set())
            self.assertTrue(state["missingVerificationCatchup"]["enabled"])
            self.assertEqual(state["missingVerificationCatchup"]["runsCompleted"], batch + 1)
            self.assertNotIn("runsRemaining", state["missingVerificationCatchup"])

        state["missingChecks"].clear()
        config = {"crawler": {"scheduled_latest_only": True, "missing_verification_budget": 500}}
        with patch.object(maintenance, "_original_configure_cloud",
                          return_value=(state, object())):
            maintenance.maintenance_configure_cloud(config, FakeStore([]))
        self.assertEqual(config["crawler"]["missing_verification_budget"], 500)
        self.assertFalse(state["missingVerificationCatchup"]["enabled"])
        self.assertIn("completedAt", state["missingVerificationCatchup"])

    def test_final_batch_marks_catchup_complete_and_restores_normal_budget(self):
        state = {"missingChecks": {"bot-a": {"count": 0}},
                 "missingVerificationCatchup": {"enabled": True, "perRunBudget": 5000,
                                                "runsCompleted": 22}}
        config = {"crawler": {"scheduled_latest_only": True, "missing_verification_budget": 500}}

        def resolve(*args):
            state["missingChecks"].clear()
            return {"verified": 1, "deleted": 1, "restored": 0}

        with patch.object(maintenance, "_original_configure_cloud",
                          return_value=(state, object())), \
             patch.object(archive, "scan_listing"), \
             patch.object(archive, "explore_more"), \
             patch.object(archive, "observe_bot"), \
             patch.object(archive, "enrich_queue"), \
             patch.object(archive, "verify_missing", side_effect=resolve):
            maintenance.maintenance_configure_cloud(config, FakeStore([]))
            self.assertEqual(config["crawler"]["missing_verification_budget"], 5000)
            archive.verify_missing(None, config, "finished", state, set())

        self.assertEqual(state["missingVerificationCatchup"]["runsCompleted"], 23)
        self.assertFalse(state["missingVerificationCatchup"]["enabled"])
        self.assertEqual(state["missingVerificationCatchup"]["completedAt"], "finished")
        normal_config = {"crawler": {"scheduled_latest_only": True, "missing_verification_budget": 500}}
        with patch.object(maintenance, "_original_configure_cloud",
                          return_value=(state, object())):
            maintenance.maintenance_configure_cloud(normal_config, FakeStore([]))
        self.assertEqual(normal_config["crawler"]["missing_verification_budget"], 500)



class FakeStore:
    def __init__(self, ids):
        self.discovery_order = list(ids)
        self.deleted_index = set()


class FakeClient:
    def character(self, bot_id):
        return archive.HTTPResult(
            True,
            200,
            data={"character": {"character_id": bot_id, "greeting": f"hello {bot_id}"}},
        )


class MetricOnlyClient:
    def character(self, bot_id):
        return archive.HTTPResult(
            True,
            200,
            data={
                "character": {
                    "character_id": bot_id,
                    "greeting": "same greeting",
                    "num_messages": 11,
                }
            },
        )


class ConcurrentFakeClient:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def character(self, bot_id):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.02)
            return archive.HTTPResult(
                True,
                200,
                data={"character": {"character_id": bot_id, "greeting": f"hello {bot_id}"}},
            )
        finally:
            with self.lock:
                self.active -= 1


def make_record(bot_id, *, greeting=None, num_messages=None):
    current = {}
    last_known = {}
    metrics = {"latest": {}, "history": []}
    if greeting is not None or num_messages is not None:
        character_api = {"character_id": bot_id}
        last_known["character_id"] = bot_id
        if greeting is not None:
            character_api["greeting"] = greeting
            last_known["greeting"] = greeting
        if num_messages is not None:
            character_api["num_messages"] = num_messages
            last_known["num_messages"] = num_messages
            metrics["latest"] = {"num_messages": num_messages}
        current["character-api"] = character_api

    return {
        "schemaVersion": 1,
        "id": bot_id,
        "firstSeenAt": "2026-09-29T00:00:00Z",
        "lastSeenAt": "2026-09-29T00:00:00Z",
        "status": {"current": "public", "since": "2026-09-29T00:00:00Z"},
        "sources": {},
        "current": current,
        "lastKnown": last_known,
        "fieldHistory": [],
        "availabilityHistory": [],
        "listings": {},
        "metrics": metrics,
        "avatarArchive": {},
    }


class MaintenanceSweepTests(unittest.TestCase):
    def test_manual_discovery_profile_skips_character_refresh_without_advancing_cursor(self):
        store = FakeStore(["bot-a", "bot-b", "bot-c"])
        state = {
            "priorityEnrichment": ["bot-a"],
            "verificationSweep": {"cursor": 2, "completedPasses": 3},
        }
        config = {"crawler": {"maintenance_verification_enabled": False}}

        result = maintenance._maintenance_character_refresh(
            FakeClient(),
            config,
            "2026-10-03T00:00:00Z",
            state,
            store=store,
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["verificationCursor"], 2)
        self.assertEqual(result["verificationCompletedPasses"], 3)
        self.assertEqual(state["priorityEnrichment"], ["bot-a"])
        self.assertTrue(state["verificationSweep"]["lastRunSkipped"])

    def test_first_404_is_recorded_once(self):
        state = {}
        maintenance._seed_missing_404(state, "bot-a", "2026-09-30T00:00:00Z")
        info = state["missingChecks"]["bot-a"]
        self.assertEqual(info["count"], 1)
        self.assertEqual(info["lastStatus"], 404)

        maintenance._seed_missing_404(state, "bot-a", "2026-09-30T03:00:00Z")
        info = state["missingChecks"]["bot-a"]
        self.assertEqual(info["count"], 1)
        self.assertEqual(info["lastAt"], "2026-09-30T00:00:00Z")

    def test_character_refresh_resumes_and_wraps_cursor(self):
        store = FakeStore(["bot-a", "bot-b", "bot-c"])
        state = {"priorityEnrichment": []}
        config = {
            "crawler": {
                "maintenance_verification_budget": 2,
                "maintenance_priority_budget": 1,
                "maintenance_verification_workers": 2,
                "maintenance_verification_time_limit_seconds": 60,
            }
        }

        records = {
            bot_id: make_record(bot_id)
            for bot_id in store.discovery_order
        }

        with patch.object(archive, "load_bot", side_effect=lambda bot_id: records[bot_id]), patch.object(
            archive, "save_bot", return_value=True
        ):
            first = maintenance._maintenance_character_refresh(
                FakeClient(),
                config,
                "2026-09-30T00:00:00Z",
                state,
                store=store,
            )
            self.assertEqual(first["processed"], 2)
            self.assertEqual(first["verificationCursor"], 2)
            self.assertEqual(first["verificationCompletedPasses"], 0)

            second = maintenance._maintenance_character_refresh(
                FakeClient(),
                config,
                "2026-09-30T03:00:00Z",
                state,
                store=store,
            )
            self.assertEqual(second["processed"], 2)
            self.assertEqual(second["verificationCursor"], 1)
            self.assertEqual(second["verificationCompletedPasses"], 1)

    def test_metric_only_updates_are_not_content_changes(self):
        store = FakeStore(["bot-a"])
        state = {"priorityEnrichment": []}
        config = {
            "crawler": {
                "maintenance_verification_budget": 1,
                "maintenance_priority_budget": 1,
                "maintenance_verification_workers": 1,
                "maintenance_verification_time_limit_seconds": 60,
            }
        }
        record = make_record("bot-a", greeting="same greeting", num_messages=10)

        with patch.object(archive, "load_bot", return_value=record), patch.object(
            archive, "save_bot", return_value=True
        ):
            result = maintenance._maintenance_character_refresh(
                MetricOnlyClient(),
                config,
                "2026-09-30T06:00:00Z",
                state,
                store=store,
            )

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["contentChanged"], 0)
        self.assertEqual(result["metricUpdates"], 0)
        self.assertEqual(record["metrics"]["latest"]["num_messages"], 10)


    def test_public_missing_candidate_is_not_counted_as_restored(self):
        store = FakeStore(["bot-a"])
        state = {
            "priorityEnrichment": [],
            "missingChecks": {
                "bot-a": {
                    "count": 1,
                    "lastAt": "2026-09-30T00:00:00Z",
                    "lastStatus": 404,
                }
            },
        }
        config = {
            "crawler": {
                "maintenance_verification_budget": 1,
                "maintenance_priority_budget": 1,
                "maintenance_verification_workers": 1,
                "maintenance_verification_time_limit_seconds": 60,
            }
        }
        record = make_record("bot-a", greeting="old")

        with patch.object(archive, "load_bot", return_value=record), patch.object(
            archive, "save_bot", return_value=True
        ):
            result = maintenance._maintenance_character_refresh(
                FakeClient(),
                config,
                "2026-09-30T06:00:00Z",
                state,
                store=store,
            )

        self.assertNotIn("bot-a", state["missingChecks"])
        self.assertEqual(result["restored"], 0)

    def test_character_requests_run_concurrently(self):
        ids = [f"bot-{index}" for index in range(8)]
        store = FakeStore(ids)
        state = {"priorityEnrichment": []}
        config = {
            "crawler": {
                "maintenance_verification_budget": 8,
                "maintenance_priority_budget": 1,
                "maintenance_verification_workers": 4,
                "maintenance_verification_time_limit_seconds": 60,
            }
        }
        records = {bot_id: make_record(bot_id) for bot_id in ids}
        client = ConcurrentFakeClient()

        with patch.object(archive, "load_bot", side_effect=lambda bot_id: records[bot_id]), patch.object(
            archive, "save_bot", return_value=True
        ):
            result = maintenance._maintenance_character_refresh(
                client,
                config,
                "2026-09-30T06:00:00Z",
                state,
                store=store,
            )

        self.assertEqual(result["processed"], 8)
        self.assertEqual(result["verificationWorkers"], 4)
        self.assertGreaterEqual(client.max_active, 2)


if __name__ == "__main__":
    unittest.main()
