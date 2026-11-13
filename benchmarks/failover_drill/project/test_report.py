import unittest

from report import summary


class SummaryTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(summary([], " ms"), "n=0")

    def test_values(self):
        self.assertEqual(summary([4, 1, 3, 2, 1], " ms"), "n=5 median=2.00 mode=1 p90=3.60 ms")


if __name__ == "__main__":
    unittest.main()
