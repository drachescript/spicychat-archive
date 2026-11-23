"""Every R2 writer must share the multi-pending FIFO queue.

Detect new archive-writing workflows automatically. A manually maintained
allowlist previously broke the bot crawler when theme/preset publishers were
added, even though those jobs already had the correct queue policy.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def is_r2_writer(source: str) -> bool:
    """Detect R2-credentialed workflows which execute Archive Python scripts.

    Worker deployments count only when they also rebuild R2 indexes.
    """
    return ("CLOUDFLARE_R2_ACCESS_KEY_ID" in source
            and bool(re.search(r"python\s+scripts/[a-zA-Z0-9_.-]+\.py\b", source)))


def has_fifo_lock(source: str) -> bool:
    return all(re.search(r"(?m)^\s*" + re.escape(line) + r"\s*$", source)
               for line in ("group: spicychat-archive-writer",
                            "cancel-in-progress: false", "queue: max"))


class SharedWriterQueueTests(unittest.TestCase):
    def test_every_r2_writer_uses_multi_pending_fifo_queue(self):
        found = set()
        for path in sorted(WORKFLOWS.glob("*.yml")):
            source = path.read_text(encoding="utf-8")
            if not is_r2_writer(source):
                continue
            found.add(path.name)
            with self.subTest(workflow=path.name):
                self.assertTrue(has_fifo_lock(source),
                                "Every R2 writer needs the shared FIFO queue")
        for required in ("archive.yml", "rulebooks.yml", "lorebooks.yml",
                         "block-presets.yml", "community-tags.yml",
                         "deploy-import-worker.yml", "rebuild-derived-indexes.yml"):
            self.assertIn(required, found)

    def test_queue_members_are_archive_writers(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            source = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^\s*group: spicychat-archive-writer\s*$", source):
                with self.subTest(workflow=path.name):
                    self.assertTrue(is_r2_writer(source),
                                    "Don't hold R2 writer lock for non-writers")

    def test_worker_deployment_does_not_wait_behind_r2_writers(self):
        source = (WORKFLOWS / "deploy-import-worker.yml").read_text(encoding="utf-8")
        self.assertRegex(source, r"(?m)^  deploy:\s*$")
        self.assertRegex(source, r"(?m)^  rebuild-if-needed:\s*$")
        self.assertRegex(source, r"(?m)^    needs: deploy\s*$")
        self.assertRegex(source, r"(?m)^    concurrency:\s*$")
        self.assertNotRegex(source, r"(?m)^concurrency:\s*$")

    def test_archive_crawler_tests_run_before_r2_work(self):
        source = (WORKFLOWS / "archive.yml").read_text(encoding="utf-8")
        test = source.find("name: Test archive code before crawler")
        guard = source.find("name: Check R2 free-tier guard")
        crawl = source.find("name: Update archive in R2")
        publish = source.find("name: Publish archive run feed")
        commit = source.find("name: Commit public stats/runtime changes")
        self.assertTrue(0 <= test < guard < crawl < publish < commit,
                        "Test regressions before crawling; publish the run feed after the crawl")

    def test_pages_still_cancels_stale_deployments_not_writers(self):
        source = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
        self.assertIn("group: pages", source)
        self.assertIn("cancel-in-progress: true", source)


if __name__ == "__main__":
    unittest.main()
