"""Unit tests for interval_summary.summarize_intervals (stdlib unittest only).

Expected values use binary-exact durations (0.5, 0.25, whole seconds, ...) so
results can be compared with assertEqual rather than tolerances.
"""

import math
import sys
import unittest
from decimal import Decimal
from fractions import Fraction

from interval_summary import summarize_intervals

KEYS = ["count", "mean_ms", "p50_ms", "p95_ms", "max_ms"]


def summary(count, mean_ms, p50_ms, p95_ms, max_ms):
    """Build the expected result dict."""
    return {
        "count": count,
        "mean_ms": mean_ms,
        "p50_ms": p50_ms,
        "p95_ms": p95_ms,
        "max_ms": max_ms,
    }


class SummarizeIntervalsTests(unittest.TestCase):
    def assert_rejected(self, values):
        with self.assertRaises(ValueError):
            summarize_intervals(values)

    def assert_all_rejected(self, cases):
        # Labels (not raw values) go into subTest so failure reports never
        # need to repr() generators or enormous integers.
        for label, values in cases.items():
            with self.subTest(case=label):
                self.assert_rejected(values)

    # ---------------------------------------------------------------- empty
    def test_empty_list_and_tuple(self):
        for container in ([], ()):
            with self.subTest(container=type(container).__name__):
                result = summarize_intervals(container)
                self.assertEqual(result, summary(0, None, None, None, None))
                self.assertEqual(list(result), KEYS)

    # ------------------------------------------------------------ singleton
    def test_singleton(self):
        self.assertEqual(
            summarize_intervals([0.25]),
            summary(1, 250.0, 250.0, 250.0, 250.0),
        )

        result = summarize_intervals((3,))
        self.assertEqual(result, summary(1, 3000.0, 3000.0, 3000.0, 3000.0))
        self.assertIs(type(result["count"]), int)
        for key in KEYS[1:]:
            self.assertIs(type(result[key]), float)

    # ------------------------------------------------------------- unsorted
    def test_unsorted_samples(self):
        result = summarize_intervals([0.5, 0.125, 2.0, 0.25, 1.0])
        # sorted ms: 125, 250, 500, 1000, 2000 -> p50 index 2, p95 index 4
        self.assertEqual(result, summary(5, 775.0, 500.0, 2000.0, 2000.0))
        self.assertEqual(list(result), KEYS)

    def test_unsorted_mixed_int_and_float_tuple(self):
        result = summarize_intervals((2, 0.5, 1))
        self.assertEqual(result["count"], 3)
        self.assertAlmostEqual(result["mean_ms"], 3500.0 / 3.0)
        self.assertEqual(result["p50_ms"], 1000.0)
        self.assertEqual(result["p95_ms"], 2000.0)
        self.assertEqual(result["max_ms"], 2000.0)

    # ------------------------------------------------- duplicates and zeros
    def test_duplicates_and_zeros(self):
        result = summarize_intervals([0, 1.5, 0.0, 1.5, 0, 1.5])
        # sorted ms: 0, 0, 0, 1500, 1500, 1500 -> p50 index 2, p95 index 5
        self.assertEqual(result, summary(6, 750.0, 0.0, 1500.0, 1500.0))

        self.assertEqual(
            summarize_intervals([0, 0.0, 0]),
            summary(3, 0.0, 0.0, 0.0, 0.0),
        )
        self.assertEqual(
            summarize_intervals([2, 2.0, 2, 2.0]),
            summary(4, 2000.0, 2000.0, 2000.0, 2000.0),
        )

    def test_negative_zero_is_treated_as_zero(self):
        result = summarize_intervals([-0.0, 0.0])
        self.assertEqual(result, summary(2, 0.0, 0.0, 0.0, 0.0))
        for key in KEYS[1:]:
            self.assertEqual(math.copysign(1.0, result[key]), 1.0)

    # ------------------------------------------------ percentile convention
    def test_exactly_twenty_values_pin_nearest_rank_convention(self):
        values = [
            1, 8, 15, 2, 9, 16, 3, 10, 17, 4,
            11, 18, 5, 12, 19, 6, 13, 20, 7, 14,
        ]
        self.assertEqual(sorted(values), list(range(1, 21)))  # fixture sanity
        self.assertNotEqual(values, sorted(values))

        result = summarize_intervals(values)
        # n = 20: p50 -> index ceil(10) - 1 = 9  (10 s)
        #         p95 -> index ceil(19) - 1 = 18 (19 s)
        self.assertEqual(
            result, summary(20, 10500.0, 10000.0, 19000.0, 20000.0)
        )

        # Other conventions give different answers on this data set:
        self.assertNotEqual(result["p50_ms"], 11000.0)  # sorted[int(p * n)]
        self.assertNotEqual(result["p50_ms"], 10500.0)  # interpolated median
        self.assertNotEqual(result["p95_ms"], 20000.0)  # sorted[int(p * n)]
        self.assertNotEqual(result["p95_ms"], 19050.0)  # linear interpolation

    def test_nearest_rank_for_neighbouring_sizes(self):
        cases = [
            # (values, expected p50_ms, expected p95_ms)
            ([4, 2], 2000.0, 4000.0),  # indices ceil(1.0)-1=0, ceil(1.9)-1=1
            ([3, 1, 2], 2000.0, 3000.0),  # indices ceil(1.5)-1=1, ceil(2.85)-1=2
            (list(range(19, 0, -1)), 10000.0, 19000.0),  # indices 9 and 18
            (list(range(21, 0, -1)), 11000.0, 20000.0),  # indices 10 and 19
        ]
        for values, p50_ms, p95_ms in cases:
            with self.subTest(n=len(values)):
                result = summarize_intervals(values)
                self.assertEqual(result["p50_ms"], p50_ms)
                self.assertEqual(result["p95_ms"], p95_ms)

    # -------------------------------------------------------- invalid types
    def test_invalid_container_types(self):
        self.assert_all_rejected(
            {
                "None": None,
                "str": "1.0",
                "bytes": b"\x01\x02",
                "int": 5,
                "float": 2.5,
                "set": {1.0, 2.0},
                "frozenset": frozenset([1.0]),
                "dict": {"a": 1.0},
                "range": range(3),
                "iterator": iter([1.0, 2.0]),
                "generator": (x for x in [1.0, 2.0]),
            }
        )

    def test_invalid_element_types(self):
        self.assert_all_rejected(
            {
                "str": ["1.0"],
                "None": [None],
                "nested list": [[1.0, 2.0]],
                "nested tuple": ((1.0,),),
                "complex": [complex(1.0, 0.0)],
                "Decimal": [Decimal("1.5")],
                "Fraction": [Fraction(1, 2)],
                "bytes": [b"1"],
                "dict": [{}],
                "valid then invalid": [1.0, 2, "3"],
            }
        )

    def test_bool_rejected(self):
        self.assert_all_rejected(
            {
                "True": [True],
                "False": [False],
                "mixed list": [1.0, True],
                "mixed tuple": (False, 2),
            }
        )

    # ------------------------------------- non-finite, negative, overflow
    def test_nonfinite_rejected(self):
        self.assert_all_rejected(
            {
                "nan": [float("nan")],
                "inf": [float("inf")],
                "-inf": [float("-inf")],
                "nan after valid": [1.0, 2.0, math.nan],
                "inf in tuple": (0.5, math.inf),
            }
        )

    def test_negative_rejected(self):
        self.assert_all_rejected(
            {
                "negative int": [-1],
                "negative float": [-0.5],
                "tiny negative": [1, 2, -1e-300],
                "negative subnormal": [-5e-324],
            }
        )

    def test_enormous_integer_rejected_with_value_error(self):
        # float(10**400) raises OverflowError; it must surface as ValueError.
        self.assert_all_rejected(
            {
                "10**400": [10 ** 400],
                "valid then 10**400": [1, 2.0, 10 ** 400],
                "10**400 in tuple": (10 ** 400,),
                "-10**400": [-(10 ** 400)],
            }
        )
        # Beyond the int->str digit limit the message must still be ours.
        with self.assertRaisesRegex(ValueError, "too large"):
            summarize_intervals([10 ** 5000])

    def test_float_overflow_rejected_with_value_error(self):
        self.assert_all_rejected(
            {
                # finite in seconds, but * 1000 overflows to inf
                "ms scaling overflow (max float)": [sys.float_info.max],
                "ms scaling overflow (1e306)": [0.0, 1e306],
                # each sample is finite in ms (~1e308) but their sum is not
                "sum overflow": [1e305, 1e305],
            }
        )

    def test_large_finite_values_are_still_summarised(self):
        expected = 1e300 * 1000.0
        self.assertTrue(math.isfinite(expected))
        self.assertEqual(
            summarize_intervals([1e300, 1e300]),
            summary(2, expected, expected, expected, expected),
        )

    def test_mean_stays_within_sample_range(self):
        # Rounding in sum / count must never push the mean outside [min, max].
        for seconds in (0.1, 0.7, 1.1, 3.3, 1e-3, 123.456):
            with self.subTest(seconds=seconds):
                result = summarize_intervals([seconds] * 3)
                self.assertEqual(result["mean_ms"], result["max_ms"])

    # ------------------------------------------------------ unchanged input
    def test_input_is_not_sorted_or_mutated(self):
        samples = [3.0, 0.5, 2, 0.5, 0]
        before = list(samples)

        first = summarize_intervals(samples)
        second = summarize_intervals(samples)

        self.assertEqual(samples, before)  # same order, same length
        self.assertTrue(all(a is b for a, b in zip(samples, before)))
        self.assertEqual(first, summary(5, 1200.0, 500.0, 3000.0, 3000.0))
        self.assertEqual(first, second)  # repeatable ...
        self.assertIsNot(first, second)  # ... with no shared state

        frozen = tuple(before)
        self.assertEqual(summarize_intervals(frozen), first)
        self.assertEqual(frozen, tuple(before))

    def test_rejected_input_is_left_unchanged(self):
        bad = [2.0, -1.0, 1.0]
        self.assert_rejected(bad)
        self.assertEqual(bad, [2.0, -1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
