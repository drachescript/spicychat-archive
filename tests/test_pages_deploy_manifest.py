"""Prevent publishing the Archive without its linked public route folders."""
from pathlib import Path
import re
import shlex
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
ROUTES = (
    "bot", "bots", "deleted", "deleted-lorebooks", "lorebook", "lorebooks",
    "recovery", "merge", "name-trends", "tag-stats", "creator", "changes",
    "activity", "search", "explorer", "restored", "health", "stats", "faq",
    "admin",
)


class PagesManifestTests(unittest.TestCase):
    def test_all_public_route_folders_exist_and_are_deployed(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(r'^\s*cp -a (index\.html favicon\.svg .+) "\$OUT/"$', content, re.M)
        self.assertIsNotNone(match, "Pages copy command not found")
        published = set(shlex.split(match.group(1)))
        for folder in ROUTES:
            with self.subTest(folder=folder):
                self.assertTrue((ROOT / folder / "index.html").is_file(),
                                f"{folder}/index.html should be present in repo")
                self.assertIn(folder, published,
                              f"{folder}/ missing from deployed Pages artifact")
                self.assertIn(f'      - "{folder}/**"', content,
                              f"{folder}/ changes do not trigger deployment")

    def test_publish_fails_if_required_html_is_missing(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        for token in ("Verify required Pages routes before publishing",
                      "recovery/index.html", "merge/index.html",
                      "deleted-lorebooks/index.html", "name-trends/index.html",
                      "tag-stats/index.html", "Pages artifact missing required routes"):
            with self.subTest(token=token):
                self.assertIn(token, content)


if __name__ == "__main__":
    unittest.main()
