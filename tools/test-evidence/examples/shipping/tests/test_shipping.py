import unittest
from shipping import shipping


class ShippingTest(unittest.TestCase):
    def test_below(self):
        self.assertEqual(shipping(4999), 500)

    def test_free(self):
        self.assertEqual(shipping(5000), 0)
