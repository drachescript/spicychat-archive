import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sca_storage_r2', ROOT / 'scripts' / 'storage_r2.py')
mod = importlib.util.module_from_spec(spec)
sys.modules['sca_storage_r2'] = mod
spec.loader.exec_module(mod)


class BloomTests(unittest.TestCase):
    def test_membership_and_reset(self):
        b = mod.BloomFilter(size_bytes=4096, hashes=5)
        self.assertNotIn('abc', b)
        b.add('abc')
        self.assertIn('abc', b)
        b.clear()
        self.assertNotIn('abc', b)

    def test_roundtrip_bytes(self):
        b = mod.BloomFilter(size_bytes=4096, hashes=5)
        for value in ['one', 'two', 'three']:
            b.add(value)
        restored = mod.BloomFilter(size_bytes=4096, hashes=5, data=b.to_bytes())
        for value in ['one', 'two', 'three']:
            self.assertIn(value, restored)


    def test_storage_quota_exception_exists(self):
        self.assertTrue(issubclass(mod.StorageQuotaExceeded, RuntimeError))


if __name__ == '__main__':
    unittest.main()
