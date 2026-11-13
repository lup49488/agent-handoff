import unittest

from stats import median, mode, percentile


class MedianTests(unittest.TestCase):
    def test_unsorted_odd(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_unsorted_even(self):
        self.assertEqual(median([4, 1, 3, 2]), 2.5)


class ModeTests(unittest.TestCase):
    def test_single_winner(self):
        self.assertEqual(mode([1, 2, 2, 3]), 2)

    def test_tie_goes_to_first_seen(self):
        self.assertEqual(mode([5, 1, 1, 5]), 5)
        self.assertEqual(mode([1, 5, 5, 1]), 1)


class PercentileTests(unittest.TestCase):
    def test_ends(self):
        self.assertEqual(percentile([10, 20, 30, 40], 0), 10)
        self.assertEqual(percentile([10, 20, 30, 40], 100), 40)

    def test_middle(self):
        self.assertEqual(percentile([10, 20, 30, 40], 50), 25)

    def test_unsorted_input(self):
        self.assertAlmostEqual(percentile([40, 10, 30, 20], 25), 17.5)


if __name__ == "__main__":
    unittest.main()
