import unittest
from unittest.mock import patch

import calibration_cli


class MacPassiveStatusTests(unittest.TestCase):
    def test_solver_marks_builtin_mac_unsupported_without_running_subprocess(self):
        from solver_runtime import SolverRuntime

        runtime = SolverRuntime()
        with patch('bridge_config.load_config', return_value={'mode': 'macvm'}), \
             patch('calibration_cli.controller_args') as controller_args, \
             patch('solver_runtime.subprocess.run') as run:
            runtime.run(install=False)

        state = runtime.snapshot()
        self.assertFalse(state['supported'])
        self.assertFalse(state['ready'])
        self.assertEqual(state['stage'], 'unsupported')
        self.assertIn('Linux x86_64', state['error'])
        self.assertIn('Mac ARM64', state['error'])
        controller_args.assert_not_called()
        run.assert_not_called()

    def test_initial_mac_snapshot_is_passive_and_explains_refresh(self):
        service = calibration_cli.CalibrationCLI()
        with patch.object(calibration_cli.sys, 'platform', 'darwin'), \
             patch.object(calibration_cli, 'load_config', return_value={'mode': 'macvm'}), \
             patch.object(calibration_cli, 'cli_json') as cli:
            state = service.snapshot(refresh=False)

        self.assertFalse(state['available'])
        self.assertEqual(state['mode'], 'macvm')
        self.assertIn('Click Refresh', state['error'])
        cli.assert_not_called()

    def test_expired_mac_cache_stays_passive_until_explicit_refresh(self):
        service = calibration_cli.CalibrationCLI()
        service.cache = dict(available=True, calibration_supported=True, mode='macvm',
                             users=[], current={'name': 'cached'}, devices=[], error=None)
        service.cached_at = 0
        with patch.object(calibration_cli.sys, 'platform', 'darwin'), \
             patch.object(calibration_cli, 'load_config', return_value={'mode': 'macvm'}), \
             patch.object(calibration_cli, 'cli_json') as cli:
            state = service.snapshot(refresh=False)

        self.assertEqual(state['current']['name'], 'cached')
        cli.assert_not_called()

    def test_explicit_mac_refresh_is_the_only_status_path_that_queries_cli(self):
        service = calibration_cli.CalibrationCLI()
        responses = [dict(users=[]), dict(name='tester'), dict(devices=[dict(type='glove', sn='WG123')])]
        with patch.object(calibration_cli.sys, 'platform', 'darwin'), \
             patch.object(calibration_cli, 'load_config', return_value={'mode': 'macvm'}), \
             patch.object(calibration_cli, 'cli_json', side_effect=responses) as cli:
            state = service.snapshot(refresh=True)

        self.assertTrue(state['available'])
        self.assertEqual(state['devices'], [dict(type='glove', sn='WG123')])
        self.assertEqual(cli.call_count, 3)

    def test_idle_cancel_does_not_query_or_boot_mac_controller(self):
        service = calibration_cli.CalibrationCLI()
        with patch.object(calibration_cli.sys, 'platform', 'darwin'), \
             patch.object(calibration_cli, 'load_config', return_value={'mode': 'macvm'}), \
             patch.object(calibration_cli, 'cli_json') as cli:
            state = service.cancel()

        self.assertEqual(state['run']['status'], 'idle')
        cli.assert_not_called()


if __name__ == '__main__':
    unittest.main()
