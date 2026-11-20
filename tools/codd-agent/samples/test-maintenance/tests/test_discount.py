import unittest

from src.discount import discount


class DiscountTest(unittest.TestCase):
    def test_applies_at_10000(self):
        self.assertEqual(discount(10000), 1000)

    def test_none_below_10000(self):
        self.assertEqual(discount(9999), 0)
