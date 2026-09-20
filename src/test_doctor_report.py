"""Unit tests for doctor_report.flatten_report.

Every fixture is synthetic and only exercises a code path; none of it is a
real diagnostic result.  The file is pure ASCII on purpose (Chinese sample
text is written with unicode escapes) so it survives any Windows code page.

Run from the folder that holds both files:  python -m unittest -v
"""

import copy
import json
import unittest

from doctor_report import MAX_DEPTH, MAX_NODES, flatten_report

ROW_KEYS = {"section", "depth", "label", "sn", "status", "summary", "tip"}


def row(section, depth, label, status, sn="", summary="", tip=""):
    """Expected output row."""
    return {
        "section": section,
        "depth": depth,
        "label": label,
        "sn": sn,
        "status": status,
        "summary": summary,
        "tip": tip,
    }


def chain(deepest_depth):
    """Single-branch tree whose leaf sits at ``deepest_depth`` (root is 0)."""
    node = {"id": "leaf", "status": "warn"}
    for _ in range(deepest_depth):
        node = {"id": "group", "children": [node]}
    return node


def leaves(count):
    return [{"id": "n%d" % index, "status": "skip"} for index in range(count)]


def statuses(document):
    return [item["status"] for item in flatten_report(document)]


class SectionTests(unittest.TestCase):
    def test_modern_sections_flatten_in_preorder(self):
        document = {
            "env": [
                {"id": "a", "label": "Check A", "status": "pass",
                 "summary": "fine"},
                {"id": "b", "label": "Check B", "status": "warn",
                 "tip": "retry"},
            ],
            "device": [
                {
                    "id": "dev-1",
                    "label": "Device 1",
                    "sn": "SN-001",
                    "children": [
                        {"label": "Step 1", "status": "fail",
                         "summary": "bad"},
                        {
                            "label": "Step 2",
                            "status": "skip",
                            "children": [
                                {"label": "Step 2.1", "status": "pass"},
                            ],
                        },
                    ],
                },
                {"label": "Device 2", "sn": "SN-002", "status": "skip"},
            ],
        }
        self.assertEqual(
            flatten_report(document),
            [
                row("env", 0, "Check A", "pass", summary="fine"),
                row("env", 0, "Check B", "warn", tip="retry"),
                row("device", 0, "Device 1", "group", sn="SN-001"),
                row("device", 1, "Step 1", "fail", summary="bad"),
                row("device", 1, "Step 2", "skip"),
                row("device", 2, "Step 2.1", "pass"),
                row("device", 0, "Device 2", "skip", sn="SN-002"),
            ],
        )

    def test_env_rows_come_first_regardless_of_key_order(self):
        document = {
            "device": [{"label": "D", "status": "pass"}],
            "env": [{"label": "E", "status": "pass"}],
        }
        sections = [item["section"] for item in flatten_report(document)]
        self.assertEqual(sections, ["env", "device"])

    def test_legacy_aliases_use_normalized_section_names(self):
        document = {
            "system": [{"label": "S", "status": "pass"}],
            "devices": [{"label": "D", "status": "fail"}],
        }
        self.assertEqual(
            flatten_report(document),
            [row("env", 0, "S", "pass"), row("device", 0, "D", "fail")],
        )

    def test_modern_keys_win_over_aliases_without_duplicates(self):
        document = {
            "env": [{"label": "new env", "status": "pass"}],
            "system": [{"label": "old env", "status": "fail"}],
            "device": [{"label": "new device", "status": "warn"}],
            "devices": [{"label": "old device", "status": "fail"}],
        }
        self.assertEqual(
            flatten_report(document),
            [
                row("env", 0, "new env", "pass"),
                row("device", 0, "new device", "warn"),
            ],
        )

    def test_modern_and_legacy_keys_can_be_mixed(self):
        document = {
            "env": [{"label": "E", "status": "pass"}],
            "devices": [{"label": "D", "status": "skip"}],
        }
        self.assertEqual(
            flatten_report(document),
            [row("env", 0, "E", "pass"), row("device", 0, "D", "skip")],
        )

    def test_present_modern_key_wins_even_when_empty(self):
        document = {"env": [], "system": [{"label": "old", "status": "pass"}]}
        self.assertEqual(flatten_report(document), [])

    def test_shadowed_legacy_alias_is_ignored_entirely(self):
        document = {"device": [], "devices": "not an array"}
        self.assertEqual(flatten_report(document), [])

    def test_single_section_and_unrelated_root_keys_are_accepted(self):
        document = {"version": "x", "device": [{"label": "D", "status": "pass"}]}
        self.assertEqual(flatten_report(document), [row("device", 0, "D", "pass")])

    def test_empty_sections_give_no_rows(self):
        self.assertEqual(flatten_report({"env": [], "device": []}), [])

    def test_accepts_a_document_parsed_by_json_loads(self):
        text = (
            '{"env": [{"label": "A", "status": "pass"}],'
            ' "devices": [{"id": "d", "sn": 42, "status": "skip"}]}'
        )
        self.assertEqual(
            flatten_report(json.loads(text)),
            [row("env", 0, "A", "pass"), row("device", 0, "d", "skip", sn="42")],
        )


class StatusTests(unittest.TestCase):
    def test_documented_statuses_are_kept(self):
        document = {"env": [{"status": s} for s in ("pass", "warn", "fail", "skip")]}
        self.assertEqual(statuses(document), ["pass", "warn", "fail", "skip"])

    def test_ascii_case_and_outer_whitespace_are_normalized(self):
        document = {
            "env": [{"status": " PASS "}, {"status": "Skip\n"}, {"status": "FAIL"}]
        }
        self.assertEqual(statuses(document), ["pass", "skip", "fail"])

    def test_skip_stays_skip_and_is_never_shown_as_pass(self):
        document = {
            "device": [
                {"label": "no recipe", "status": "skip"},
                {"label": "skipped group", "status": "SKIP",
                 "children": [{"status": "pass"}]},
                {"label": "group of skips",
                 "children": [{"status": "skip"}, {"status": "skip"}]},
            ]
        }
        self.assertEqual(
            statuses(document),
            ["skip", "skip", "pass", "group", "skip", "skip"],
        )

    def test_unrecognized_statuses_become_unknown(self):
        raw_values = [
            "ok", "passed", "success", "error", "group", "unknown",
            "pa\u00df",     # sharp s: must not fold to "pass"
            "s\u212aip",    # Kelvin sign: only ASCII spellings are accepted
            True, False, 0, 1, 1.5, ["pass"], {"status": "pass"},
        ]
        document = {"env": [{"status": value} for value in raw_values]}
        self.assertEqual(statuses(document), ["unknown"] * len(raw_values))

    def test_leaf_without_status_is_unknown_not_pass(self):
        document = {
            "env": [
                {"label": "a"},
                {"label": "b", "status": None},
                {"label": "c", "status": "  "},
                {"label": "d", "children": []},
                {"label": "e", "children": None},
            ]
        }
        self.assertEqual(statuses(document), ["unknown"] * 5)

    def test_parent_without_status_is_group_not_pass(self):
        for missing in ({}, {"status": None}, {"status": ""}):
            node = dict(
                missing,
                label="parent",
                children=[{"status": "pass"}, {"status": "pass"}],
            )
            with self.subTest(missing=missing):
                self.assertEqual(
                    statuses({"device": [node]}), ["group", "pass", "pass"]
                )

    def test_parent_keeps_its_own_status_and_is_not_rolled_up(self):
        document = {
            "device": [
                {"label": "p1", "status": "fail", "children": [{"status": "pass"}]},
                {"label": "p2", "status": "weird", "children": [{"status": "pass"}]},
                {"label": "p3", "children": [{"status": "fail"}]},
            ]
        }
        self.assertEqual(
            statuses(document),
            ["fail", "pass", "unknown", "pass", "group", "fail"],
        )


class TextFieldTests(unittest.TestCase):
    def test_label_falls_back_to_id(self):
        document = {
            "env": [
                {"id": "only-id", "status": "pass"},
                {"id": "an-id", "label": "A label", "status": "pass"},
                {"id": "blank-label", "label": "   ", "status": "pass"},
                {"status": "pass"},
            ]
        }
        labels = [item["label"] for item in flatten_report(document)]
        self.assertEqual(labels, ["only-id", "A label", "blank-label", ""])

    def test_scalars_are_converted_to_text(self):
        node = {"id": 7, "sn": 12345, "summary": 1.5, "tip": True, "status": "pass"}
        self.assertEqual(
            flatten_report({"device": [node]}),
            [row("device", 0, "7", "pass", sn="12345", summary="1.5", tip="true")],
        )

    def test_null_text_fields_become_empty_strings(self):
        node = {"label": None, "sn": None, "summary": None, "tip": None,
                "status": "warn"}
        self.assertEqual(flatten_report({"env": [node]}), [row("env", 0, "", "warn")])

    def test_bilingual_text_is_preserved_and_stripped(self):
        label = "\u793a\u4f8b\u68c0\u67e5 / Sample check"    # zh: "sample check"
        summary = "\u793a\u4f8b\u6458\u8981\nSample summary"  # zh: "sample summary"
        tip = "\u793a\u4f8b\u63d0\u793a"                      # zh: "sample tip"
        node = {
            "label": "  " + label + " ",
            "summary": summary + "\n",
            "tip": tip,
            "status": "fail",
        }
        self.assertEqual(
            flatten_report({"env": [node]}),
            [row("env", 0, label, "fail", summary=summary, tip=tip)],
        )

    def test_markup_is_carried_as_plain_text(self):
        node = {"label": "<b>bold</b>", "tip": "<script>alert(1)</script>",
                "status": "warn"}
        result = flatten_report({"env": [node]})[0]
        self.assertEqual(result["label"], "<b>bold</b>")
        self.assertEqual(result["tip"], "<script>alert(1)</script>")

    def test_container_in_a_text_field_is_rejected(self):
        for field in ("id", "label", "sn", "summary", "tip"):
            for bad in (["x"], {"en": "x"}):
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(ValueError):
                        flatten_report({"env": [{field: bad, "status": "pass"}]})


class ValidationTests(unittest.TestCase):
    def assert_rejected(self, document):
        with self.assertRaises(ValueError):
            flatten_report(document)

    def test_root_must_be_an_object(self):
        for bad in (None, [], [{"env": []}], "{}", 5, 1.5, True):
            with self.subTest(root=bad):
                self.assert_rejected(bad)

    def test_root_without_any_known_section_is_rejected(self):
        for bad in ({}, {"results": []}, {"ENV": []}):
            with self.subTest(root=bad):
                self.assert_rejected(bad)

    def test_sections_must_be_arrays(self):
        for key in ("env", "device", "system", "devices"):
            for bad in (None, {}, "text", 3, True):
                with self.subTest(key=key, value=bad):
                    self.assert_rejected({key: bad})

    def test_top_level_nodes_must_be_objects(self):
        for bad in ("text", 1, 1.5, True, None, []):
            with self.subTest(node=bad):
                self.assert_rejected({"env": [{"status": "pass"}, bad]})

    def test_child_scalars_are_rejected(self):
        for bad in ("text", 1, 1.5, True, None):
            with self.subTest(child=bad):
                self.assert_rejected(
                    {"device": [{"label": "p", "children": [{"status": "pass"}, bad]}]}
                )

    def test_child_arrays_are_rejected(self):
        self.assert_rejected({"device": [{"children": [[{"status": "pass"}]]}]})

    def test_children_must_be_an_array(self):
        for bad in ("text", 3, True, {"status": "pass"}):
            with self.subTest(children=bad):
                self.assert_rejected({"device": [{"children": bad}]})


class LimitTests(unittest.TestCase):
    def test_depth_limit_boundary(self):
        rows = flatten_report({"env": [chain(MAX_DEPTH)]})
        self.assertEqual([item["depth"] for item in rows], list(range(MAX_DEPTH + 1)))
        self.assertEqual(rows[-1]["status"], "warn")
        with self.assertRaises(ValueError):
            flatten_report({"env": [chain(MAX_DEPTH + 1)]})

    def test_node_limit_boundary(self):
        self.assertEqual(len(flatten_report({"env": leaves(MAX_NODES)})), MAX_NODES)
        with self.assertRaises(ValueError):
            flatten_report({"env": leaves(MAX_NODES + 1)})

    def test_node_limit_counts_all_sections_and_children(self):
        half = MAX_NODES // 2
        allowed = {
            "env": leaves(half),
            "device": [{"label": "p", "children": leaves(MAX_NODES - half - 1)}],
        }
        self.assertEqual(len(flatten_report(allowed)), MAX_NODES)
        too_many = {
            "env": leaves(half),
            "device": [{"label": "p", "children": leaves(MAX_NODES - half)}],
        }
        with self.assertRaises(ValueError):
            flatten_report(too_many)

    def test_cyclic_input_is_rejected_instead_of_looping(self):
        node = {"label": "loop"}
        node["children"] = [node]
        with self.assertRaises(ValueError):
            flatten_report({"device": [node]})

    def test_shared_subtrees_are_bounded_by_the_node_limit(self):
        node = {"status": "pass"}
        for _ in range(20):  # ~2 million logical nodes from 21 real objects
            node = {"children": [node, node]}
        with self.assertRaises(ValueError):
            flatten_report({"device": [node]})


class OutputShapeTests(unittest.TestCase):
    def test_rows_are_plain_dicts_with_exact_keys_and_types(self):
        document = {
            "env": [
                {
                    "id": 1,
                    "sn": 2,
                    "status": "pass",
                    "extra": {"ignored": True},
                    "children": [{"label": "c"}],
                }
            ]
        }
        rows = flatten_report(document)
        self.assertEqual(len(rows), 2)
        for item in rows:
            self.assertIs(type(item), dict)
            self.assertEqual(set(item), ROW_KEYS)
            self.assertIs(type(item["depth"]), int)
            for key in ROW_KEYS - {"depth"}:
                self.assertIs(type(item[key]), str)

    def test_input_is_not_modified(self):
        document = {
            "env": [{"id": "a", "label": " A ", "status": " PASS "}],
            "devices": [{"id": "d", "children": [{"id": "c", "status": "skip"}]}],
        }
        snapshot = copy.deepcopy(document)
        flatten_report(document)
        self.assertEqual(document, snapshot)


if __name__ == "__main__":
    unittest.main()
