import unittest

from hl_reconciler.hypercore import bracket_points, fill_coverage, portfolio_series


class HyperCoreTests(unittest.TestCase):
    def test_portfolio_bracket(self):
        raw = [["allTime", {"accountValueHistory": [[1000, "10"], [2000, "20"], [3000, "30"]]}]]
        before, after = bracket_points(portfolio_series(raw), 2500)
        self.assertEqual(before.timestamp_ms, 2000)
        self.assertEqual(after.timestamp_ms, 3000)

    def test_fill_cap_is_flagged(self):
        fills = [{"time": i} for i in range(2000)]
        coverage = fill_coverage(fills, 0, 9999)
        self.assertTrue(coverage["response_cap_hit"])
        self.assertFalse(coverage["complete"])


if __name__ == "__main__":
    unittest.main()
