"""Simple presentation regression checks for Archive navigation and metrics."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INNER = [
    "bots", "deleted", "lorebooks", "deleted-lorebooks", "stats",
    "name-trends", "tag-stats", "faq", "recovery", "merge", "changes", "restored",
    "activity", "explorer", "health", "search", "creator", "bot", "lorebook",
]
EXPECTED_PATHS = [
    "../", "../deleted/", "../deleted-lorebooks/", "../recovery/",
    "../merge/", "../stats/", "../name-trends/", "../tag-stats/", "../faq/",
]


class SiteReportingTests(unittest.TestCase):
    def test_inner_navigation_uses_homepage_links_consistently(self):
        for page in INNER:
            with self.subTest(page=page):
                content = (ROOT / page / "index.html").read_text(encoding="utf-8")
                self.assertIn('class="archive-unified-header"', content)
                match = re.search(
                    r'<nav class="archive-head-links"[^>]*>(.*?)</nav>',
                    content,
                    re.DOTALL,
                )
                self.assertIsNotNone(match)
                links = re.findall(r'<a\b[^>]*href="([^"]+)"', match.group(1))
                self.assertEqual(links, EXPECTED_PATHS)

    def test_homepage_verification_is_real_queue_not_arithmetic_gap(self):
        home = (ROOT / "assets" / "home.js").read_text(encoding="utf-8")
        self.assertIn("pendingVerificationBots", home)
        self.assertIn("snapshot-reconcile", home)
        self.assertIn("stat-bots-saved", home)
        page = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="snapshot-more"', page)
        self.assertEqual(page.count('<div><b id="stat-'), 6)
        self.assertNotIn("set('stat-bots-unreconciled'", home)

    def test_stats_separates_old_status_audits_from_verified_file_counts(self):
        app = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        growth = (ROOT / "assets" / "stats-live.js").read_text(encoding="utf-8")
        self.assertIn("saved bot files (last full check)", app)
        self.assertIn("search entries (some have no saved file)", app)
        self.assertIn("IDs in the creator index at that check", app)
        self.assertIn("Separate creator/status report, checked", app)
        self.assertIn("last recorded as '+esc(key)", app)
        self.assertNotIn("indexed materialized bot records", app)
        self.assertIn("Recent public-bot count change per day", growth)

    def test_bot_dates_are_reported_separately_from_archive_first_seen(self):
        app = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertIn("reportedBotTimestamp(record,['createdAt','created_at'])", app)
        self.assertIn("reportedBotTimestamp(record,['publishedAt','published_at'])", app)
        self.assertIn("Created (reported by SpicyChat)", app)
        self.assertIn("Made public (exact date)", app)
        self.assertIn("First captured by Archive", app)
        self.assertIn("not when it was published", app)
        self.assertIn("status==='public'?await publicBotCreationDate(runtime,record.id):null", app)

    def test_missing_bot_page_offers_recovery_instead_of_dead_end(self):
        app = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertIn("No detailed bot file saved", app)
        self.assertIn("Open Recovery Lab", app)
        self.assertIn("R2_READ_BUDGET_PAUSED", app)
        self.assertIn("const missing=e?.status===404", app)
        self.assertIn("Could not load this bot right now", app)

    def test_stats_show_both_newest_and_deep_discovery(self):
        app = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        live = (ROOT / "assets" / "stats-live.js").read_text(encoding="utf-8")
        self.assertIn("run?.listings?.latest?.count", app)
        self.assertIn("row?.listings?.latest?.count", live)
        self.assertIn("Newest listings scanned", app)
        self.assertIn("New from newest", app)
        self.assertIn("Deep discovery", app)
        self.assertNotIn("next normal scan", app)
        self.assertNotIn("next normal scan", live)


if __name__ == "__main__":
    unittest.main()
