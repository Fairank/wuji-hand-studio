"""Tests for mapping_library.py. Run: python -m unittest -v test_mapping_library

The expected defaults, ranges and limits are restated from the specification.
They are not imported from the implementation.
"""

import copy
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import mapping_library
from mapping_library import MappingLibrary

JOINTS = 20
FOUR_MIB = 4 * 1024 * 1024
FILE_NAME = "mappings.json"
DEFAULTS = {"gain": [1.0] * 20, "offset_deg": [0.0] * 20, "smoothing_ms": 0.0}
BASE = {
    "generation": "hand1",
    "side": "left",
    "glove_serial": "GLV-0001",
    "sdk_user": "alice_01",
    "hand_serial": "HND.0001",
}


def bind(**changes):
    result = dict(BASE)
    result.update(changes)
    return result


def cfg(gain=1.0, offset=0.0, smoothing=0.0):
    return {"gain": [gain] * JOINTS, "offset_deg": [offset] * JOINTS, "smoothing_ms": smoothing}


class LibraryTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.path = os.path.join(self.dir, FILE_NAME)
        self.lib = MappingLibrary(self.path)

    def read_bytes(self):
        with open(self.path, "rb") as handle:
            return handle.read()

    def write_bytes(self, data):
        with open(self.path, "wb") as handle:
            handle.write(data)

    def assert_only_library_file(self):
        self.assertEqual(os.listdir(self.dir), [FILE_NAME])


class BindingTests(LibraryTestCase):
    def test_each_binding_dimension_is_isolated(self):
        variants = [
            BASE,
            bind(generation="hand2"),
            bind(side="right"),
            bind(sdk_user="bob_02"),
            bind(glove_serial="GLV-0002"),
            bind(hand_serial="HND.0002"),
            bind(glove_serial="", sdk_user="", hand_serial=""),
        ]
        for index, variant in enumerate(variants):
            self.lib.save(variant, cfg(smoothing=10.0 * index))
        reader = MappingLibrary(self.path)
        for index, variant in enumerate(variants):
            snap = reader.snapshot(variant)
            self.assertEqual((snap["saved"], snap["revision"]), (True, 1))
            self.assertEqual(snap["settings"]["smoothing_ms"], 10.0 * index)
        self.assertEqual(len(reader.list_entries()), len(variants))
        self.assertFalse(reader.snapshot(bind(generation="hand2", side="right"))["saved"])

    def test_values_moved_between_fields_do_not_collide(self):
        variants = [
            bind(glove_serial="A.B", hand_serial="C"),
            bind(glove_serial="A", hand_serial="B.C"),
            bind(glove_serial="C", hand_serial="A.B"),
            bind(sdk_user="A.B", glove_serial="C", hand_serial=""),
        ]
        for index, variant in enumerate(variants):
            self.lib.save(variant, cfg(gain=0.25 * (index + 1)))
        for index, variant in enumerate(variants):
            self.assertEqual(
                self.lib.snapshot(variant)["settings"]["gain"], [0.25 * (index + 1)] * JOINTS
            )
        self.assertEqual(len({mapping_library.binding_key(v) for v in variants}), len(variants))

    def test_key_is_stable_sha256_and_ignores_field_order(self):
        reordered = {field: BASE[field] for field in reversed(list(BASE))}
        self.lib.save(BASE, cfg(smoothing=5.0))
        snap = MappingLibrary(self.path).save(reordered, cfg(smoothing=6.0), expected_revision=1)
        self.assertEqual(snap["revision"], 2)
        (key,) = json.loads(self.read_bytes())["entries"]
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertEqual(key, mapping_library.binding_key(reordered))

    def test_invalid_bindings_are_rejected_without_touching_disk(self):
        invalid = [
            bind(generation="hand3"), bind(generation="HAND1"), bind(side="Left"),
            bind(side="both"), bind(glove_serial="has space"), bind(glove_serial="a/b"),
            bind(sdk_user="user@host"), bind(sdk_user="line\n"), bind(hand_serial="caf\u00e9"),
            bind(hand_serial="x" * 101), bind(glove_serial="192.168.1.20"), bind(sdk_user=7),
            bind(hand_serial=None), bind(side=True), dict(BASE, host="10.0.0.2"),
            dict(BASE, token="secret"), {k: v for k, v in BASE.items() if k != "sdk_user"},
            ["hand1", "left", "", "", ""], None,
        ]
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    self.lib.snapshot(candidate)
                with self.assertRaises(ValueError):
                    self.lib.save(candidate, cfg())
        self.assertEqual(os.listdir(self.dir), [])

    def test_identifier_boundaries_are_accepted(self):
        edge = bind(glove_serial="", sdk_user="U" * 100, hand_serial="a_b.C-9")
        self.assertEqual(self.lib.save(edge, cfg())["binding"], edge)
        self.assertTrue(self.lib.snapshot(edge)["saved"])


class SettingsTests(LibraryTestCase):
    def test_invalid_settings_are_rejected(self):
        def with_item(field, index, value):
            settings = cfg()
            settings[field][index] = value
            return settings

        invalid = {
            "NaN gain": with_item("gain", 3, float("nan")),
            "inf gain": with_item("gain", 0, float("inf")),
            "-inf offset": with_item("offset_deg", 19, float("-inf")),
            "bool gain": with_item("gain", 5, True),
            "bool offset": with_item("offset_deg", 0, False),
            "string gain": with_item("gain", 1, "1.0"),
            "None offset": with_item("offset_deg", 2, None),
            "gain above 2": with_item("gain", 4, 2.0001),
            "gain below 0": with_item("gain", 4, -0.001),
            "offset above 180": with_item("offset_deg", 7, 180.01),
            "offset below -180": with_item("offset_deg", 7, -180.01),
            "smoothing above 1000": cfg(smoothing=1000.5),
            "smoothing negative": cfg(smoothing=-1.0),
            "smoothing NaN": cfg(smoothing=float("nan")),
            "smoothing bool": cfg(smoothing=True),
            "smoothing huge int": cfg(smoothing=10**400),
            "gain has 19 values": dict(cfg(), gain=[1.0] * 19),
            "offset has 21 values": dict(cfg(), offset_deg=[0.0] * 21),
            "gain is a scalar": dict(cfg(), gain=1.0),
            "missing key": {"gain": [1.0] * 20, "offset_deg": [0.0] * 20},
            "extra key": dict(cfg(), deadband=0.0),
            "not a mapping": [[1.0] * 20, [0.0] * 20, 0.0],
        }
        for label, candidate in invalid.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    self.lib.save(BASE, candidate)
                with self.assertRaises(ValueError):
                    self.lib.save_preset("Preset", candidate)
        self.assertEqual(os.listdir(self.dir), [])

    def test_inclusive_bounds_and_integers_are_accepted_as_floats(self):
        edge = {
            "gain": [0] * 10 + [2.0] * 10,
            "offset_deg": [-180] * 10 + [180.0] * 10,
            "smoothing_ms": 1000,
        }
        stored = self.lib.save(BASE, edge)["settings"]
        self.assertEqual(
            stored,
            {
                "gain": [0.0] * 10 + [2.0] * 10,
                "offset_deg": [-180.0] * 10 + [180.0] * 10,
                "smoothing_ms": 1000.0,
            },
        )
        self.assertTrue(all(type(v) is float for v in stored["gain"] + stored["offset_deg"]))
        self.assertIs(type(stored["smoothing_ms"]), float)


class DefaultsAndCopyTests(LibraryTestCase):
    def test_unsaved_binding_gets_defaults_and_no_file_is_created(self):
        expected = {"binding": BASE, "settings": DEFAULTS, "saved": False, "revision": 0}
        self.assertEqual(self.lib.snapshot(BASE), expected)
        self.assertEqual(self.lib.list_entries(), [])
        self.assertEqual(self.lib.list_presets(), [])
        self.assertEqual(os.listdir(self.dir), [])

    def test_all_returned_values_are_copies(self):
        caller_binding = dict(BASE)
        first = self.lib.snapshot(caller_binding)
        first["settings"]["gain"][0] = 1.9
        first["binding"]["side"] = "right"
        self.assertEqual(caller_binding, BASE)
        self.assertEqual(self.lib.snapshot(BASE)["settings"], DEFAULTS)

        source = cfg(gain=0.5)
        saved = self.lib.save(BASE, source)
        source["gain"][0] = 1.5
        saved["settings"]["offset_deg"][0] = 90.0
        self.lib.list_entries()[0]["settings"]["smoothing_ms"] = 999.0
        self.assertEqual(self.lib.snapshot(BASE)["settings"], cfg(gain=0.5))

        preset_source = cfg(gain=0.25)
        returned = self.lib.save_preset("Soft", preset_source)
        preset_source["gain"][1] = 2.0
        returned["settings"]["gain"][2] = 2.0
        listed = self.lib.list_presets()
        listed[0]["settings"]["gain"][3] = 2.0
        listed[0]["name"] = "Changed"
        self.assertEqual(self.lib.list_presets(), [{"name": "Soft", "settings": cfg(gain=0.25)}])


class PersistenceTests(LibraryTestCase):
    def test_new_instance_sees_data_and_every_call_rereads_disk(self):
        self.lib.save(BASE, cfg(offset=12.5))
        second = MappingLibrary(self.path)
        snap = second.snapshot(BASE)
        self.assertEqual((snap["saved"], snap["revision"], snap["settings"]), (True, 1, cfg(offset=12.5)))
        second.save(BASE, cfg(offset=-45.0), expected_revision=1)
        again = self.lib.snapshot(BASE)
        self.assertEqual((again["revision"], again["settings"]), (2, cfg(offset=-45.0)))
        self.write_bytes(b"{}")  # external corruption must be noticed (no cache)
        with self.assertRaises(ValueError):
            self.lib.snapshot(BASE)

    def test_constructor_and_reads_never_create_or_modify(self):
        MappingLibrary(self.path)
        self.lib.snapshot(BASE)
        self.lib.list_entries()
        self.lib.list_presets()
        self.assertEqual(os.listdir(self.dir), [])
        self.lib.save(BASE, cfg())
        before = self.read_bytes()
        MappingLibrary(self.path).snapshot(BASE)
        self.assertEqual(self.read_bytes(), before)
        self.write_bytes(b"not json")
        MappingLibrary(self.path)
        self.assertEqual(self.read_bytes(), b"not json")

    def test_unrelated_entries_and_presets_are_preserved(self):
        other = bind(side="right", hand_serial="HND.0099")
        self.lib.save(other, cfg(gain=1.75, smoothing=250.0))
        self.lib.save_preset("Keep me", cfg(offset=30.0))
        self.lib.save(BASE, cfg(gain=0.5))
        self.lib.save(BASE, cfg(gain=0.75))
        fresh = MappingLibrary(self.path)
        snap = fresh.snapshot(other)
        self.assertEqual((snap["revision"], snap["settings"]), (1, cfg(gain=1.75, smoothing=250.0)))
        self.assertEqual(fresh.list_presets(), [{"name": "Keep me", "settings": cfg(offset=30.0)}])

    def test_empty_library_layout(self):
        self.lib.save_preset("Only", cfg())
        self.lib.delete_preset("Only")
        self.assertEqual(
            json.loads(self.read_bytes()), {"schema_version": 1, "entries": {}, "presets": []}
        )

    def test_hand_written_file_is_accepted(self):
        preset = {"gain": [2] * 20, "offset_deg": [-90.5] * 20, "smoothing_ms": 12}
        document = {"schema_version": 1, "entries": {}, "presets": [{"name": "Hand written", "settings": preset}]}
        self.write_bytes(json.dumps(document).encode("utf-8"))
        self.assertEqual(
            self.lib.list_presets(),
            [{"name": "Hand written", "settings": cfg(gain=2.0, offset=-90.5, smoothing=12.0)}],
        )

    def test_file_without_presets_key_reads_as_no_presets(self):
        self.write_bytes(b'{"schema_version": 1, "entries": {}}')
        self.assertEqual(self.lib.list_presets(), [])
        self.lib.save_preset("First", cfg())
        self.assertEqual(json.loads(self.read_bytes())["presets"], [{"name": "First", "settings": cfg()}])

    def test_listing_order_does_not_depend_on_insertion_order(self):
        other = MappingLibrary(os.path.join(self.dir, "other.json"))
        bindings = [bind(side="right"), BASE, bind(generation="hand2", sdk_user="zed"), bind(glove_serial="")]
        names = ["beta", "Alpha", "gamma", "\u00c9lan"]
        for item in bindings:
            self.lib.save(item, cfg())
        for item in reversed(bindings):
            other.save(item, cfg())
        for name in names:
            self.lib.save_preset(name, cfg())
        for name in reversed(names):
            other.save_preset(name, cfg())
        self.assertEqual(self.lib.list_entries(), other.list_entries())
        self.assertEqual(self.lib.list_presets(), other.list_presets())

    def test_threads_sharing_a_path_do_not_lose_updates(self):
        libraries = [self.lib, MappingLibrary(self.path)]
        errors = []

        def worker(library):
            try:
                for _ in range(5):
                    library.save(BASE, cfg())
            except Exception as exc:  # reported by the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(libraries[i % 2],)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.lib.snapshot(BASE)["revision"], 20)
        self.assert_only_library_file()


class RevisionTests(LibraryTestCase):
    def test_optimistic_concurrency(self):
        self.assertEqual(self.lib.save(BASE, cfg(gain=0.5), expected_revision=0)["revision"], 1)
        before = self.read_bytes()
        for stale in (0, 2, 7):
            with self.subTest(stale=stale):
                with self.assertRaises(ValueError):
                    self.lib.save(BASE, cfg(gain=0.6), expected_revision=stale)
        self.assertEqual(self.read_bytes(), before)
        self.assertEqual(self.lib.snapshot(BASE)["revision"], 1)
        self.assertEqual(self.lib.save(BASE, cfg(gain=0.6), expected_revision=1)["revision"], 2)
        self.assertEqual(self.lib.save(BASE, cfg(gain=0.6))["revision"], 3)

    def test_expected_revision_for_missing_entry(self):
        with self.assertRaises(ValueError):
            self.lib.save(BASE, cfg(), expected_revision=1)
        self.assertEqual(os.listdir(self.dir), [])

    def test_expected_revision_must_be_non_negative_int(self):
        for bad in (True, False, -1, 1.0, "0", [0]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.lib.save(BASE, cfg(), expected_revision=bad)
        self.assertEqual(os.listdir(self.dir), [])

    def test_revisions_are_tracked_per_binding(self):
        self.lib.save(BASE, cfg())
        self.lib.save(BASE, cfg())
        self.assertEqual(self.lib.save(bind(generation="hand2"), cfg(), expected_revision=0)["revision"], 1)
        self.assertEqual(self.lib.snapshot(BASE)["revision"], 2)


class MalformedFileTests(LibraryTestCase):
    OPERATIONS = {
        "snapshot": lambda lib: lib.snapshot(BASE),
        "list_entries": lambda lib: lib.list_entries(),
        "list_presets": lambda lib: lib.list_presets(),
        "save": lambda lib: lib.save(BASE, cfg(gain=0.9)),
        "save_preset": lambda lib: lib.save_preset("New", cfg()),
        "delete_preset": lambda lib: lib.delete_preset("Base"),
    }

    def baseline(self):
        self.lib.save(BASE, cfg(gain=0.5, smoothing=123.25))
        self.lib.save_preset("Base", cfg())
        return json.loads(self.read_bytes())

    def assert_rejected_and_untouched(self, data):
        self.write_bytes(data)
        for label, operation in self.OPERATIONS.items():
            with self.assertRaises(ValueError, msg=label):
                operation(self.lib)
        self.assertEqual(self.read_bytes(), data)
        self.assert_only_library_file()

    def test_schema_violations(self):
        base = self.baseline()

        def first_entry(doc):
            return next(iter(doc["entries"].values()))

        def rekey(doc):
            doc["entries"] = {"0" * 64: first_entry(doc)}

        mutations = {
            "schema_version 2": lambda d: d.update(schema_version=2),
            "schema_version true": lambda d: d.update(schema_version=True),
            "schema_version missing": lambda d: d.pop("schema_version"),
            "entries is a list": lambda d: d.update(entries=[]),
            "unknown top-level field": lambda d: d.update(comment="x"),
            "unknown entry field": lambda d: first_entry(d).update(note="x"),
            "missing revision": lambda d: first_entry(d).pop("revision"),
            "revision true": lambda d: first_entry(d).update(revision=True),
            "revision zero": lambda d: first_entry(d).update(revision=0),
            "key not matching binding": rekey,
            "binding edited in place": lambda d: first_entry(d)["binding"].update(side="right"),
            "binding extra field": lambda d: first_entry(d)["binding"].update(token="abc"),
            "gain bool": lambda d: first_entry(d)["settings"]["gain"].__setitem__(0, True),
            "gain 19 values": lambda d: first_entry(d)["settings"]["gain"].pop(),
            "gain out of range": lambda d: first_entry(d)["settings"]["gain"].__setitem__(0, 2.5),
            "settings extra field": lambda d: first_entry(d)["settings"].update(extra=1),
            "NaN literal": lambda d: first_entry(d)["settings"].update(smoothing_ms=float("nan")),
            "Infinity literal": lambda d: first_entry(d)["settings"]["offset_deg"].__setitem__(0, float("inf")),
            "presets is an object": lambda d: d.update(presets={}),
            "duplicate preset": lambda d: d["presets"].append(copy.deepcopy(d["presets"][0])),
            "unstripped preset name": lambda d: d["presets"][0].update(name=" Base "),
            "control char in preset name": lambda d: d["presets"][0].update(name="Ba\u0007se"),
            "preset extra field": lambda d: d["presets"][0].update(color="red"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                document = copy.deepcopy(base)
                mutate(document)
                self.assert_rejected_and_untouched(json.dumps(document).encode("utf-8"))

    def test_unparseable_bytes(self):
        base = json.dumps(self.baseline())
        self.assertIn("123.25", base)
        cases = {
            "empty file": b"",
            "truncated": base[:-5].encode("utf-8"),
            "array": b"[]",
            "null": b"null",
            "invalid utf-8": b'{"schema_version": 1, "entries": {}, "presets": ["\xff"]}',
            "duplicate key": b'{"schema_version": 1, "entries": {}, "entries": {}, "presets": []}',
            "overflowing literal": base.replace("123.25", "1e999").encode("utf-8"),
            "deep nesting": b"[" * 100000 + b"]" * 100000,
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assert_rejected_and_untouched(data)

    def test_file_size_limit(self):
        text = json.dumps(self.baseline()).encode("utf-8")
        at_limit = text + b" " * (FOUR_MIB - len(text))
        self.write_bytes(at_limit)
        self.assertEqual(len(self.lib.list_presets()), 1)  # exactly 4 MiB is still readable
        self.assert_rejected_and_untouched(at_limit + b" ")

    def test_too_many_presets_on_disk(self):
        presets = [{"name": f"P{i}", "settings": cfg()} for i in range(65)]
        document = {"schema_version": 1, "entries": {}, "presets": presets}
        self.assert_rejected_and_untouched(json.dumps(document).encode("utf-8"))


class AtomicWriteTests(LibraryTestCase):
    def seed(self):
        self.lib.save(BASE, cfg(gain=0.5))
        self.lib.save_preset("Keep", cfg(offset=15.0))
        return self.read_bytes()

    def test_failed_replace_preserves_file_and_removes_temp(self):
        before = self.seed()
        with mock.patch.object(mapping_library.os, "replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                self.lib.save(BASE, cfg(gain=1.5))
            with self.assertRaises(OSError):
                self.lib.save_preset("Other", cfg())
            with self.assertRaises(OSError):
                self.lib.delete_preset("Keep")
        self.assertEqual(self.read_bytes(), before)
        self.assert_only_library_file()
        snap = self.lib.snapshot(BASE)
        self.assertEqual((snap["revision"], snap["settings"]), (1, cfg(gain=0.5)))
        self.assertEqual(self.lib.list_presets(), [{"name": "Keep", "settings": cfg(offset=15.0)}])

    def test_failed_fsync_preserves_file_and_removes_temp(self):
        before = self.seed()
        with mock.patch.object(mapping_library.os, "fsync", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                self.lib.save(BASE, cfg(gain=1.5))
        self.assertEqual(self.read_bytes(), before)
        self.assert_only_library_file()

    def test_failed_first_write_leaves_nothing_behind(self):
        with mock.patch.object(mapping_library.os, "replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                self.lib.save(BASE, cfg())
        self.assertEqual(os.listdir(self.dir), [])
        self.assertFalse(self.lib.snapshot(BASE)["saved"])


class PresetTests(LibraryTestCase):
    def test_create_update_list_and_delete(self):
        unicode_name = "Pr\u00e4zision \u2713 \u624b"
        created = self.lib.save_preset("  Soft grip  ", cfg(gain=0.5))
        self.assertEqual(created, {"name": "Soft grip", "settings": cfg(gain=0.5)})
        self.lib.save_preset("Soft grip", cfg(gain=0.75))  # same exact name: update
        self.lib.save_preset("soft grip", cfg(gain=1.25))  # different exact name: new preset
        self.lib.save_preset(unicode_name, cfg(offset=-10.0))
        stored = {p["name"]: p["settings"] for p in MappingLibrary(self.path).list_presets()}
        self.assertEqual(
            stored,
            {"Soft grip": cfg(gain=0.75), "soft grip": cfg(gain=1.25), unicode_name: cfg(offset=-10.0)},
        )
        self.lib.delete_preset("  soft grip ")
        self.assertEqual(
            sorted(p["name"] for p in self.lib.list_presets()), sorted(["Soft grip", unicode_name])
        )
        for missing in ("soft grip", "never existed"):
            with self.assertRaises(KeyError):
                self.lib.delete_preset(missing)
        self.assertEqual(self.lib.list_entries(), [])

    def test_invalid_names_are_rejected(self):
        invalid = ["", "   ", "x" * 49, "line\nbreak", "tab\tinside", "\tleading-tab", "bell\x07",
                   "nul\x00", "del\x7f", "zero\u200bwidth", None, 5, b"bytes"]
        for bad in invalid:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.lib.save_preset(bad, cfg())
                with self.assertRaises(ValueError):
                    self.lib.delete_preset(bad)
        self.assertEqual(os.listdir(self.dir), [])

    def test_name_length_counts_after_stripping(self):
        name = "\u00e9" * 48
        self.assertEqual(self.lib.save_preset("  " + name + "  ", cfg())["name"], name)
        with self.assertRaises(ValueError):
            self.lib.save_preset(name + "x", cfg())
        self.assertEqual([p["name"] for p in self.lib.list_presets()], [name])

    def test_presets_never_touch_entries(self):
        self.lib.save(BASE, cfg(gain=0.5))
        self.lib.save_preset("Temp", cfg(gain=2.0))
        self.lib.delete_preset("Temp")
        snap = self.lib.snapshot(BASE)
        self.assertEqual((snap["revision"], snap["settings"]), (1, cfg(gain=0.5)))


class LimitTests(LibraryTestCase):
    def test_preset_limit_is_64(self):
        for index in range(64):
            self.lib.save_preset(f"Preset {index:02d}", cfg())
        before = self.read_bytes()
        with self.assertRaises(ValueError):
            self.lib.save_preset("Preset 64", cfg())
        self.assertEqual(self.read_bytes(), before)
        self.lib.save_preset("Preset 00", cfg(gain=2.0))  # updating at the limit is allowed
        self.lib.delete_preset("Preset 01")
        self.lib.save_preset("Preset 64", cfg())
        names = [p["name"] for p in MappingLibrary(self.path).list_presets()]
        self.assertEqual(len(names), 64)
        self.assertIn("Preset 64", names)
        self.assertNotIn("Preset 01", names)

    def test_entry_limit_on_save_and_load(self):
        self.assertEqual(mapping_library.MAX_ENTRIES, 512)  # value from the specification
        with mock.patch.object(mapping_library, "MAX_ENTRIES", 3):  # small limit keeps the test fast
            for index in range(3):
                self.lib.save(bind(sdk_user=f"user{index}"), cfg())
            before = self.read_bytes()
            with self.assertRaises(ValueError):
                self.lib.save(bind(sdk_user="user3"), cfg())
            self.assertEqual(self.read_bytes(), before)
            self.assertEqual(self.lib.save(bind(sdk_user="user0"), cfg(gain=2.0))["revision"], 2)
        with mock.patch.object(mapping_library, "MAX_ENTRIES", 2):
            before = self.read_bytes()
            with self.assertRaises(ValueError):
                self.lib.list_entries()
            self.assertEqual(self.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
