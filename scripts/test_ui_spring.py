# test_ui_spring.py
"""Standard-library unit tests for ui_spring. Run: python -m unittest -v"""

import math
import re
import unittest
from fractions import Fraction

from ui_spring import css_linear, spring_samples

# Plain decimal: optional sign, no exponent, at most 6 decimals, no trailing zeros.
NUMBER = re.compile(r"\A-?(?:0|[1-9]\d*)(?:\.\d{0,5}[1-9])?\Z")


class SpringSamplesTest(unittest.TestCase):
    def test_endpoints_length_and_types(self):
        cases = [
            (),
            (0.3, 2.0, 1.0, 3),
            (0.99, 10, 0.1, 1001),
            (Fraction(1, 2), 2, Fraction(3, 4), 5),
            (0.05, 1, 2, 17),
        ]
        for args in cases:
            with self.subTest(args=args):
                s = spring_samples(*args)
                self.assertEqual(len(s), args[3] if args else 61)
                self.assertEqual(s[0], 0.0)
                self.assertEqual(math.copysign(1.0, s[0]), 1.0)  # +0.0, not -0.0
                self.assertEqual(s[-1], 1.0)
                self.assertTrue(all(type(v) is float and math.isfinite(v) for v in s))

    def test_known_analytical_midpoint(self):
        # With duration = one damped period (wd*T = 2*pi) the midpoint is the
        # first peak, x = 1 + b, and the end is the first trough, x = 1 - b**2,
        # where b = exp(-pi*z/sqrt(1 - z**2)). Normalized midpoint = 1/(1 - b).
        zeta, freq = 0.5, 2.0
        root = math.sqrt(1.0 - zeta * zeta)
        b = math.exp(-math.pi * zeta / root)
        s = spring_samples(zeta, freq, 1.0 / (freq * root), 101)
        self.assertAlmostEqual(s[50], 1.0 / (1.0 - b), places=12)
        self.assertAlmostEqual(s[50], 1.194791, places=6)
        self.assertEqual(max(s), s[50])

    def test_matches_equivalent_phase_form(self):
        # x(t) = 1 - exp(-z*w*t) / sqrt(1 - z**2) * sin(wd*t + acos(z))
        z, f, d, n = 0.72, 3.5, 0.45, 61
        w, r, phi = 2.0 * math.pi * f, math.sqrt(1.0 - z * z), math.acos(z)

        def x(t):
            return 1.0 - math.exp(-z * w * t) / r * math.sin(w * r * t + phi)

        expected = [x(d * i / (n - 1)) / x(d) for i in range(n)]
        for got, want in zip(spring_samples(z, f, d, n), expected):
            self.assertAlmostEqual(got, want, places=12)

    def test_small_overshoot_is_preserved(self):
        s = spring_samples()  # z = 0.72: analytic overshoot is about 3.8 %
        self.assertGreater(max(s), 1.03)
        self.assertLess(max(s), 1.05)
        self.assertGreaterEqual(min(s), 0.0)

    def test_invalid_inputs(self):
        type_errors = [
            {"damping_ratio": True}, {"damping_ratio": "0.5"}, {"frequency_hz": None},
            {"duration_s": [0.45]}, {"count": True}, {"count": 61.0}, {"count": "61"},
        ]
        value_errors = [
            {"damping_ratio": 0.0}, {"damping_ratio": 1.0}, {"damping_ratio": -0.2},
            {"damping_ratio": math.nan}, {"frequency_hz": 0}, {"frequency_hz": -3.5},
            {"frequency_hz": math.inf}, {"frequency_hz": 10**400},
            {"duration_s": 0.0}, {"duration_s": -0.45}, {"duration_s": math.nan},
            {"count": 2}, {"count": 1002},
            # omega * duration overflows
            {"frequency_hz": 1e300, "duration_s": 1e300},
            # near-zero x(duration): far too short, or almost undamped at a trough
            {"frequency_hz": 1e-9, "duration_s": 1e-9},
            {"damping_ratio": 1e-12, "frequency_hz": 1.0, "duration_s": 1.0},
        ]
        for kwargs in type_errors:
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                spring_samples(**kwargs)
        for kwargs in value_errors:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                spring_samples(**kwargs)


class CssLinearTest(unittest.TestCase):
    def test_exact_formatting(self):
        self.assertEqual(
            css_linear([0.0, -0.0, -4e-7, 0.12345678, 1.0399999999, 1]),
            "linear(0 0%, 0 20%, 0 40%, 0.123457 60%, 1.04 80%, 1 100%)",
        )
        self.assertEqual(
            css_linear((0, 0.5, 1, 1.5, 2, -0.25, 1)),
            "linear(0 0%, 0.5 16.666667%, 1 33.333333%, 1.5 50%, "
            "2 66.666667%, -0.25 83.333333%, 1 100%)",
        )

    def test_default_output_is_well_formed(self):
        samples = spring_samples()
        text = css_linear(samples)
        self.assertTrue(text.startswith("linear(0 0%, "))
        self.assertTrue(text.endswith(", 1 100%)"))
        stops = text[len("linear("):-1].split(", ")
        self.assertEqual(len(stops), len(samples))
        for i, stop in enumerate(stops):
            value, percent = stop.split(" ")
            self.assertRegex(value, NUMBER)
            self.assertNotEqual(value, "-0")
            self.assertAlmostEqual(float(value), samples[i], delta=6e-7)
            self.assertEqual(percent[-1], "%")
            self.assertRegex(percent[:-1], NUMBER)
            self.assertAlmostEqual(float(percent[:-1]), 100 * i / 60, delta=6e-7)
        self.assertGreater(max(float(stop.split(" ")[0]) for stop in stops), 1.0)

    def test_invalid_samples(self):
        value_errors = [
            [0.0, 1.0], [0.5] * 1002, [0.0, math.nan, 1.0], [0.0, -math.inf, 1.0],
        ]
        type_errors = [[0.0, True, 1.0], [0.0, "0.5", 1.0], [0.0, None, 1.0], 42]
        for i, bad in enumerate(value_errors):
            with self.subTest(case=i), self.assertRaises(ValueError):
                css_linear(bad)
        for i, bad in enumerate(type_errors):
            with self.subTest(case=i), self.assertRaises(TypeError):
                css_linear(bad)


if __name__ == "__main__":
    unittest.main()
