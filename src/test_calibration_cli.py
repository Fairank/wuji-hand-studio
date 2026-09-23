"""Guardrails around official named-user hand-model calibration."""
import io
import time
import unittest
from unittest.mock import patch

import calibration_cli


class FakeProcess:
    def __init__(self, command, **_kwargs):
        self.command = command
        self.stdout = io.StringIO('{"schema_version":2,"calibration":"hand_model","event":"progress","step_index":1}\n')
        self.stderr = io.StringIO('')
        self.returncode = None

    def wait(self):
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode


class CalibrationTest(unittest.TestCase):
    def setUp(self):
        self.config = {'mode': 'local', 'cli': 'wuji'}
        self.users = [{'name': 'Default', 'is_default': True}, {'name': 'Demo', 'is_default': False}]
        self.current = {'name': 'Demo', 'left_hand': {'calibrated': False}, 'right_hand': {'calibrated': False}}
        self.devices = [{'sn': 'WG123'}]
        self.calls = []

    def cli(self, _config, *args):
        self.calls.append(args)
        if args == ('user', 'list'):
            return {'users': self.users}
        if args == ('user', 'show'):
            return self.current
        if args == ('devices',):
            return {'devices': self.devices}
        return {'ok': True}

    def test_default_user_cannot_start(self):
        self.current['name'] = 'Default'
        with patch.object(calibration_cli, 'load_config', return_value=self.config), patch.object(calibration_cli, 'cli_json', side_effect=self.cli):
            with self.assertRaisesRegex(ValueError, 'named SDK user'):
                calibration_cli.CalibrationCLI().start('left', 'WG123')

    def test_existing_model_requires_explicit_replace(self):
        self.current['left_hand']['calibrated'] = True
        with patch.object(calibration_cli, 'load_config', return_value=self.config), patch.object(calibration_cli, 'cli_json', side_effect=self.cli), patch.object(calibration_cli.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(ValueError, 'confirm replacement'):
                calibration_cli.CalibrationCLI().start('left', 'WG123')
            popen.assert_not_called()

    def test_only_latest_discovered_serial_is_accepted(self):
        with patch.object(calibration_cli, 'load_config', return_value=self.config), patch.object(calibration_cli, 'cli_json', side_effect=self.cli), patch.object(calibration_cli.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(ValueError, 'not in the latest'):
                calibration_cli.CalibrationCLI().start('right', 'UNKNOWN')
            popen.assert_not_called()

    def test_hand_devices_are_not_calibration_sources(self):
        self.devices.append({'sn': 'WH456', 'model': 'wuji_hand_2'})
        with patch.object(calibration_cli, 'load_config', return_value=self.config), patch.object(calibration_cli, 'cli_json', side_effect=self.cli):
            status = calibration_cli.CalibrationCLI().snapshot(refresh=True)
        self.assertEqual([d['sn'] for d in status['devices']], ['WG123'])

    def test_remote_ssh_is_status_only(self):
        config = {'mode': 'ssh', 'cli': 'wuji'}
        with patch.object(calibration_cli, 'load_config', return_value=config), patch.object(calibration_cli, 'cli_json', side_effect=self.cli):
            service = calibration_cli.CalibrationCLI()
            self.assertFalse(service.snapshot(refresh=True)['calibration_supported'])
            with self.assertRaisesRegex(ValueError, 'local managed controller'):
                service.start('left', 'WG123')

    def test_valid_named_user_runs_official_jsonl_flow(self):
        created = []
        def fake_open(command, **kwargs):
            created.append((command, kwargs))
            return FakeProcess(command, **kwargs)
        with patch.object(calibration_cli, 'load_config', return_value=self.config), patch.object(calibration_cli, 'cli_json', side_effect=self.cli), patch.object(calibration_cli.subprocess, 'Popen', side_effect=fake_open):
            service = calibration_cli.CalibrationCLI()
            service.start('left', 'WG123')
            service.thread.join(timeout=2)
            self.assertFalse(service.thread.is_alive())
            self.assertEqual(service.run['status'], 'completed')
            self.assertEqual(service.run['step'], 1)
            self.assertEqual(created[0][0], ['wuji', '--jsonl', 'calib', 'hand-model', '--sn', 'WG123', '--handedness', 'left', '--timeout-s', '900'])
            self.assertEqual(created[0][1]['stdin'], calibration_cli.subprocess.DEVNULL)

    def test_cli_unavailable_returns_safe_status(self):
        with patch.object(calibration_cli, 'load_config', side_effect=ValueError('not configured')):
            status = calibration_cli.CalibrationCLI().snapshot(refresh=True)
        self.assertFalse(status['available'])
        self.assertEqual(status['error'], 'not configured')


if __name__ == '__main__':
    unittest.main()
