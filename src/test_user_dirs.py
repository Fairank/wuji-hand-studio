"""Tests for user_dirs.user_data_dir: temp dirs and a mocked os.environ only."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from user_dirs import user_data_dir

APP = "DemoApp"
PLATFORMS = ("win32", "darwin", "linux")


class UserDataDirTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        # posixpath.expanduser reads HOME and ntpath.expanduser reads USERPROFILE,
        # so setting both makes the tests independent of the host OS.
        env = mock.patch.dict(
            os.environ,
            {"HOME": str(self.home), "USERPROFILE": str(self.home)},
            clear=True,
        )
        env.start()
        self.addCleanup(env.stop)  # cleanups are LIFO: runs before tmp.cleanup

    def setenv(self, **values):
        patcher = mock.patch.dict(os.environ, {k: str(v) for k, v in values.items()})
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- platform locations ---------------------------------------------------

    def test_windows_uses_localappdata(self):
        base = self.root / "LocalAppData"
        self.setenv(LOCALAPPDATA=base)
        self.assertEqual(user_data_dir(APP, platform="win32"), base / APP)

    def test_windows_falls_back_to_home_appdata_local(self):
        expected = self.home / "AppData" / "Local" / APP
        self.assertEqual(user_data_dir(APP, platform="win32"), expected)

    def test_macos_uses_application_support(self):
        expected = self.home / "Library" / "Application Support" / APP
        self.assertEqual(user_data_dir(APP, platform="darwin"), expected)

    def test_linux_uses_xdg_data_home(self):
        base = self.root / "xdg"
        self.setenv(XDG_DATA_HOME=base)
        self.assertEqual(user_data_dir(APP, platform="linux"), base / APP)

    def test_linux_falls_back_to_local_share(self):
        expected = self.home / ".local" / "share" / APP
        self.assertEqual(user_data_dir(APP, platform="linux"), expected)

    def test_platform_defaults_to_sys_platform(self):
        with mock.patch.object(sys, "platform", "darwin"):
            result = user_data_dir(APP)
        self.assertEqual(result, self.home / "Library" / "Application Support" / APP)

    def test_variables_of_other_platforms_are_ignored(self):
        other = self.root / "other"
        self.setenv(LOCALAPPDATA=other, XDG_DATA_HOME=other)
        expected = self.home / "Library" / "Application Support" / APP
        self.assertEqual(user_data_dir(APP, platform="darwin"), expected)

    # --- base path validation -------------------------------------------------

    def test_empty_or_relative_env_values_are_ignored(self):
        cases = (
            ("win32", "LOCALAPPDATA", self.home / "AppData" / "Local"),
            ("linux", "XDG_DATA_HOME", self.home / ".local" / "share"),
        )
        for plat, variable, fallback in cases:
            for bad in ("", ".", "relative/dir"):
                with self.subTest(platform=plat, value=bad):
                    with mock.patch.dict(os.environ, {variable: bad}):
                        result = user_data_dir(APP, platform=plat)
                    self.assertEqual(result, fallback / APP)

    def test_base_is_normalised(self):
        messy = os.path.join(str(self.root), "a", "..", "xdg")
        self.setenv(XDG_DATA_HOME=messy)
        self.assertEqual(user_data_dir(APP, platform="linux"), self.root / "xdg" / APP)

    def test_relative_home_is_an_error(self):
        self.setenv(HOME="relative-home", USERPROFILE="relative-home")
        for plat in PLATFORMS:
            with self.subTest(platform=plat):
                with self.assertRaises(RuntimeError):
                    user_data_dir(APP, platform=plat)

    # --- app_name validation --------------------------------------------------

    def test_unsafe_names_are_rejected_on_every_platform(self):
        bad_names = [
            "", " ", ".", "..", "../evil", "a/b", "a\\b", "/abs", "C:\\abs",
            "trailing.", "trailing ", " leading", "a\x00b", "a\nb",
            "a:b", "a*b", "a?b", 'a"b', "a|b", "a<b", "a>b",
            "nul", "COM1", "lpt9.txt",
            "x" * 65,           # too many characters
            "\U0001F600" * 64,  # 64 characters but 256 UTF-8 bytes
            "bad\udc80",        # lone surrogate
        ]
        for name in bad_names:
            for plat in PLATFORMS:
                with self.subTest(name=ascii(name), platform=plat):
                    with self.assertRaises(ValueError):
                        user_data_dir(name, platform=plat)

    def test_non_string_names_are_rejected(self):
        for value in (None, 123, b"DemoApp", Path("DemoApp")):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    user_data_dir(value, platform="linux")

    def test_safe_names_are_accepted(self):
        # The last name is Chinese ("demo app"), escaped to keep this file ASCII-only.
        names = ("DemoApp", "demo-app_1.0", "Demo App", ".hidden", "\u6f14\u793a\u5e94\u7528")
        for name in names:
            for plat in PLATFORMS:
                with self.subTest(name=ascii(name), platform=plat):
                    result = user_data_dir(name, platform=plat)
                    self.assertTrue(result.is_absolute())
                    self.assertEqual(result.name, name)
                    self.assertIn(self.home, result.parents)

    # --- side effects ---------------------------------------------------------

    def test_nothing_is_created_by_default(self):
        result = user_data_dir(APP, platform="linux")
        self.assertFalse(result.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_create_makes_the_directory_and_is_idempotent(self):
        self.setenv(XDG_DATA_HOME=self.root / "xdg")
        first = user_data_dir(APP, platform="linux", create=True)
        second = user_data_dir(APP, platform="linux", create=True)
        self.assertEqual(first, second)
        self.assertTrue(first.is_dir())

    def test_create_fails_when_a_file_is_in_the_way(self):
        base = self.root / "xdg"
        base.mkdir()
        (base / APP).write_text("not a directory", encoding="utf-8")
        self.setenv(XDG_DATA_HOME=base)
        with self.assertRaises(FileExistsError):
            user_data_dir(APP, platform="linux", create=True)


if __name__ == "__main__":
    unittest.main()
