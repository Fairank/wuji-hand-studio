# -*- coding: utf-8 -*-
"""Unit tests for the pure configuration text helper ``parameter_file``.

Public synthetic interface under test::

    from parameter_file import parse_parameters, render_parameters

    parse_parameters(source: str) -> dict
    render_parameters(source: str, values: dict) -> str

Notes
-----
* Everything runs on in-memory strings: no filesystem, network or device access.
* Every number in this file is a synthetic placeholder chosen to exercise text
  handling.  None of them is a setting, a tuning choice or a recommendation.
* The public interface does not name an exception class, so "reject" is taken to
  mean raising ``ValueError`` or ``TypeError`` (``SyntaxError`` is also accepted
  for malformed source).  The two tuples below are the only place to adjust that.
"""

import re
import unittest

from parameter_file import parse_parameters, render_parameters

VALUE_REJECTION = (ValueError, TypeError)
SOURCE_REJECTION = (ValueError, TypeError, SyntaxError)

ALLOWED_KEYS = (
    "KP",
    "KD",
    "CURRENT_LIMIT_A",
    "PATH_SPEED_RAD_S",
    "COMMAND_SPEED_RAD_S",
    "MIN_TRANSITION_S",
    "POSE_HOLD_S",
    "OFFICIAL_ENDPOINT_HOLD_S",
    "OFFICIAL_RETURN_HOLD_S",
    "MAX_TRIAL_DURATION_S",
    "PROBE_SPEED_RAD_S",
    "COMMAND_RATE_HZ",
)
NONNEGATIVE_KEYS = ("KP", "KD", "CURRENT_LIMIT_A")
STRICTLY_POSITIVE_KEYS = tuple(key for key in ALLOWED_KEYS if key not in NONNEGATIVE_KEYS)
CURRENT_CEILING_A = 2.0  # public device ceiling, inclusive

# Synthetic edits used by several tests (placeholders, not settings).
CHANGED_GAINS_AND_CURRENT = {"KP": 12.5, "KD": 0.5, "CURRENT_LIMIT_A": 1.75}

# (key, literal as written, full-line comment, inline comment or None)
_FIXTURE_ROWS = (
    ("KP", "1.5", "比例增益（合成占位值）", "合成增益占位值"),
    ("KD", "0.0625", "微分增益（合成占位值）", None),
    ("CURRENT_LIMIT_A", "1.0", "电流上限，单位：安培", "公开的设备上限为 2 A"),
    ("PATH_SPEED_RAD_S", "0.5", "路径速度，单位：弧度/秒", "必须大于零"),
    ("COMMAND_SPEED_RAD_S", "0.75", "指令速度，单位：弧度/秒", None),
    ("MIN_TRANSITION_S", "0.25", "最短过渡时间，单位：秒", "秒"),
    ("POSE_HOLD_S", "1.5", "姿态保持时间，单位：秒", "保持姿态"),
    ("OFFICIAL_ENDPOINT_HOLD_S", "2.0", "正式终点保持时间，单位：秒", None),
    ("OFFICIAL_RETURN_HOLD_S", "2.5", "正式返回保持时间，单位：秒", "返回后保持"),
    ("MAX_TRIAL_DURATION_S", "30.0", "单次试验最长时间，单位：秒", "时间上限"),
    ("PROBE_SPEED_RAD_S", "0.125", "探测速度，单位：弧度/秒", "探测用"),
    ("COMMAND_RATE_HZ", "100", "发送频率（合成值）", "最高1000Hz"),
)

# Guard the synthetic fixture itself (not the helper under test).
if tuple(row[0] for row in _FIXTURE_ROWS) != ALLOWED_KEYS:
    raise AssertionError("fixture rows must list every public key exactly once, in order")

_FIXTURE_DOCSTRING = (
    '"""合成参数夹具：仅用于文本读写测试。',
    "",
    "所有数值均为占位符，不代表任何设置或建议。",
    '"""',
)
_FIXTURE_FOOTER = "# 文件结束"


def build_source(literal_overrides=None):
    """Assemble the synthetic text; overrides change only how a literal is spelled."""
    overrides = dict(literal_overrides or {})
    lines = list(_FIXTURE_DOCSTRING) + [""]
    for key, literal, line_comment, inline_comment in _FIXTURE_ROWS:
        assignment = f"{key} = {overrides.pop(key, literal)}"
        if inline_comment is not None:
            assignment += f"  # {inline_comment}"
        lines += [f"# {line_comment}", assignment, ""]
    if overrides:
        raise AssertionError(f"not fixture keys: {sorted(overrides)}")
    lines.append(_FIXTURE_FOOTER)
    return "\n".join(lines) + "\n"


FIXTURE_SOURCE = build_source()
FIXTURE_LITERALS = {row[0]: row[1] for row in _FIXTURE_ROWS}
FIXTURE_VALUES = {key: float(literal) for key, literal in FIXTURE_LITERALS.items()}
FIXTURE_COMMENTS = (
    tuple(line for line in _FIXTURE_DOCSTRING if line.strip('"'))  # docstring text lines
    + tuple(f"# {row[2]}" for row in _FIXTURE_ROWS)
    + tuple(f"  # {row[3]}" for row in _FIXTURE_ROWS if row[3] is not None)
    + (_FIXTURE_FOOTER,)
)

_ASSIGNMENT_RE = re.compile(
    r"^(?P<head>[A-Z][A-Z0-9_]*[ \t]*=[ \t]*)"
    r"(?P<value>[^#\n]*?)"
    r"(?P<tail>[ \t]*(?:#[^\n]*)?)$",
    re.MULTILINE,
)


def skeleton(text):
    """Mask only the value token of each assignment line; all other text stays verbatim."""
    return _ASSIGNMENT_RE.sub(lambda m: m.group("head") + "<VALUE>" + m.group("tail"), text)


def _assignment_index(lines, key):
    pattern = re.compile(rf"{re.escape(key)}[ \t]*=")
    hits = [index for index, line in enumerate(lines) if pattern.match(line)]
    if len(hits) != 1:
        raise AssertionError(f"expected exactly one assignment line for {key}, found {len(hits)}")
    return hits[0]


def assignment_line(text, key):
    """Return the single line that assigns ``key``."""
    lines = text.split("\n")
    return lines[_assignment_index(lines, key)]


def swap_assignment(source, key, replacement):
    """Swap the single assignment line of ``key``; ``None`` removes that line."""
    lines = source.split("\n")
    index = _assignment_index(lines, key)
    if replacement is None:
        del lines[index]
    else:
        lines[index] = replacement
    return "\n".join(lines)


class ParameterFileTextTests(unittest.TestCase):
    maxDiff = None

    def test_01_round_trip_with_changed_gains_and_current(self):
        parsed = parse_parameters(FIXTURE_SOURCE)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed, FIXTURE_VALUES)

        updated = dict(FIXTURE_VALUES, **CHANGED_GAINS_AND_CURRENT)
        rendered = render_parameters(FIXTURE_SOURCE, updated)

        self.assertIsInstance(rendered, str)
        self.assertEqual(parse_parameters(rendered), updated)

    def test_02_preserves_chinese_comments_including_inline(self):
        updated = dict(FIXTURE_VALUES, PROBE_SPEED_RAD_S=0.375, **CHANGED_GAINS_AND_CURRENT)

        with self.subTest(scenario="fixture comments"):
            rendered = render_parameters(FIXTURE_SOURCE, updated)
            for comment in FIXTURE_COMMENTS:
                self.assertIn(comment, rendered)
            for key, _literal, _line_comment, inline_comment in _FIXTURE_ROWS:
                if inline_comment is not None:
                    self.assertTrue(
                        assignment_line(rendered, key).endswith(f"  # {inline_comment}"),
                        msg=f"inline comment of {key} was not kept on its line",
                    )
            # Names, order, comments and spacing are verbatim; only value tokens differ.
            self.assertEqual(skeleton(rendered), skeleton(FIXTURE_SOURCE))
            self.assertEqual(parse_parameters(rendered), updated)

        with self.subTest(scenario="comments that look like assignments"):
            decoy_inline = "  # 微分增益；旧值 KD = 9.0 已废弃"
            decoy_line = "# 旧记录：KP = 99.0（这是注释，不是赋值）"
            source = (
                swap_assignment(
                    FIXTURE_SOURCE, "KD", f"KD = {FIXTURE_LITERALS['KD']}{decoy_inline}"
                )
                + decoy_line
                + "\n"
            )
            rendered = render_parameters(source, updated)
            self.assertIn(decoy_line, rendered)
            self.assertTrue(assignment_line(rendered, "KD").endswith(decoy_inline))
            self.assertEqual(skeleton(rendered), skeleton(source))
            self.assertEqual(parse_parameters(rendered), updated)

    def test_03_preserves_original_source_input(self):
        source = build_source()
        values = dict(FIXTURE_VALUES, **CHANGED_GAINS_AND_CURRENT)
        values_before = dict(values)

        rendered = render_parameters(source, values)

        self.assertIsInstance(rendered, str)
        self.assertNotEqual(rendered, source)  # the edit went into a new string
        self.assertEqual(source, FIXTURE_SOURCE)  # str is immutable; this pins the intent
        self.assertEqual(values, values_before)  # caller's dictionary was not mutated
        self.assertEqual(parse_parameters(source), FIXTURE_VALUES)  # no hidden state

        scratch = parse_parameters(source)
        scratch["KP"] = 12345.0  # mutating a returned dict must not leak back
        self.assertEqual(parse_parameters(source), FIXTURE_VALUES)

    def test_04_same_values_are_replaced_stably_and_semantically(self):
        respelled = build_source({"POSE_HOLD_S": "1.50", "MAX_TRIAL_DURATION_S": "30"})
        sources = (("fixture spelling", FIXTURE_SOURCE), ("1.50 and 30 spellings", respelled))
        for label, source in sources:
            with self.subTest(source=label):
                values = parse_parameters(source)
                self.assertEqual(values, FIXTURE_VALUES)

                once = render_parameters(source, values)
                twice = render_parameters(once, parse_parameters(once))

                # Spelling may be normalised, meaning may not change ...
                self.assertEqual(parse_parameters(once), FIXTURE_VALUES)
                self.assertEqual(skeleton(once), skeleton(source))
                # ... and a second pass with the same values is a fixed point.
                self.assertEqual(twice, once)

    def test_05_rejects_incomplete_or_extra_values(self):
        render_parameters(FIXTURE_SOURCE, dict(FIXTURE_VALUES))  # baseline is accepted

        for key in ALLOWED_KEYS:
            incomplete = dict(FIXTURE_VALUES)
            del incomplete[key]
            with self.subTest(missing=key):
                with self.assertRaises(VALUE_REJECTION):
                    render_parameters(FIXTURE_SOURCE, incomplete)

        with self.subTest(case="empty dictionary"):
            with self.assertRaises(VALUE_REJECTION):
                render_parameters(FIXTURE_SOURCE, {})

        for extra in ("UNLISTED_GAIN", "kp", "CURRENT_LIMIT"):
            with self.subTest(extra=extra):
                with self.assertRaises(VALUE_REJECTION):
                    render_parameters(FIXTURE_SOURCE, dict(FIXTURE_VALUES, **{extra: 1.0}))

        renamed = dict(FIXTURE_VALUES)
        renamed["KP_GAIN"] = renamed.pop("KP")
        with self.subTest(case="same size, one key renamed"):
            with self.assertRaises(VALUE_REJECTION):
                render_parameters(FIXTURE_SOURCE, renamed)

    def test_06_rejects_invalid_numeric_types_and_ranges(self):
        render_parameters(FIXTURE_SOURCE, dict(FIXTURE_VALUES))  # baseline is accepted

        nan, inf = float("nan"), float("inf")
        cases = []
        for key in ALLOWED_KEYS:
            cases += [(key, True), (key, False)]  # bool is not an accepted number
            cases += [(key, nan), (key, inf), (key, -inf)]  # non-finite
            cases += [(key, -0.5)]  # negative, including negative current
        for key in STRICTLY_POSITIVE_KEYS:
            cases += [(key, 0.0), (key, 0)]  # zero speed / zero duration
        for above_ceiling in (2.000001, 2.5, 20.0):
            cases += [("CURRENT_LIMIT_A", above_ceiling)]  # must raise, never clip

        for key, bad in cases:
            with self.subTest(key=key, value=repr(bad)):
                with self.assertRaises(VALUE_REJECTION):
                    render_parameters(FIXTURE_SOURCE, dict(FIXTURE_VALUES, **{key: bad}))

    def test_07_rejects_invalid_or_executable_source(self):
        parse_parameters(FIXTURE_SOURCE)  # baseline is accepted by both functions
        render_parameters(FIXTURE_SOURCE, dict(FIXTURE_VALUES))

        # Deliberately harmless snippets: they must be rejected as text, never executed.
        bad_sources = (
            ("import statement", FIXTURE_SOURCE + "import math\n"),
            ("bare call", FIXTURE_SOURCE + "abs(KP)\n"),
            ("call as value", swap_assignment(FIXTURE_SOURCE, "KP", 'KP = float("1.5")')),
            ("name as value", swap_assignment(FIXTURE_SOURCE, "KD", "KD = KP")),
            ("string as value", swap_assignment(FIXTURE_SOURCE, "KP", 'KP = "1.5"')),
            ("bool as value", swap_assignment(FIXTURE_SOURCE, "KP", "KP = True")),
            (
                "two statements on one line",
                swap_assignment(FIXTURE_SOURCE, "KP", "KP = 1.5; import math"),
            ),
            ("control flow", swap_assignment(FIXTURE_SOURCE, "KP", "if True:\n    KP = 1.5")),
            ("function definition", FIXTURE_SOURCE + "def helper():\n    return 1.0\n"),
            ("repeated key", FIXTURE_SOURCE + "KP = 3.0  # 重复赋值\n"),
            ("syntax error", swap_assignment(FIXTURE_SOURCE, "KP", "KP = = 1.5")),
        )
        for label, source in bad_sources:
            with self.subTest(function="parse_parameters", source=label):
                with self.assertRaises(SOURCE_REJECTION):
                    parse_parameters(source)
            with self.subTest(function="render_parameters", source=label):
                with self.assertRaises(SOURCE_REJECTION):
                    render_parameters(source, dict(FIXTURE_VALUES))

        # The public text states key-set rules for render_parameters only, so only it
        # is checked against a source whose key set differs from the complete list.
        key_set_sources = (
            ("unlisted key in source", FIXTURE_SOURCE + "UNLISTED_GAIN = 1.0\n"),
            (
                "listed key absent from source",
                swap_assignment(FIXTURE_SOURCE, "PROBE_SPEED_RAD_S", None),
            ),
        )
        for label, source in key_set_sources:
            with self.subTest(function="render_parameters", source=label):
                with self.assertRaises(SOURCE_REJECTION):
                    render_parameters(source, dict(FIXTURE_VALUES))

    def test_08_does_not_clip_valid_values_to_fixture_defaults(self):
        # Scaling by 8 is arbitrary: it only moves each value away from the fixture number.
        scaled_down = {key: FIXTURE_VALUES[key] / 8.0 for key in ALLOWED_KEYS}
        scaled_up = {key: FIXTURE_VALUES[key] * 8.0 for key in ALLOWED_KEYS}
        scaled_up["CURRENT_LIMIT_A"] = CURRENT_CEILING_A  # stay inside the public ceiling

        candidates = (
            ("every value below the fixture number", scaled_down),
            ("every value above the fixture number", scaled_up),
            (
                "zero gains and zero current are allowed",
                dict(FIXTURE_VALUES, KP=0.0, KD=0.0, CURRENT_LIMIT_A=0.0),
            ),
            (
                "current exactly at the public ceiling",
                dict(FIXTURE_VALUES, CURRENT_LIMIT_A=CURRENT_CEILING_A),
            ),
        )
        for label, candidate in candidates:
            with self.subTest(case=label):
                reparsed = parse_parameters(render_parameters(FIXTURE_SOURCE, candidate))
                self.assertEqual(reparsed, candidate)


if __name__ == "__main__":
    unittest.main()
