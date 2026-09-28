import importlib.util
import sys
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
