import unittest

from r2_archive_optimized import _typesense_page_has_more


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


if __name__ == '__main__':
    unittest.main()
