import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rulebook_archive as rules


class RulebookArchiveTests(unittest.TestCase):
    def setUp(self):
        self.id = "a21710ec-3a34-4e45-aa62-d291bbee5744"
        self.doc = {
            "rulebook_id": self.id, "name": "Public sample",
            "description": "Public listing text", "creator_username": "example",
            "tags": ["Focus", "Slow Burn"], "num_rules": 12,
            "createdAt": 1793586900,
            "rules": [{"id": "secret", "content": "private rule text"}],
            "variables": [{"name": "hidden", "value": "secret"}],
            "private_config": {"token": "should never appear"},
        }

    def test_known_public_cover_references_are_url_only(self):
        refs = rules.load_public_cover_references()
        self.assertEqual(len(refs), 48)
        dante = "63416959-f266-593d-830d-37bc1c474ef9"
        self.assertEqual(
            refs[dante],
            "https://cdn.nd-api.com/avatars/66f8c467-db24-4546-b36b-0e94d19d4f38.webp?class=image500x650",
        )
        self.assertTrue(all(url.startswith("https://cdn.nd-api.com/") for url in refs.values()))

    def test_cover_priority_and_stable_fingerprint(self):
        id = self.id
        reference = "https://cdn.nd-api.com/avatars/11111111-2222-3333-4444-555555555555.webp?class=image500x650"
        enriched = rules.listing_with_cover(self.doc, id, cover_references={id: reference})
        self.assertEqual(enriched["cover_image_url"], reference)
        record, changed = rules.build_record(None, {**self.doc, **enriched}, "2026-11-04T12:00:00Z")
        self.assertTrue(changed)
        newer, changed = rules.build_record(record, {**self.doc, **enriched}, "2026-11-04T13:00:00Z")
        self.assertFalse(changed)
        self.assertEqual(record["fingerprint"], rules.digest(enriched))
        self.assertEqual(newer["fingerprint"], record["fingerprint"])
        public = {**self.doc, "image_url": "avatars/public.webp"}
        self.assertEqual(
            rules.listing_with_cover(public, id, cover_references={id: reference})["image_url"],
            "avatars/public.webp",
        )
        self.assertNotIn("cover_image_url", rules.listing_with_cover(public, id, cover_references={id: reference}))

    def test_cover_metadata_preserved_without_private_rule_content(self):
        doc = {**self.doc, "cover_image_url": "rulebooks/public-cover.webp"}
        record, _ = rules.build_record(None, doc, "2026-11-04T12:00:00Z")
        self.assertEqual(record["listing"]["cover_image_url"], "rulebooks/public-cover.webp")
        self.assertEqual(rules.summary(record)["cover"], "rulebooks/public-cover.webp")
        self.assertNotIn("private rule text", str(record))
        # If a later public listing omits the image, retain the last-known cover
        # so a missing public image field never erases the archived reference.
        newer, changed = rules.build_record(record, self.doc, "2026-11-04T13:00:00Z")
        self.assertFalse(changed)
        self.assertEqual(newer["listing"]["cover_image_url"], "rulebooks/public-cover.webp")
        self.assertEqual(len(newer["versions"]), 1)

    def test_multiple_public_image_fields_without_rule_body_extraction(self):
        for field in ("cover_url", "coverImage", "avatar_url", "image_url", "image"):
            with self.subTest(field=field):
                record, _ = rules.build_record(None, {**self.doc, field: "cover.webp"},
                                                "2026-11-04T14:00:00Z")
                self.assertEqual(rules.summary(record)["cover"], "cover.webp")
                self.assertNotIn("secret", str(record))

    def test_cover_diagnostics_only_list_public_field_names(self):
        doc = {
            **self.doc,
            "coverImageFilename": "safe-image.webp",
            "thumbnail_url": "safe-thumb.webp",
            "private_image_secret": "do-not-log",
            "imageAuthToken": "do-not-log",
        }
        fields = rules.public_cover_field_names(doc)
        self.assertEqual(fields, ["coverImageFilename", "thumbnail_url"])
        self.assertNotIn("do-not-log", str(fields))
        self.assertNotIn("private_image_secret", fields)

    def test_uuid_only_public_rulebook_ids(self):
        self.assertEqual(rules.rulebook_id(self.doc), self.id)
        self.assertIsNone(rules.rulebook_id({"id": "../../private"}))
        self.assertIsNone(rules.rulebook_id({"rulebook_id": "secret"}))

    def test_public_whitelist_never_saves_rule_bodies_or_secret_variables(self):
        value = rules.public_metadata(self.doc)
        self.assertEqual(value["name"], "Public sample")
        self.assertEqual(value["num_rules"], 12)
        self.assertNotIn("rules", value)
        self.assertNotIn("variables", value)
        self.assertNotIn("private_config", value)
        record, changed = rules.build_record(None, self.doc, "2026-11-01T00:00:00Z")
        self.assertTrue(changed)
        self.assertNotIn("private rule text", str(record))
        self.assertEqual(len(record["versions"]), 1)
        again, changed = rules.build_record(record, self.doc, "2026-11-02T00:00:00Z")
        self.assertFalse(changed)
        self.assertEqual(len(again["versions"]), 1)

    def test_status_change_only_means_no_longer_public(self):
        record, _ = rules.build_record(None, self.doc, "2026-11-01T00:00:00Z")
        self.assertTrue(rules.apply_disappearance(record, "2026-11-02T00:00:00Z"))
        self.assertEqual(record["status"]["current"], "not-public")
        self.assertFalse(rules.apply_disappearance(record, "2026-11-02T00:00:01Z"))
        restored, change = rules.build_record(record, self.doc, "2026-11-03T00:00:00Z")
        self.assertTrue(change)
        self.assertEqual(restored["status"]["current"], "public")
        self.assertEqual(len(restored["versions"]), 1)

    def test_uses_separate_public_collection(self):
        search = rules.query(page=2, per_page=48)
        self.assertEqual(search["collection"], "rulebooks_public")
        self.assertEqual(search["filter_by"], "application_ids:=spicychat")
        self.assertNotIn("creator_user_id", search["filter_by"])

    def test_public_key_must_not_fall_back_to_bot_or_lorebook(self):
        client = Mock()
        client.guest_user_id = "guest"
        client.timeout = 5
        client.session.get.return_value.ok = True
        client.session.get.return_value.content = b"{}"
        client.session.get.return_value.json.return_value = {
            "typesenseConfig": {
                "apiKeyLorebook": "lorebook-only",
                "apiKey": "character-only",
                "collectionNameRulebook": "rulebooks_public",
            }
        }
        config = {"typesense": {"api_key": "character-only"}}
        self.assertIsNone(rules.configure_public_key(client, config))
        self.assertNotEqual(client.typesense_key, "character-only")
        client.session.get.return_value.json.return_value = {
            "typesenseConfig": {
                "apiKeyRulebook": "rulebook-public-scoped",
                "collectionNameRulebook": "rulebooks_public",
            }
        }
        self.assertEqual(rules.configure_public_key(client, config), "rulebooks_public")
        self.assertEqual(client.typesense_key, "rulebook-public-scoped")

    def test_index_summary_is_listing_only(self):
        record, _ = rules.build_record(None, self.doc, "2026-11-01T00:00:00Z")
        s = rules.summary(record)
        self.assertEqual(s["numRules"], 12)
        self.assertEqual(s["creator"], "example")
        self.assertNotIn("rules", s)


if __name__ == "__main__":
    unittest.main()
