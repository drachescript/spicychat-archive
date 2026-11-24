"""Keep beginner recovery guidance navigable and separate from restoration claims."""
from pathlib import Path
import re
import unittest
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]

class GuideHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "a" and "href" in a:
            self.links.append(a["href"])

class BeginnerGuideTests(unittest.TestCase):
    def test_guide_links_resolve(self):
        page = ROOT / "guide" / "index.html"
        parser = GuideHTML()
        parser.feed(page.read_text(encoding="utf-8"))
        for link in parser.links:
            with self.subTest(link=link):
                if link.startswith("#"):
                    self.assertIn(link[1:], parser.ids)
                elif link.startswith("../"):
                    target = page.parent / link
                    self.assertTrue((target / "index.html").is_file())
    def test_guide_explains_recovery_limits_and_steps(self):
        html = (ROOT / "guide" / "index.html").read_text(encoding="utf-8")
        for term in ("cannot undelete", "conversation ID", "Load archive evidence",
                     "Use pasted conversation", "Archived Original", "Reconstructed",
                     "Copy fields", "different ID", "parsing happens locally",
                     "No technical knowledge needed"):
            self.assertIn(term, html)
        nav = (ROOT / "assets" / "site-shell.js").read_text(encoding="utf-8")
        self.assertIn("['/guide/', 'Archive for Dummies']", nav)
