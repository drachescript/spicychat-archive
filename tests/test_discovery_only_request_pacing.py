"""Regression for accidental use of discovery-only variables in ranking scans."""
import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import archive as legacy
import r2_archive_optimized as optimized


class DiscoveryOnlyDelayTests(unittest.TestCase):
    def test_fast_ranking_listing_runs_without_discovery_local_variables(self):
        store = Mock()
        client = Mock()
        client.multi_search.return_value = legacy.HTTPResult(
            True, 200, data={"results": [{"hits": [], "found": 0}]}
        )
        config = {"crawler": {"listing_page_size": 250, "listing_max_hits": 250}}
        # Replace all real storage/API activity; exercise the installed listing
        # function itself instead of merely searching source text.
        with patch.object(optimized, "_original_configure_cloud",
                          return_value=({}, set())), \
             patch.object(optimized, "FingerprintIndex") as fingerprints, \
             patch.object(legacy, "typesense_search", return_value={
                 "page": 1, "per_page": 250, "sort_by": "createdAt:desc"
             }), \
             patch.object(legacy, "scan_listing"), \
             patch.object(legacy, "explore_more"), \
             patch.object(optimized.time, "sleep") as sleep:
            optimized.optimized_configure_cloud(config, store)
            result = legacy.scan_listing(
                client, config, "latest", "createdAt:desc",
                "2026-11-07T00:00:00Z", {}
            )
            self.assertEqual(result["ok"], True)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["new"], 0)
            client.multi_search.assert_called_once()
            store.flush_discovery_journal.assert_called_once()
            fingerprints.return_value.save_if_dirty.assert_called_once()
            sleep.assert_not_called()

    def test_manual_delay_is_defined_and_used_only_inside_deep_sweep(self):
        tree = ast.parse(
            (SCRIPTS / "r2_archive_optimized.py").read_text(encoding="utf-8")
        )
        scope = next(x for x in tree.body
                     if isinstance(x, ast.FunctionDef)
                     and x.name == "optimized_configure_cloud")
        listing = next(x for x in scope.body if isinstance(x, ast.FunctionDef)
                       and x.name == "fast_scan_listing")
        explore = next(x for x in scope.body if isinstance(x, ast.FunctionDef)
                       and x.name == "fast_explore_more")
        listing_names = {x.id for x in ast.walk(listing) if isinstance(x, ast.Name)}
        explore_names = {x.id for x in ast.walk(explore) if isinstance(x, ast.Name)}
        self.assertNotIn("request_pause", listing_names)
        self.assertNotIn("completed_pages", listing_names)
        self.assertIn("request_pause", explore_names)
        self.assertIn("completed_pages", explore_names)
        self.assertTrue(any(isinstance(x, ast.Name) and x.id == "request_pause"
                            and isinstance(x.ctx, ast.Store)
                            for x in ast.walk(explore)))


if __name__ == "__main__":
    unittest.main()
