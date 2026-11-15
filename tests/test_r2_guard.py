import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sca_r2_guard', ROOT / 'scripts' / 'r2_guard.py')
mod = importlib.util.module_from_spec(spec)
sys.modules['sca_r2_guard'] = mod
spec.loader.exec_module(mod)


class R2GuardTests(unittest.TestCase):
    def test_cloudflare_billing_classes(self):
        for action in ['PutObject', 'ListObjects', 'ListObjectsV2', 'CopyObject', 'UploadPart']:
            self.assertEqual(mod.classify(action), 'A')
        for action in ['GetObject', 'HeadObject', 'UsageSummary', 'GetBucketCors']:
            self.assertEqual(mod.classify(action), 'B')
        for action in ['DeleteObject', 'AbortMultipartUpload']:
            self.assertEqual(mod.classify(action), 'FREE')

    def test_unknown_reads_are_conservative_class_b(self):
        self.assertEqual(mod.classify('GetSomethingNew'), 'B')
        self.assertEqual(mod.classify('HeadSomethingNew'), 'B')

    def test_unknown_mutation_is_conservative_class_a(self):
        self.assertEqual(mod.classify('FutureWriteOperation'), 'A')

    def _guard_status_at(self, class_a, previous=None, storage_bytes=1_000_000):
        config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
        status_path = Path('/unused/test-r2-status.json')
        captured = {}
        usage = {
            'classA': class_a,
            'classB': 1000,
            'storageBytes': storage_bytes,
            'actions': {'PutObject': class_a},
            'buckets': {},
        }

        def read_json(path, default):
            if Path(path) == mod.CONFIG_PATH:
                return config
            if Path(path) == status_path:
                return previous or {}
            return default

        with patch.object(mod, 'read_json', side_effect=read_json), \
             patch.object(mod, 'write_json', side_effect=lambda path, data: captured.update(data)), \
             patch.object(mod, 'emit_output'), \
             patch.object(mod, 'query_usage', return_value=usage), \
             patch.dict(os.environ, {
                 'CLOUDFLARE_R2_ACCOUNT_ID': 'test-account',
                 'CLOUDFLARE_API_TOKEN': 'test-token',
             }), \
             patch.object(sys, 'argv', ['r2_guard.py', '--status-file', str(status_path)]):
            self.assertEqual(mod.main(), 0)
        return captured

    def test_class_a_guard_allows_usage_above_old_700k_cap(self):
        status = self._guard_status_at(800_000)
        self.assertEqual(status['selectedMode'], 'r2')
        self.assertEqual(status['limits']['classAPause'], 950_000)
        self.assertEqual(status['limits']['classAResume'], 900_000)

    def test_class_a_guard_pauses_at_950k(self):
        self.assertEqual(self._guard_status_at(949_999)['selectedMode'], 'r2')
        status = self._guard_status_at(950_000)
        self.assertEqual(status['selectedMode'], 'github')
        self.assertIn('class-a', status['reasonCodes'])

    def test_class_a_guard_resumes_below_900k_after_previous_pause(self):
        previous = {'selectedMode': 'github', 'reasonCodes': ['class-a']}
        self.assertEqual(self._guard_status_at(900_000, previous)['selectedMode'], 'github')
        self.assertEqual(self._guard_status_at(899_999, previous)['selectedMode'], 'r2')

    def test_storage_pauses_at_9_4gb_and_resumes_below_9_2gb(self):
        self.assertEqual(self._guard_status_at(300_000, storage_bytes=9_399_999_999)['selectedMode'], 'r2')
        paused = self._guard_status_at(300_000, storage_bytes=9_400_000_000)
        self.assertEqual(paused['selectedMode'], 'github')
        self.assertIn('storage', paused['reasonCodes'])
        self.assertEqual(paused['limits']['storagePauseBytes'], 9_400_000_000)
        self.assertEqual(paused['limits']['storageResumeBytes'], 9_200_000_000)
        previous = {'selectedMode': 'github', 'reasonCodes': ['storage']}
        self.assertEqual(self._guard_status_at(300_000, previous, 9_200_000_000)['selectedMode'], 'github')
        self.assertEqual(self._guard_status_at(300_000, previous, 9_199_999_999)['selectedMode'], 'r2')

    def test_r2_hard_limit_and_warning_both_exceed_normal_pause(self):
        config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
        store = config['storage']['r2']
        guard = config['storage']['quota_guard']
        self.assertEqual(store['warning_bytes'], 9_300_000_000)
        self.assertEqual(store['hard_limit_bytes'], 9_500_000_000)
        self.assertLess(guard['storage_resume_bytes'], store['warning_bytes'])
        self.assertLess(store['warning_bytes'], guard['storage_pause_bytes'])
        self.assertLess(guard['storage_pause_bytes'], store['hard_limit_bytes'])
        self.assertLess(store['hard_limit_bytes'], 10_000_000_000)
        self.assertEqual(guard['class_a_pause'], 950_000)
        self.assertEqual(guard['image_pause_storage_bytes'], 5_000_000_000)

    def test_discovery_write_limit_matches_class_a_pause(self):
        config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['storage']['r2']['discovery_write_soft_limit'], 950_000)
        self.assertEqual(config['storage']['quota_guard']['class_a_pause'], 950_000)
        self.assertEqual(config['storage']['quota_guard']['class_a_reserve_per_run'], 10_000)


if __name__ == '__main__':
    unittest.main()
