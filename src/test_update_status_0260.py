"""A recorded native version is not current evidence after integrity fails."""
from unittest.mock import patch
import unittest

import test_native_cli_maintenance as fixture
from cli_coordination import atomic
from native_cli_update import pointer


class NativeCLIStatusTests(unittest.TestCase):
    def environment(self):
        env=fixture.MaintenanceTests();env.setUp();self.addCleanup(env.doCleanups)
        atomic(env.native/'selection.json',{'current':pointer(env.new),'previous':pointer(env.old)})
        return env

    def test_failed_validation_retains_only_recorded_version_and_bounded_reason_without_side_effects(self):
        env=self.environment();env.owner._work(True)
        before=(env.native/'selection.json').read_bytes();prefs=env.folder/'update-preferences.json'
        for failure in (ValueError('synthetic selected binary checksum differs'),OSError('synthetic missing binary')):
            with self.subTest(failure=failure),patch('native_cli.current_info',side_effect=failure):
                state=env.owner.snapshot()
                self.assertFalse(state['current']['validated']);self.assertIsNone(state['current']['binary'])
                self.assertEqual(state['current']['version'],fixture.NEW)
                self.assertEqual(state['current']['validation_error'],str(failure))
                self.assertEqual((env.native/'selection.json').read_bytes(),before)
                self.assertFalse(prefs.exists());env.store.prepare.assert_not_called()

    def test_verified_reread_discards_previous_error_instead_of_poisoning_current_identity(self):
        env=self.environment();env.owner._work(True)
        with patch('native_cli.current_info',side_effect=ValueError('synthetic temporarily unavailable')):
            self.assertIn('validation_error',env.owner.snapshot()['current'])
        after=env.owner.snapshot()
        self.assertTrue(after['current']['validated']);self.assertNotIn('validation_error',after['current'])
        self.assertEqual(after['current']['binary_sha256'],env.new['binary_sha256'])
        env.store.prepare.assert_not_called()

    def test_validation_error_is_bounded_and_does_not_invent_an_active_binary(self):
        env=self.environment()
        with patch('native_cli.current_info',side_effect=ValueError('x'*5000)):
            state=env.owner.snapshot()['current']
        self.assertEqual(len(state['validation_error']),900);self.assertIsNone(state['binary'])
        self.assertFalse(state['validated']);env.store.prepare.assert_not_called()


if __name__=='__main__':unittest.main()
