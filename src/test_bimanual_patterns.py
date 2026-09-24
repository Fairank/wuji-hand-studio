# -*- coding: utf-8 -*-
"""unittest suite for bimanual_patterns (standard library only)."""
import copy
import itertools
import math
import unittest
from collections import OrderedDict
from fractions import Fraction

import bimanual_patterns as bp

IDS = ["pair_wave", "pair_reverse", "pair_bounce", "pair_ripple",
       "pair_alternate", "pair_bloom", "pair_piano", "pair_chase"]
KEYS = ("curl", "spread", "curl_velocity", "spread_velocity")
SIDES = ("left", "right")
OMEGA = 2.0 * math.pi / 4.0  # contract: base period 4 s


def chain(action, t, key="curl"):
    """Values along the spatial chain: left little..thumb, then right thumb..little."""
    return bp.sample(action, t, "left")[key][::-1] + bp.sample(action, t, "right")[key]


def close(xs, ys, tol=1e-9):
    return len(xs) == len(ys) and all(abs(x - y) <= tol for x, y in zip(xs, ys))


class CatalogTests(unittest.TestCase):
    def test_catalog_contract(self):
        self.assertIsInstance(bp.CATALOG, OrderedDict)
        self.assertEqual(list(bp.CATALOG), IDS)
        for entry in bp.CATALOG.values():
            self.assertEqual(set(entry), {"zh", "en", "duration_s"})
            self.assertIsInstance(entry["duration_s"], float)
            self.assertEqual(entry["duration_s"], 16.0)
            for name in (entry["zh"], entry["en"]):
                self.assertIsInstance(name, str)
                self.assertTrue(name.strip())
        self.assertEqual(bp.FINGERS, ("thumb", "index", "middle", "ring", "little"))


class RangeAndDerivativeTests(unittest.TestCase):
    def test_finite_range_and_length(self):
        times = [i / 10.0 for i in range(161)] + [0.013, 7.777, 15.999]
        for action, side, t in itertools.product(IDS, SIDES, times):
            out = bp.sample(action, t, side)
            self.assertEqual(sorted(out), sorted(KEYS))
            for key in KEYS:
                self.assertEqual(len(out[key]), 5)
                for v in out[key]:
                    self.assertIsInstance(v, float)
                    self.assertTrue(math.isfinite(v), (action, side, t, key))
            for v in out["curl"] + out["spread"]:
                self.assertTrue(0.0 <= v <= 1.0, (action, side, t, v))

    def test_exact_zero_at_and_beyond_endpoints_without_wrap(self):
        edge_times = (0, 0.0, -0.0, -3.5, -1e-12, -10 ** 400,
                      16, 16.0, 16.25, 17.0, 1e9, 10 ** 400)
        for action, side in itertools.product(IDS, SIDES):
            for t in edge_times:
                out = bp.sample(action, t, side)
                for key in KEYS:
                    self.assertEqual(out[key], [0.0] * 5, (action, side, t, key))
            inside = bp.sample(action, 1.0, side)  # 17 s is clamped, not a replay of 1 s
            self.assertGreater(max(inside["curl"] + inside["spread"]), 0.01)

    def test_velocity_matches_numerical_derivative(self):
        h = 1e-5  # sample times avoid the envelope joins (2 s, 14 s) and the clip ends
        times = (0.37, 1.1, 1.73, 2.6, 5.05, 7.9, 8.3, 11.45, 13.2, 14.35, 14.9, 15.62)
        for action, side, t in itertools.product(IDS, SIDES, times):
            mid = bp.sample(action, t, side)
            lo, hi = bp.sample(action, t - h, side), bp.sample(action, t + h, side)
            for key, f in itertools.product(("curl", "spread"), range(5)):
                numeric = (hi[key][f] - lo[key][f]) / (2.0 * h)
                self.assertAlmostEqual(numeric, mid[key + "_velocity"][f], delta=1e-6,
                                       msg=(action, side, t, key, f))


class SpatialPatternTests(unittest.TestCase):
    def test_global_index_is_thumb_inward(self):
        order = [bp.global_index(side, f) for side in SIDES for f in range(5)]
        self.assertEqual(order, [4, 3, 2, 1, 0, 5, 6, 7, 8, 9])

    def test_wave_and_reverse_travel_along_thumb_inward_chain(self):
        delay = 0.52 / OMEGA  # one chain step
        for t, key in itertools.product((3.0, 6.4, 9.7), ("curl", "spread")):
            now, later = chain("pair_wave", t, key), chain("pair_wave", t + delay, key)
            self.assertGreater(max(now) - min(now), 0.1)
            # left little -> ... -> left thumb -> right thumb -> ... -> right little
            self.assertTrue(close(now[:-1], later[1:]), (t, key))
            now, later = chain("pair_reverse", t, key), chain("pair_reverse", t + delay, key)
            self.assertTrue(close(now[1:], later[:-1]), (t, key))

    def test_ripple_moves_outward_from_thumbs(self):
        delay = 0.75 / OMEGA
        for t in (3.0, 7.5, 11.0):
            now, later = chain("pair_ripple", t), chain("pair_ripple", t + delay)
            self.assertTrue(close(now, now[::-1], 1e-12))  # mirror symmetric about thumbs
            self.assertTrue(close(now[1:5], later[0:4]))  # left hand: thumb -> little
            self.assertTrue(close(now[5:9], later[6:10]))  # right hand: thumb -> little

    def test_bounce_reverses_travel_direction(self):
        shift = 0.52 * 4.5 / OMEGA
        for key in ("curl", "spread"):
            self.assertTrue(close(chain("pair_bounce", 8.0, key),
                                  chain("pair_wave", 8.0 + shift, key)))
            self.assertTrue(close(chain("pair_bounce", 4.0, key),
                                  chain("pair_reverse", 4.0 + shift, key)))

    def test_bloom_is_same_phase_on_all_ten_fingers(self):
        for t in (0.8, 3.0, 6.0, 9.3, 15.1):
            left, right = bp.sample("pair_bloom", t, "left"), bp.sample("pair_bloom", t, "right")
            self.assertEqual(left, right)
            for key in KEYS:
                self.assertEqual(len(set(left[key])), 1, (t, key))
        self.assertAlmostEqual(bp.sample("pair_bloom", 6.0, "right")["curl"][3], 1.0, places=12)

    def test_alternate_hands_in_opposite_phase_away_from_ends(self):
        for t in (2.5, 3.3, 4.0, 5.1, 6.0, 8.8, 11.2, 13.7):
            left = bp.sample("pair_alternate", t, "left")
            right = bp.sample("pair_alternate", t, "right")
            for f in range(5):
                self.assertAlmostEqual(left["curl"][f] + right["curl"][f], 1.0, places=12)
                self.assertAlmostEqual(left["spread"][f] + right["spread"][f], 1.0, places=12)
                self.assertAlmostEqual(left["curl_velocity"][f], -right["curl_velocity"][f],
                                       places=12)
                self.assertAlmostEqual(left["curl"][f], left["curl"][0], places=12)
        self.assertAlmostEqual(bp.sample("pair_alternate", 6.0, "left")["curl"][1], 1.0, places=12)
        self.assertAlmostEqual(bp.sample("pair_alternate", 6.0, "right")["curl"][1], 0.0, places=12)

    def test_actions_are_mutually_distinct(self):
        def signature(action):
            return [v for t in (3.0, 5.5, 7.25, 10.0, 12.75) for side in SIDES
                    for key in ("curl", "spread") for v in bp.sample(action, t, side)[key]]
        sigs = {action: signature(action) for action in IDS}
        for a, b in itertools.combinations(IDS, 2):
            gap = max(abs(x - y) for x, y in zip(sigs[a], sigs[b]))
            self.assertGreater(gap, 0.05, (a, b))


class ValidationAndPurityTests(unittest.TestCase):
    def test_input_validation(self):
        for bad in (True, False, "1.0", b"1", None, [1.0], 1j):
            with self.assertRaises(TypeError):
                bp.sample("pair_wave", bad, "left")
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                bp.sample("pair_wave", bad, "left")
        for bad in ("pair_unknown", "", "PAIR_WAVE", "wave", None, 0):
            with self.assertRaises(ValueError):
                bp.sample(bad, 1.0, "left")
        for bad in ("Left", "RIGHT", " left", "both", "", None, 0, True):
            with self.assertRaises(ValueError):
                bp.sample("pair_wave", 1.0, bad)
        for bad in (-1, 5, True, 1.0, None):
            with self.assertRaises(ValueError):
                bp.global_index("left", bad)
        self.assertEqual(bp.sample("pair_wave", 3, "left"), bp.sample("pair_wave", 3.0, "left"))
        self.assertEqual(bp.sample("pair_piano", Fraction(7, 2), "right"),
                         bp.sample("pair_piano", 3.5, "right"))

    def test_deterministic_and_no_mutation(self):
        catalog_before = copy.deepcopy(bp.CATALOG)
        first = bp.sample("pair_chase", 5.25, "right")
        again = bp.sample("pair_chase", 5.25, "right")
        self.assertEqual(first, again)
        self.assertIsNot(first["curl"], again["curl"])
        first["curl"][0] = 42.0
        first["spread"].clear()
        self.assertEqual(bp.sample("pair_chase", 5.25, "right"), again)
        self.assertEqual(bp.CATALOG, catalog_before)
        self.assertEqual(list(bp.CATALOG), IDS)


if __name__ == "__main__":
    unittest.main()
