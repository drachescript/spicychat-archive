import unittest

from r2_archive_optimized import _discovery_page_budget, _typesense_page_has_more


class TypesensePagingTests(unittest.TestCase):
    def test_partial_page_with_reported_rows_remaining_is_not_natural_end(self):
        self.assertTrue(
            _typesense_page_has_more(
                1_392_525, page=169, per_page=250, received=188
            )
        )

    def test_true_final_partial_page_is_natural_end(self):
        self.assertFalse(
            _typesense_page_has_more(
                42_188, page=169, per_page=250, received=188
            )
        )

    def test_empty_page_before_reported_end_still_has_more(self):
        self.assertTrue(
            _typesense_page_has_more(
                1_392_525, page=170, per_page=250, received=0
            )
        )


class DiscoveryPageBudgetTests(unittest.TestCase):
    def test_manual_selection_is_not_increased_by_adaptive_state(self):
        for selected in (50, 100, 250, 500, 750, 1000):
            with self.subTest(selected=selected):
                self.assertEqual(
                    _discovery_page_budget(selected, 1000, 1000, 1000, manual_discovery=True),
                    selected,
                )

    def test_manual_selection_still_obeys_quota_guard(self):
        self.assertEqual(
            _discovery_page_budget(500, 1000, 30, 1000, manual_discovery=True),
            30,
        )

    def test_scheduled_runs_retain_adaptive_budget(self):
        self.assertEqual(_discovery_page_budget(500, 750, 900, 1000), 750)
        self.assertEqual(_discovery_page_budget(500, 1000, 700, 1000), 700)


if __name__ == '__main__':
    unittest.main()
