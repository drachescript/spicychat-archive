import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import inconclusive_review as mod

A = "c0c8beed-9afd-457b-bda0-c142a6805b0c"
B = "b376faf0-8c37-4b37-8861-8ca68cdd64b6"
C = "f7f66631-9105-4402-b5b7-f46ecee2e470"


class ReviewIndexTests(unittest.TestCase):
    def test_distinguishes_errors_empty_payload_and_waiting_404(self):
        state = {"missingChecks": {
            A: {"count": 0, "lastAt": "2026-11-09T08:00:00Z", "lastStatus": 429,
                "queuedAt": "2026-11-09T05:00:00Z", "errorKind": "http-429"},
            B: {"count": 1, "lastAt": "2026-11-09T08:00:00Z", "lastStatus": 404},
            C: {"count": 0, "lastAt": "2026-11-09T08:00:00Z", "lastStatus": 200,
                "errorKind": "empty-character-payload"},
            "other": {"lastAt": "2026-11-09T08:00:00Z", "lastStatus": 500},
            "invalid-type": "private",
        }}
        index, corrections = mod.build_review_index(
            state, {"schemaVersion": 1, "corrections": []}, "2026-11-09T08:01:00Z")
        self.assertEqual(index["pendingTotal"], 5)
        self.assertEqual(index["caseCount"], 3)
        self.assertEqual(index["counts"], {
            "api-error": 1, "empty-response": 1, "awaiting-confirmation": 1,
        })
        for row in index["cases"]:
            self.assertTrue(row["spicychatUrl"].endswith(row["id"]))
            self.assertIn(row["id"], row["archiveUrl"])
            self.assertIn(row["category"], mod.ERROR_CLASSES)
        self.assertEqual(corrections["corrections"], [])

    def test_unqueried_does_not_look_like_an_error(self):
        state = {"missingChecks": {
            A: {"count": 0, "lastAt": None, "lastStatus": None},
        }}
        index, _ = mod.build_review_index(state, {}, "now")
        self.assertEqual(index["caseCount"], 0)
        self.assertEqual(index["notYetChecked"], 1)

    def test_corrections_are_only_annotations_then_pruned_on_conclusive_result(self):
        correction = {
            "id": A, "designation": "unavailable",
            "evidenceUrl": "https://spicychat.ai/chatbot/" + A,
            "note": "Verified manually",
        }
        state = {"missingChecks": {
            A: {"count": 0, "lastAt": "now", "lastStatus": 503},
            B: {"count": 1, "lastAt": "now", "lastStatus": 404},
        }}
        manual = {"schemaVersion": 1, "corrections": [
            correction,
            {"id": C, "designation": "public", "note": "Previously corrected"},
        ]}
        index, retained = mod.build_review_index(state, manual, "now")
        self.assertEqual(len(retained["corrections"]), 1)
        self.assertEqual(retained["corrections"][0]["id"], A)
        self.assertEqual(next(c for c in index["cases"] if c["id"] == A)["manualReview"]["designation"], "unavailable")
        # After API confirmation removes the resolved bot ID, no stale review survives.
        del state["missingChecks"][A]
        index2, corrected = mod.build_review_index(state, retained, "later")
        self.assertEqual(index2["caseCount"], 1)
        self.assertEqual(corrected["corrections"], [])

    def test_manual_corrections_cannot_inject_urls_or_untrusted_statuses(self):
        corrections = mod.normalize_corrections({"corrections": [
            {"id": A, "designation": "deleted", "note": "not allowed"},
            {"id": A, "designation": "restricted", "evidenceUrl": "javascript:alert(1)",
             "correctBotId": B, "note": "a" * 1200},
            {"id": B, "designation": "public", "evidenceUrl": "http://example.com"},
            {"id": C, "designation": "inconclusive", "evidenceUrl": "https://example.com/evidence"},
            {"id": "not-an-id", "designation": "public"},
        ]})
        self.assertEqual(len(corrections), 3)
        self.assertEqual(corrections[0]["designation"], "restricted")
        self.assertNotIn("evidenceUrl", corrections[0])
        self.assertEqual(corrections[0]["correctBotId"], B)
        self.assertLessEqual(len(corrections[0]["note"]), 500)
        self.assertNotIn("evidenceUrl", corrections[1])
        self.assertEqual(corrections[2]["evidenceUrl"], "https://example.com/evidence")

    def test_publish_reuses_in_memory_queue_and_prunes_stale_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            mod.write_if_changed(data / "inconclusive-corrections.json", {
                "schemaVersion": 1,
                "corrections": [{"id": A, "designation": "restricted"},
                                {"id": B, "designation": "public"}],
            })
            state = {"missingChecks": {
                A: {"lastStatus": 403, "lastAt": "2026-11-09T05:00:00Z"},
            }}
            index = mod.publish_review_files(data, state, "2026-11-09T08:00:00Z")
            self.assertEqual(index["caseCount"], 1)
            self.assertIn('"api-error"', (data / "inconclusive.json").read_text())
            self.assertNotIn(B, (data / "inconclusive-corrections.json").read_text())
            self.assertFalse(mod.write_if_changed(data / "inconclusive.json", index))


if __name__ == "__main__":
    unittest.main()
