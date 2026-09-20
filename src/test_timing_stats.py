# test_timing_stats.py
"""Unit tests for timing_stats.summarize_timing.

Every row below is synthetic and generated in this file; no recorded, project
or device data is used. Apart from the 1 kHz case, host timestamps are
binary-exact fractions so expected values can be compared with plain equality.

Run from the directory holding both files:
    python -m unittest -v test_timing_stats
"""

import copy
import unittest

from timing_stats import summarize_timing

RESULT_KEYS = {
    "n",
    "elapsed_s",
    "arrival_hz",
    "duplicates",
    "backwards",
    "missing",
    "max_gap_ms",
    "nonmonotonic_host",
}


def make_rows(seqs, host_times):
    """Build synthetic rows from parallel seq / host_s sequences."""
    if len(seqs) != len(host_times):
        raise ValueError("seqs and host_times must have the same length")
    return [{"seq": seq, "host_s": host_s} for seq, host_s in zip(seqs, host_times)]


class SummarizeTimingTests(unittest.TestCase):
    def test_empty_input_reports_explicit_zeros(self):
        self.assertEqual(
            summarize_timing([]),
            {
                "n": 0,
                "elapsed_s": 0.0,
                "arrival_hz": None,
                "duplicates": 0,
                "backwards": 0,
                "missing": 0,
                "max_gap_ms": None,
                "nonmonotonic_host": 0,
            },
        )

    def test_singleton_has_zero_elapsed_and_no_rate(self):
        self.assertEqual(
            summarize_timing([{"seq": 41, "host_s": 12.5}]),
            {
                "n": 1,
                "elapsed_s": 0.0,
                "arrival_hz": None,
                "duplicates": 0,
                "backwards": 0,
                "missing": 0,
                "max_gap_ms": None,
                "nonmonotonic_host": 0,
            },
        )

    def test_exact_1khz_arrivals(self):
        # 1001 arrivals spaced 1 ms apart: 1000 intervals across exactly 1.0 s.
        rows = [{"seq": 500 + i, "host_s": 10.0 + i / 1000.0} for i in range(1001)]
        result = summarize_timing(rows)

        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(result["n"], 1001)
        self.assertEqual(result["elapsed_s"], 1.0)
        # Host arrival rate only; nothing here claims a device sampling rate.
        self.assertEqual(result["arrival_hz"], 1000.0)
        self.assertEqual(result["duplicates"], 0)
        self.assertEqual(result["backwards"], 0)
        self.assertEqual(result["missing"], 0)
        self.assertEqual(result["nonmonotonic_host"], 0)
        # 0.001 s is not binary-exact, so individual gaps carry float rounding.
        self.assertAlmostEqual(result["max_gap_ms"], 1.0, places=9)

    def test_dropped_samples_are_counted_as_missing(self):
        # seq 3, 4 and 7 never arrive.
        rows = make_rows(
            [0, 1, 2, 5, 6, 8],
            [0.0, 0.25, 0.5, 1.25, 1.5, 2.0],
        )
        self.assertEqual(
            summarize_timing(rows),
            {
                "n": 6,
                "elapsed_s": 2.0,
                # 5 observed intervals over 2.0 s; not rescaled for the drops.
                "arrival_hz": 2.5,
                "duplicates": 0,
                "backwards": 0,
                "missing": 3,
                "max_gap_ms": 750.0,
                "nonmonotonic_host": 0,
            },
        )

    def test_adjacent_duplicates_are_counted_and_kept(self):
        rows = make_rows(
            [0, 1, 1, 2, 2, 2, 3],
            [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
        )
        self.assertEqual(
            summarize_timing(rows),
            {
                # Duplicate rows stay in n; nothing is de-duplicated.
                "n": 7,
                "elapsed_s": 3.0,
                "arrival_hz": 2.0,
                "duplicates": 3,
                "backwards": 0,
                "missing": 0,
                "max_gap_ms": 500.0,
                "nonmonotonic_host": 0,
            },
        )

    def test_out_of_order_seq_is_counted_without_reordering(self):
        rows = make_rows(
            [0, 1, 3, 2, 4],
            [0.0, 0.5, 1.0, 1.5, 2.0],
        )
        snapshot = copy.deepcopy(rows)
        result = summarize_timing(rows)

        # The input must come back untouched: no sorting, no repair.
        self.assertEqual(rows, snapshot)
        # Adjacent-pair accounting: 1->3 and 2->4 each skip one seq and 3->2
        # is a backwards step. The late row is not slotted back into place.
        self.assertEqual(
            result,
            {
                "n": 5,
                "elapsed_s": 2.0,
                "arrival_hz": 2.0,
                "duplicates": 0,
                "backwards": 1,
                "missing": 2,
                "max_gap_ms": 500.0,
                "nonmonotonic_host": 0,
            },
        )

    def test_decreasing_host_time_is_reported_not_repaired(self):
        # One repeated and one earlier host timestamp inside a forward run.
        stepped = make_rows(
            [0, 1, 2, 3, 4],
            [0.0, 0.5, 0.5, 0.25, 1.0],
        )
        self.assertEqual(
            summarize_timing(stepped),
            {
                "n": 5,
                "elapsed_s": 1.0,
                "arrival_hz": 4.0,
                "duplicates": 0,
                "backwards": 0,
                "missing": 0,
                "max_gap_ms": 750.0,
                "nonmonotonic_host": 2,
            },
        )

        # Host time decreasing throughout: elapsed is negative, so no rate.
        decreasing = make_rows([0, 1, 2], [2.0, 1.5, 1.0])
        self.assertEqual(
            summarize_timing(decreasing),
            {
                "n": 3,
                "elapsed_s": -1.0,
                "arrival_hz": None,
                "duplicates": 0,
                "backwards": 0,
                "missing": 0,
                "max_gap_ms": -500.0,
                "nonmonotonic_host": 2,
            },
        )

    def test_invalid_input_raises_value_error(self):
        good = {"seq": 0, "host_s": 0.0}
        bad_rows = {
            "seq is True": {"seq": True, "host_s": 0.5},
            "seq is False": {"seq": False, "host_s": 0.5},
            "seq is float": {"seq": 1.0, "host_s": 0.5},
            "seq is str": {"seq": "1", "host_s": 0.5},
            "seq is None": {"seq": None, "host_s": 0.5},
            "seq missing": {"host_s": 0.5},
            "host_s is nan": {"seq": 1, "host_s": float("nan")},
            "host_s is +inf": {"seq": 1, "host_s": float("inf")},
            "host_s is -inf": {"seq": 1, "host_s": float("-inf")},
            "host_s is None": {"seq": 1, "host_s": None},
            "host_s is str": {"seq": 1, "host_s": "0.5"},
            "host_s is bool": {"seq": 1, "host_s": True},
            "host_s int too large": {"seq": 1, "host_s": 10 ** 400},
            "host_s missing": {"seq": 1},
            "row is not a dict": (1, 0.5),
        }
        for label, bad in bad_rows.items():
            for position, rows in (("first", [bad, good]), ("later", [good, bad])):
                with self.subTest(case=label, position=position):
                    with self.assertRaises(ValueError):
                        summarize_timing(rows)

        not_lists = {"None": None, "dict": dict(good), "str": "rows"}
        for label, not_a_list in not_lists.items():
            with self.subTest(container=label):
                with self.assertRaises(ValueError):
                    summarize_timing(not_a_list)


if __name__ == "__main__":
    unittest.main()
