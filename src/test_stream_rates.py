"""Tests for stream_rates.ArrivalRates. Run: python -m unittest -v test_stream_rates"""
import unittest

from stream_rates import ArrivalRates

NAN, INF = float("nan"), float("inf")


def feed(rates, times, seqs=None):
    seqs = [None] * len(times) if seqs is None else seqs
    return [rates.add(t, s) for t, s in zip(times, seqs)]


class ArrivalRatesTest(unittest.TestCase):
    def check(self, rates, now, **expected):
        self.assertEqual(rates.snapshot(now), expected)

    def test_empty_then_single_frame_has_age_but_no_rate(self):
        r = ArrivalRates()
        self.check(r, 0.5, frames=0, rate_hz=None, age_ms=None, dropped=0)
        self.assertTrue(r.add(1.0))
        self.check(r, 1.5, frames=1, rate_hz=None, age_ms=500.0, dropped=0)

    def test_steady_rate(self):
        r = ArrivalRates()
        feed(r, [0.0, 0.25, 0.5, 0.75, 1.0])  # (5 - 1) / (1.0 - 0.0) = 4 Hz
        self.check(r, 1.5, frames=5, rate_hz=4.0, age_ms=500.0, dropped=0)

    def test_window_rolls_but_frames_is_lifetime(self):
        r = ArrivalRates(window_s=2.0)
        feed(r, [0.0, 0.25, 0.5, 3.0, 4.0])  # only 3.0 and 4.0 lie in [2.0, 4.0]
        self.check(r, 4.0, frames=5, rate_hz=1.0, age_ms=0.0, dropped=0)

    def test_window_boundary_is_inclusive(self):
        r = ArrivalRates(window_s=2.0)
        feed(r, [0.0, 2.0])
        self.assertEqual(r.snapshot(2.0)["rate_hz"], 0.5)
        self.assertIsNone(r.snapshot(2.25)["rate_hz"])

    def test_old_frames_give_no_current_rate(self):
        r = ArrivalRates(window_s=2.0)
        feed(r, [0.0, 10.0])
        self.check(r, 10.0, frames=2, rate_hz=None, age_ms=0.0, dropped=0)  # not 1 / 10 s
        r = ArrivalRates(window_s=2.0)
        feed(r, [0.0, 1.5])
        self.assertIsNone(r.snapshot(3.0)["rate_hz"])  # only 1.5 is still in the window
        self.check(r, 100.0, frames=2, rate_hz=None, age_ms=98500.0, dropped=0)  # all old

    def test_zero_or_denormal_span_gives_no_rate(self):
        for times in ([1.0, 1.0], [0.0, 5e-324]):  # zero span; span too small to invert
            r = ArrivalRates()
            feed(r, times)
            self.assertIsNone(r.snapshot(1.0)["rate_hz"])

    def test_sequence_duplicates_reordering_and_gaps(self):
        r = ArrivalRates()
        flags = feed(r, [0.0, 0.25, 0.5, 0.75, 1.0], [10, 10, 9, 13, 12])
        self.assertEqual(flags, [True, False, False, True, False])
        # 10 is the baseline; 11-12 are gaps; the late 12 is ignored and changes nothing
        self.check(r, 1.0, frames=2, rate_hz=1 / 0.75, age_ms=250.0, dropped=2)

    def test_unsequenced_frames_skip_comparison(self):
        r = ArrivalRates()
        flags = feed(r, [0.0, 0.25, 0.5, 0.75], [5, None, 5, 6])
        self.assertEqual(flags, [True, True, False, True])
        self.check(r, 0.75, frames=3, rate_hz=2 / 0.75, age_ms=0.0, dropped=0)

    def test_invalid_input_raises_and_changes_nothing(self):
        r = ArrivalRates()
        r.add(1.0, 5)
        before = r.snapshot(1.0)
        for bad_now in (NAN, INF, -INF, None, "2.0", True, 10**400, 0.5):  # 0.5 is a decrease
            self.assertRaises(ValueError, r.add, bad_now, 6)
            self.assertRaises(ValueError, r.snapshot, bad_now)
        for bad_seq in (True, False, 6.0, "6"):
            self.assertRaises(ValueError, r.add, 2.0, bad_seq)
        self.assertEqual(r.snapshot(1.0), before)

    def test_time_may_not_decrease_even_after_ignored_frame(self):
        r = ArrivalRates()
        self.assertEqual(feed(r, [1.0, 2.0], [5, 5]), [True, False])
        self.assertRaises(ValueError, r.add, 1.5, 6)
        self.assertTrue(r.add(2.0, 6))  # an equal time is allowed

    def test_invalid_window(self):
        for bad in (0, -1.0, NAN, INF, None, True):
            self.assertRaises(ValueError, ArrivalRates, bad)

    def test_snapshot_does_not_mutate(self):
        r = ArrivalRates(window_s=2.0)
        feed(r, [0.0, 0.5, 1.0], [1, 2, 4])
        first = r.snapshot(1.0)
        self.assertEqual(first, dict(frames=3, rate_hz=2.0, age_ms=0.0, dropped=1))
        self.assertIsNone(r.snapshot(100.0)["rate_hz"])  # far-future look
        self.assertEqual(r.snapshot(1.0), first)         # nothing pruned or recounted
        self.assertTrue(r.add(1.5, 5))                   # add() clock was not advanced

    def test_history_is_pruned_on_add(self):
        r = ArrivalRates(window_s=1.0)
        feed(r, [i * 0.25 for i in range(400)])
        self.assertEqual(len(r._times), 5)  # white-box: only 98.75 .. 99.75 kept
        self.assertEqual(r.snapshot(99.75)["rate_hz"], 4.0)


if __name__ == "__main__":
    unittest.main()
