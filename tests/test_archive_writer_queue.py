"""Regression guard: archive writers must queue instead of replacing pending runs.

GitHub's default concurrency queue keeps only one pending run, so a scheduled
Rulebook job can otherwise cancel a waiting normal bot run during a long catalog
sweep, even though cancel-in-progress is false.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

WRITERS = {
    "archive.yml",
    "archive-id-audit.yml",
    "lorebooks.yml",
    "rulebooks.yml",
    "name-trends.yml",
    "migrate-r2.yml",
    "prune-rulebook-listing-only.yml",
    "repair-search-index.yml",
}


class SharedWriterQueueTests(unittest.TestCase):
    def test_every_shared_writer_uses_multi_pending_fifo_queue(self):
        for filename in sorted(WRITERS):
            with self.subTest(workflow=filename):
                source = (WORKFLOWS / filename).read_text(encoding="utf-8")
                block = re.search(r"(?m)^concurrency:\s*\n((?:[ \t]+[^\n]*\n)+)", source)
                self.assertIsNotNone(block, "Shared writer must retain a concurrency block")
                settings = block.group(1)
                self.assertRegex(settings, r"(?m)^  group: spicychat-archive-writer\s*$")
                self.assertRegex(settings, r"(?m)^  cancel-in-progress: false\s*$")
                self.assertRegex(settings, r"(?m)^  queue: max\s*$")

    def test_all_writers_of_shared_r2_bucket_are_covered(self):
        for path in WORKFLOWS.glob("*.yml"):
            source = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^  group: spicychat-archive-writer\s*$", source):
                self.assertIn(path.name, WRITERS, "New writer must be added to queue regression test")

    def test_pages_still_cancels_stale_deployments_not_writers(self):
        source = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
        self.assertIn("group: pages", source)
        self.assertIn("cancel-in-progress: true", source)


if __name__ == "__main__":
    unittest.main()
