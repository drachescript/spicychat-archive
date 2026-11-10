import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import inconclusive_review as review
from inconclusive_metadata import image_url, preview_from_record, enrich_case_previews

A = "c0c8beed-9afd-457b-bda0-c142a6805b0c"
B = "b376faf0-8c37-4b37-8861-8ca68cdd64b6"
BASE = "https://data.spicychatarchive.drache.uk"
NOW = "2026-11-10T20:00:00Z"
SOON = "2026-11-10T22:00:00Z"


class ReviewMetadataTests(unittest.TestCase):
    def test_name_and_avatar_from_existing_archive_record(self):
        record = {
            "lastKnown": {
                "name": "   Saved   Example  ", "title": "Old title",
                "creator_username": "author",
                "avatar_url": "avatars/saved.jpg",
            },
            "avatarArchive": {"r2Key": "media/ab/avatar.webp"},
        }
        result = preview_from_record(record, BASE)
        self.assertEqual(result["name"], "Saved Example")
        self.assertEqual(result["title"], "Old title")
        self.assertEqual(result["creator"], "author")
        self.assertEqual(result["avatar"], BASE + "/media/ab/avatar.webp")
        self.assertEqual(result["avatarFallback"], "https://cdn.nd-api.com/avatars/saved.jpg?class=avatar256x256")
        self.assertFalse(result["isNsfw"])

    def test_unknown_and_insecure_images_are_not_fabricated(self):
        self.assertEqual(preview_from_record(None), {})
        self.assertNotIn("name", preview_from_record({"lastKnown": {"name": "Unknown bot"}}))
        self.assertEqual(image_url("javascript:alert(1)", BASE), "")
        self.assertEqual(image_url("http://example.com/a.jpg", BASE), "")
        self.assertEqual(image_url("media/foo.webp", ""), "")
        self.assertEqual(image_url("avatars/test.webp"), "https://cdn.nd-api.com/avatars/test.webp?class=avatar256x256")

    def test_publish_enriches_only_once_and_preserves_corrections(self):
        state = {"missingChecks": {
            A: {"lastAt": NOW, "lastStatus": 404, "count": 1},
            B: {"lastAt": NOW, "lastStatus": 503, "count": 0},
        }}
        calls = []
        def lookup(bot_id):
            calls.append(bot_id)
            return ({
                "lastKnown": {"name": "Bot A", "avatar_url": "avatars/a.webp"},
            } if bot_id == A else None)

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            review.write_if_changed(directory / "inconclusive-corrections.json", {
                "schemaVersion": 1,
                "corrections": [{"id": A, "designation": "restricted", "note": "Manual note"}],
            })
            first = review.publish_review_files(
                directory, state, NOW, lookup_record=lookup, public_base=BASE,
            )
            self.assertEqual(sorted(calls), sorted([A, B]))
            saved = {row["id"]: row for row in first["cases"]}
            self.assertEqual(saved[A]["name"], "Bot A")
            self.assertEqual(saved[A]["avatar"], "https://cdn.nd-api.com/avatars/a.webp?class=avatar256x256")
            self.assertEqual(saved[A]["previewCheckedAt"], NOW)
            self.assertEqual(saved[B]["previewCheckedAt"], NOW)
            self.assertNotIn("name", saved[B])
            corrections = review.json.loads((directory / "inconclusive-corrections.json").read_text())
            self.assertEqual(next(x for x in corrections["corrections"] if x["id"] == A)["designation"], "restricted")

            calls.clear()
            second = review.publish_review_files(
                directory, state, SOON, lookup_record=lookup, public_base=BASE,
            )
            self.assertEqual(calls, [])
            self.assertEqual({x["id"]: x for x in second["cases"]}[A]["name"], "Bot A")

            # A conclusively resolved review case is removed without stale
            # preview metadata or manual classifications lingering.
            state["missingChecks"].pop(A)
            third = review.publish_review_files(
                directory, state, SOON, lookup_record=lookup, public_base=BASE,
            )
            self.assertEqual([x["id"] for x in third["cases"]], [B])
            corrections = review.json.loads((directory / "inconclusive-corrections.json").read_text())
            self.assertEqual([x["id"] for x in corrections["corrections"]], [B])

    def test_budget_defers_work_and_errors_can_retry(self):
        rows = [{"id": A}, {"id": B}]
        bad = lambda bot_id: (_ for _ in ()).throw(RuntimeError("temporary R2 error"))
        stats = enrich_case_previews(rows, [], bad, NOW, max_lookups=1, workers=1)
        self.assertEqual(stats["lookupAttempts"], 1)
        self.assertEqual(stats["lookupErrors"], 1)
        self.assertEqual(stats["deferred"], 1)
        self.assertNotIn("previewCheckedAt", rows[0])
        self.assertNotIn("previewCheckedAt", rows[1])


if __name__ == "__main__":
    unittest.main()
