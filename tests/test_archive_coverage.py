"""The site mirror only accepts complete evidence and never overwrites newer totals."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from sync_archive_coverage import verified_summary, save_if_newer


class CoverageMirrorTests(unittest.TestCase):
    def valid(self):
        return {
            'schemaVersion': 2, 'complete': True, 'readOnly': True,
            'generatedAt': '2026-10-23T11:03:38.478963Z',
            'materializedIndexGeneratedAt': '2026-10-23T11:03:38.478963Z',
            'observedDiscoveryIds': 1428093, 'materializedIndexIds': 473307,
            'storedBotFileIds': 473210,
            'storedFileWithoutIndex': 0, 'indexedWithoutStoredFile': 97,
            'observedWithoutStoredFile': 954883, 'listing': {'r2ListPages': 474},
        }

    def test_completed_repair_mirrors_without_extra_id_lists(self):
        result = verified_summary(self.valid())
        self.assertEqual(result['storedBotFileIds'], 473210)
        self.assertEqual(result['materializedIndexIds'], 473307)
        self.assertNotIn('observedWithoutStoredFileExamples', result)

    def test_missing_and_partial_audits_rejected(self):
        for change in [{'complete': False}, {'storedBotFileIds': None},
                       {'indexedWithoutStoredFile': 0}, {'observedDiscoveryIds': 100}]:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    verified_summary({**self.valid(), **change})

    def test_older_report_cannot_replace_newer_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'archive-coverage.json'
            current = verified_summary(self.valid())
            self.assertTrue(save_if_newer(current, dest))
            older = {**current, 'generatedAt': '2026-10-22T10:05:33Z'}
            self.assertFalse(save_if_newer(older, dest))
            self.assertEqual(json.loads(dest.read_text())['generatedAt'], current['generatedAt'])


if __name__ == '__main__':
    unittest.main()
