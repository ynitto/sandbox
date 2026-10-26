import unittest


def shipping(total):
    return 0 if total >= 5000 else 500


class TestShipping(unittest.TestCase):
    def test_free(self):
        actual = shipping(5000)
        self.assertEqual(actual, 0)

    def test_same_behavior(self):
        result = shipping(5000)
        self.assertEqual(result, 0)

    def test_below(self):
        amount = shipping(4999)
        self.assertEqual(amount, 500)
