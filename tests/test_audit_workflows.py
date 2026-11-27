"""Make sure the read-only ID audit has only one scheduled workflow.

The old 'Audit materialized Archive bot IDs' workflow used a removed CLI flag
and failed every Sunday. The full inventory is already scheduled on Monday.
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = ROOT / "scripts" / "audit_discovery_ids.py"


class AuditWorkflowTests(unittest.TestCase):
    def test_exactly_one_scheduled_inventory_and_guarded_explicit_followup(self):
        scheduled = []
        invocations = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            source = path.read_text(encoding="utf-8")
            if "python scripts/audit_discovery_ids.py" not in source:
                continue
            invocations.append((path.name, source))
            self.assertNotIn("--max-head-checks", source, path.name)
            self.assertIn("--max-list-pages", source, path.name)
            if re.search(r"(?m)^  schedule:\s*$", source):
                scheduled.append(path.name)
        self.assertEqual([name for name, _ in invocations], ["archive-id-audit.yml", "post-catalog-coverage.yml"])
        followup = dict(invocations)["post-catalog-coverage.yml"]
        self.assertIn("github.event.workflow_run.id == 45859255626", followup)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", followup)
        self.assertIn("paths: [\".github/coverage-sweep-retry.txt\"]", followup)
        self.assertIn("Verify preceding catalog actually crawled", followup)
        self.assertIn("Fresh quota check", followup)
        self.assertIn("steps.crawl.outcome == 'success'", followup)
        self.assertIn("group: spicychat-archive-writer", followup)
        self.assertIn("max-parallel: 1", followup)
        self.assertEqual(scheduled, ["archive-id-audit.yml"])

    def test_workflow_flags_match_script_help(self):
        output = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout
        self.assertIn("--max-list-pages", output)
        self.assertNotIn("--max-head-checks", output)

    def test_audit_is_read_only_for_character_records(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("delete_objects(", source)
        self.assertNotIn("save_bot(", source)
        self.assertNotIn("mark_deleted(", source)


if __name__ == "__main__":
    unittest.main()
