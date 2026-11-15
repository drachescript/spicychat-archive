"""Repository safety regression checks for the Windows mirror operator script."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HetznerMirrorScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "ops/hetzner-mirror/mirror-r2.ps1").read_text(encoding="utf-8")
        cls.guide = (ROOT / "ops/hetzner-mirror/README.md").read_text(encoding="utf-8")

    def test_safe_one_way_copy_and_dry_run_default(self):
        script = self.source
        self.assertIn("[switch]$Apply", script)
        self.assertIn('"copy", $source, $localPath', script)
        self.assertIn('$arguments += "--dry-run"', script)
        self.assertIn('if (-not $Apply)', script)
        self.assertNotIn('"sync",', script)
        self.assertNotIn('rclone sync', script)
        self.assertNotIn('--delete-', script)
        self.assertIn('"-use-server-modtime"'.replace('"-','"--'), script)

    def test_robust_disk_lock_and_backoff(self):
        script = self.source
        self.assertIn("MinimumFreeGB = 12", script)
        self.assertIn('[System.IO.DriveInfo]::new($root)', script)
        self.assertIn('[System.IO.FileShare]::None', script)
        self.assertIn('"--retries", "5"', script)
        self.assertIn('"--tpslimit", "30"', script)
        self.assertIn('"--log-file", $log', script)
        self.assertIn('last-run.json', script)
        self.assertIn('exit $exitCode', script)

    def test_guide_requires_readonly_credentials_and_no_deletions(self):
        guide = self.guide.lower()
        self.assertIn('read-only', guide)
        self.assertIn('do not delete objects', guide)
        self.assertIn('task scheduler', guide)
        self.assertIn('postgresql', guide)


if __name__ == "__main__":
    unittest.main()
