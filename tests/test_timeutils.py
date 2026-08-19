import unittest

from hl_reconciler.timeutils import parse_local_cutoff, to_millis


class TimeUtilsTests(unittest.TestCase):
    def test_regina_cutoff(self):
        dt = parse_local_cutoff("2026-06-01T00:00:00", "America/Regina")
        self.assertEqual(dt.isoformat(), "2026-06-01T06:00:00+00:00")
        self.assertEqual(to_millis(dt), 1780293600000)


if __name__ == "__main__":
    unittest.main()
