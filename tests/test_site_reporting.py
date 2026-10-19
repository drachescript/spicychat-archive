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
        self.assertIn("stat-bots-verification-pending", home)
        self.assertNotIn("set('stat-bots-unreconciled'", home)

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
