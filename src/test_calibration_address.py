"""Synthetic checks for native discovery and Linux-owned glove calibration.

No SDK, device, socket, actual lease file or child process is used. The locked
runner tests replace both lock factories, signals, stdin and background threads.
"""
from contextlib import ExitStack, contextmanager
import copy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import calibration_address
import calibration_cli
import calibration_runner
import official_device_discovery


SERIAL = 'WG1_SYNTHETIC_LEFT'
ADDRESS = '192.168.1.121:9400'


class SyntheticGlove:
    def __init__(self, serial=SERIAL, side='left', side_error=None):
        self.serial_number = serial
        self.side = side
        self.side_error = side_error
        self.disconnect = Mock()

    def hand_side(self):
        def get():
            if self.side_error:
                raise self.side_error
            return self.side
        return SimpleNamespace(get=get)


class NoThread:
    """Record dispatch without running a reader against any real stream."""
    def __init__(self, target=None, args=(), daemon=None, **_kwargs):
        self.target, self.args, self.daemon = target, args, daemon
        self.started = False

    def start(self):
        self.started = True


class AddressTests(unittest.TestCase):
    def test_address_canonicalizes_only_ipv4_and_numeric_port(self):
        for value, expected in [('udp/' + ADDRESS, ADDRESS), (ADDRESS, ADDRESS),
                                ('10.0.0.7:00080', '10.0.0.7:80'),
                                ('172.16.5.4:1', '172.16.5.4:1'),
                                ('192.168.1.121:65535', '192.168.1.121:65535')]:
            with self.subTest(value=value):
                self.assertEqual(calibration_address.network_address(value), expected)

    def test_invalid_address_never_becomes_a_device_endpoint(self):
        values = [None, True, 1, [], '', 'localhost:9400', '192.168.1.121',
                  '127.0.0.1:9400', '0.0.0.0:9400', '224.0.0.1:9400',
                  '169.254.2.3:9400', '192.168.1.121:0', '192.168.1.121:65536',
                  '192.168.1.121:-1', '192.168.1.121:+2', '192.168.1.121:９４００',
                  '192.168.1.121:9400 ', '192.168.1.121:9400:1',
                  '192.168.1.121:9400\n', 'udp://192.168.1.121:9400',
                  '[::1]:9400', 'user@192.168.1.121:9400', '1.2.3.999:9400']
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                calibration_address.network_address(value)

    def test_reserved_and_limited_broadcast_addresses_are_rejected(self):
        for value in ['255.255.255.255:9400', '240.0.0.1:9400']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                calibration_address.network_address(value)


class GloveIdentityTests(unittest.TestCase):
    def sdk(self, manager):
        return SimpleNamespace(WujiGlove=SyntheticGlove,
                               SdkManager=SimpleNamespace(instance=Mock(return_value=manager)))

    def test_verified_glove_disconnects_before_return(self):
        for side in ['left', 'Handedness.LEFT']:
            glove = SyntheticGlove(side=side)
            manager = SimpleNamespace(connect=Mock(return_value=glove))
            with self.subTest(side=side):
                self.assertIsNone(calibration_address.verify_glove('udp/' + ADDRESS, SERIAL, 'left',
                                                                  manager=manager, sdk=self.sdk(manager)))
                manager.connect.assert_called_once_with(address=ADDRESS,
                                                        device_name='workbench_calibration_identity')
                glove.disconnect.assert_called_once_with()

    def test_default_manager_and_sdk_are_synthetic(self):
        glove = SyntheticGlove()
        manager = SimpleNamespace(connect=Mock(return_value=glove))
        sdk = self.sdk(manager)
        with patch.dict('sys.modules', {'wuji_sdk': sdk}):
            calibration_address.verify_glove(ADDRESS, SERIAL, 'left')
        sdk.SdkManager.instance.assert_called_once_with()
        manager.connect.assert_called_once()
        glove.disconnect.assert_called_once_with()

    def test_wrong_type_serial_or_side_always_disconnects(self):
        wrong_type = SimpleNamespace(serial_number=SERIAL, disconnect=Mock())
        examples = [('type', wrong_type), ('serial', SyntheticGlove(serial='WG_OTHER')),
                    ('side', SyntheticGlove(side='right')),
                    ('unknown_side', SyntheticGlove(side='unknown'))]
        for label, glove in examples:
            manager = SimpleNamespace(connect=Mock(return_value=glove))
            with self.subTest(case=label), self.assertRaises(ValueError):
                calibration_address.verify_glove(ADDRESS, SERIAL, 'left', manager=manager,
                                                 sdk=self.sdk(manager))
            glove.disconnect.assert_called_once_with()

    def test_feedback_read_failure_still_disconnects(self):
        glove = SyntheticGlove(side_error=RuntimeError('synthetic side read failed'))
        manager = SimpleNamespace(connect=Mock(return_value=glove))
        with self.assertRaisesRegex(RuntimeError, 'synthetic side read'):
            calibration_address.verify_glove(ADDRESS, SERIAL, 'left', manager=manager,
                                             sdk=self.sdk(manager))
        glove.disconnect.assert_called_once_with()

    def test_bad_expected_identity_or_address_never_connects(self):
        manager = SimpleNamespace(connect=Mock())
        cases = [('127.0.0.1:9400', SERIAL, 'left'), (ADDRESS, 'WH2_SYNTHETIC', 'left'),
                 (ADDRESS, 'WG', 'left'), (ADDRESS, None, 'left'),
                 (ADDRESS, SERIAL, 'LEFT'), (ADDRESS, SERIAL, 'unknown')]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                calibration_address.verify_glove(*args, manager=manager, sdk=self.sdk(manager))
        manager.connect.assert_not_called()


def address_command(user='Demo', overwrite='keep', serial=SERIAL, side='left', address=ADDRESS):
    return ['calibrate_address', user, overwrite, serial, side, '/fake/linux/wuji',
            '--jsonl', 'calib', 'hand-model', '--address', address, '--timeout-s', '900']


class AddressRunnerTests(unittest.TestCase):
    @contextmanager
    def environment(self, current=None, verify_error=None, query_code=0):
        events = []
        active = {'profile': False, 'device': False}
        current = current if current is not None else {'name': 'Demo', 'is_default': False,
                                                       'left_hand': {'calibrated': False}}
        owner = self

        class Lease:
            def __init__(self, key): self.key = key
            def __enter__(self):
                if self.key == 'device': owner.assertTrue(active['profile'])
                active[self.key] = True
                events.append(self.key + '.enter')
                return self
            def __exit__(self, *_):
                active[self.key] = False
                events.append(self.key + '.exit')

        def profile_factory(*args, **kwargs):
            self.assertEqual(args, ())
            self.assertEqual(kwargs, {'exclusive': True})
            return Lease('profile')

        def device_factory(serial):
            self.assertEqual(serial, SERIAL)
            return Lease('device')

        def check_user(command, **kwargs):
            self.assertEqual(active, {'profile': True, 'device': True})
            self.assertEqual(command, ['/fake/linux/wuji', '--json', 'user', 'show'])
            self.assertEqual(kwargs, {'capture_output': True, 'text': True, 'timeout': 20})
            events.append('user.show')
            return SimpleNamespace(returncode=query_code, stdout=json.dumps(current))

        def verify(address, serial, side):
            self.assertEqual(active, {'profile': True, 'device': True})
            self.assertEqual((address, serial, side), (ADDRESS, SERIAL, 'left'))
            self.assertIn('user.show', events)
            events.append('identity.verify')
            if verify_error: raise verify_error

        process = Mock()
        process.poll.return_value = 0
        process.wait.side_effect = lambda: events.append('wait') or 0

        def spawn(command, **kwargs):
            self.assertEqual(active, {'profile': True, 'device': True})
            self.assertEqual(events[-1], 'identity.verify')
            self.assertEqual(command, address_command()[5:])
            self.assertEqual(command.count('--address'), 1)
            self.assertNotIn('--sn', command)
            self.assertNotIn('--handedness', command)
            self.assertEqual(kwargs, {'stdin': calibration_runner.subprocess.DEVNULL})
            events.append('popen')
            return process

        with ExitStack() as stack:
            p = stack.enter_context(patch('sdk_session.ProfileLease', side_effect=profile_factory))
            d = stack.enter_context(patch('device_ownership.DeviceOwnership', side_effect=device_factory))
            q = stack.enter_context(patch.object(calibration_runner.subprocess, 'run', side_effect=check_user))
            v = stack.enter_context(patch('calibration_address.verify_glove', side_effect=verify))
            s = stack.enter_context(patch.object(calibration_runner.subprocess, 'Popen', side_effect=spawn))
            stack.enter_context(patch.object(calibration_runner.signal, 'signal'))
            stack.enter_context(patch.object(calibration_runner.sys, 'stdin', io.StringIO('')))
            stack.enter_context(patch.object(calibration_runner.threading, 'Thread', NoThread))
            yield SimpleNamespace(events=events, active=active, profile=p, device=d, query=q,
                                  verify=v, spawn=s, process=process)

    def test_locked_order_is_user_model_identity_capture_then_release(self):
        with self.environment() as e:
            self.assertEqual(calibration_runner.run(address_command()), 0)
        self.assertEqual(e.events, ['profile.enter', 'device.enter', 'user.show',
                                   'identity.verify', 'popen', 'wait', 'device.exit', 'profile.exit'])
        self.assertEqual(e.active, {'profile': False, 'device': False})

    def test_default_expected_user_is_rejected_before_any_lease_or_capture(self):
        for name in ['Default', 'default', 'DEFAULT', '']:
            with self.subTest(user=name), self.environment() as e:
                with self.assertRaisesRegex(ValueError, 'Named calibration user'):
                    calibration_runner.run(address_command(user=name))
                e.profile.assert_not_called(); e.device.assert_not_called()
                e.query.assert_not_called(); e.verify.assert_not_called(); e.spawn.assert_not_called()

    def test_locked_user_change_default_or_unknown_model_prevents_identity_and_capture(self):
        cases = [('changed', {'name': 'Other', 'left_hand': {'calibrated': False}}),
                 ('default', {'name': 'Demo', 'is_default': True, 'left_hand': {'calibrated': False}}),
                 ('missing_model', {'name': 'Demo'}),
                 ('unknown_model', {'name': 'Demo', 'left_hand': {'calibrated': 'false'}})]
        for label, current in cases:
            with self.subTest(case=label), self.environment(current=current) as e:
                with self.assertRaises(ValueError):calibration_runner.run(address_command())
                e.verify.assert_not_called(); e.spawn.assert_not_called()
            self.assertEqual(e.events[-2:], ['device.exit', 'profile.exit'])

    def test_failed_official_user_read_does_not_probe_or_capture(self):
        with self.environment(query_code=1) as e:
            with self.assertRaisesRegex(ValueError, 'Cannot verify'):
                calibration_runner.run(address_command())
            e.verify.assert_not_called(); e.spawn.assert_not_called()

    def test_new_model_without_overwrite_consent_does_not_probe_or_capture(self):
        current = {'name': 'Demo', 'left_hand': {'calibrated': True}}
        with self.environment(current=current) as e:
            with self.assertRaisesRegex(ValueError, 'explicit replacement'):
                calibration_runner.run(address_command())
            e.verify.assert_not_called(); e.spawn.assert_not_called()

    def test_explicit_replace_still_rechecks_user_and_identity(self):
        current = {'name': 'Demo', 'left_hand': {'calibrated': True}}
        with self.environment(current=current) as e:
            self.assertEqual(calibration_runner.run(address_command(overwrite='replace')), 0)
            e.query.assert_called_once(); e.verify.assert_called_once(); e.spawn.assert_called_once()

    def test_identity_failure_prevents_capture_and_releases_both_leases(self):
        with self.environment(verify_error=ValueError('synthetic identity changed')) as e:
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                calibration_runner.run(address_command())
            e.verify.assert_called_once(); e.spawn.assert_not_called()
        self.assertEqual(e.events[-2:], ['device.exit', 'profile.exit'])

    def test_malformed_address_envelopes_never_acquire_or_spawn(self):
        valid = address_command()
        examples = [valid[:4], valid + ['--sn', SERIAL],
                    valid[:9] + ['--sn', ADDRESS, '--timeout-s', '900'],
                    address_command(side='unknown'), address_command(address='localhost:9400')]
        for command in examples:
            with self.subTest(command=command), self.environment() as e:
                with self.assertRaises(ValueError):calibration_runner.run(command)
                e.profile.assert_not_called(); e.verify.assert_not_called(); e.spawn.assert_not_called()


class NativeDiscoveryAdapterTests(unittest.TestCase):
    def test_default_windows_wsl_device_scan_uses_only_native_cli(self):
        for cli in ['wuji', '/usr/local/bin/wuji']:
            config = {'mode': 'wsl', 'cli': cli}
            with self.subTest(cli=cli), patch.object(official_device_discovery.sys, 'platform', 'win32'), \
                    patch.object(official_device_discovery, 'native_devices', return_value={'devices': [{'sn': SERIAL}]}) as native, \
                    patch.object(calibration_cli, 'local_run') as local, patch.object(calibration_cli, 'remote_run') as remote:
                self.assertEqual(calibration_cli.cli_json(config, 'devices'), {'devices': [{'sn': SERIAL}]})
                native.assert_called_once_with(); local.assert_not_called(); remote.assert_not_called()

    def test_native_discovery_error_is_not_silently_replaced_by_linux_scan(self):
        with patch.object(official_device_discovery.sys, 'platform', 'win32'), \
                patch.object(official_device_discovery, 'native_devices', side_effect=ValueError('synthetic native failure')), \
                patch.object(calibration_cli, 'local_run') as local:
            with self.assertRaisesRegex(ValueError, 'native failure'):
                calibration_cli.cli_json({'mode': 'wsl', 'cli': 'wuji'}, 'devices')
            local.assert_not_called()

    def test_user_model_queries_stay_on_linux_even_when_device_scan_is_native(self):
        config = {'mode': 'wsl', 'cli': 'wuji'}
        for args in [('user', 'list'), ('user', 'show')]:
            with self.subTest(args=args), patch.object(official_device_discovery.sys, 'platform', 'win32'), \
                    patch.object(official_device_discovery, 'native_devices') as native, \
                    patch('official_cli.cli_path', return_value='/fake/linux/wuji'), \
                    patch.object(calibration_cli, 'controller_args', side_effect=lambda _config, argv: ['fake-linux', *argv]) as wrap, \
                    patch.object(calibration_cli, 'local_run', return_value=(0, '{"name":"Demo"}', '')) as local:
                self.assertEqual(calibration_cli.cli_json(config, *args), {'name': 'Demo'})
                native.assert_not_called()
                wrap.assert_called_once_with(config, ['/fake/linux/wuji', '--json', *args])
                self.assertEqual(local.call_args.args[0][0], 'fake-linux')

    def test_explicit_custom_cli_and_non_windows_discovery_keep_existing_path(self):
        for platform, config in [('linux', {'mode': 'local', 'cli': 'wuji'}),
                                 ('win32', {'mode': 'wsl', 'cli': '/opt/custom/wuji'})]:
            with self.subTest(platform=platform, config=config), patch.object(official_device_discovery.sys, 'platform', platform), \
                    patch.object(official_device_discovery, 'native_devices') as native, \
                    patch('official_cli.cli_path', return_value='/fake/linux/wuji'), \
                    patch.object(calibration_cli, 'controller_args', side_effect=lambda _config, argv: argv), \
                    patch.object(calibration_cli, 'local_run', return_value=(0, '{"devices":[]}', '')) as local:
                self.assertEqual(calibration_cli.cli_json(config, 'devices'), {'devices': []})
                native.assert_not_called(); local.assert_called_once()


class NativeCaptureAdapterTests(unittest.TestCase):
    def setUp(self):
        self.config = {'mode': 'wsl', 'cli': 'wuji'}
        self.status = {'available': True, 'calibration_supported': True,
                       'users': [{'name': 'Demo', 'is_default': False}],
                       'current': {'name': 'Demo', 'left_hand': {'calibrated': False}},
                       'devices': [{'sn': SERIAL, 'model': 'Wuji Glove', 'handedness': 'left',
                                    'address': 'udp/' + ADDRESS}]}

    @contextmanager
    def environment(self, native=True):
        service = calibration_cli.CalibrationCLI()
        process = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
        with ExitStack() as stack:
            stack.enter_context(patch.object(calibration_cli, 'load_config', return_value=self.config))
            snapshot = stack.enter_context(patch.object(service, 'snapshot', return_value=self.status))
            stack.enter_context(patch('official_cli.cli_path', return_value='/fake/linux/wuji'))
            stack.enter_context(patch.object(official_device_discovery, 'use_native', return_value=native))
            command = stack.enter_context(patch.object(calibration_cli, 'runner_command', return_value=['fake-linux-envelope']))
            spawn = stack.enter_context(patch.object(calibration_cli.subprocess, 'Popen', return_value=process))
            stack.enter_context(patch.object(calibration_cli.threading, 'Thread', NoThread))
            yield SimpleNamespace(service=service, snapshot=snapshot, command=command, spawn=spawn)

    def test_native_scan_identity_is_wrapped_for_linux_address_capture(self):
        with self.environment() as e:
            e.service.start('left', SERIAL)
            args = ['/fake/linux/wuji', '--jsonl', 'calib', 'hand-model', '--address', ADDRESS,
                    '--timeout-s', '900']
            e.command.assert_called_once_with(self.config, 'calibrate_address', args,
                                             'Demo', False, SERIAL, 'left')
            e.spawn.assert_called_once()
            self.assertEqual(e.spawn.call_args.args[0], ['fake-linux-envelope'])
            self.assertEqual(e.service.run['serial'], SERIAL)
            self.assertEqual(e.service.run['user'], 'Demo')
            self.assertTrue(e.service.thread.started)

    def test_runner_envelope_keeps_identity_outside_single_official_selector(self):
        config = {'mode': 'local', 'agent_directory': '/fake/controller', 'python': 'python-test'}
        args = ['/fake/linux/wuji', '--jsonl', 'calib', 'hand-model', '--address', ADDRESS,
                '--timeout-s', '900']
        command = calibration_cli.runner_command(config, 'calibrate_address', args,
                                                  'Demo', True, SERIAL, 'left')
        self.assertEqual(command, ['python-test', '-u', '/fake/controller/calibration_runner.py',
                                  'calibrate_address', 'Demo', 'replace', SERIAL, 'left', *args])
        self.assertEqual(command.count('--address'), 1)
        self.assertNotIn('--sn', command); self.assertNotIn('--handedness', command)

    def test_host_default_overwrite_and_side_checks_still_prevent_launch(self):
        cases = [('default', lambda s: s['current'].update(name='Default'), False),
                 ('profile_marked_default', lambda s: s['users'][0].update(is_default=True), False),
                 ('existing_model', lambda s: s['current']['left_hand'].update(calibrated=True), False),
                 ('side_mismatch', lambda s: s['devices'][0].update(handedness='right'), False),
                 ('missing_address', lambda s: s['devices'][0].pop('address'), False)]
        original = copy.deepcopy(self.status)
        for label, mutate, replace in cases:
            self.status = copy.deepcopy(original); mutate(self.status)
            with self.subTest(case=label), self.environment() as e:
                with self.assertRaises(ValueError):e.service.start('left', SERIAL, replace=replace)
                e.command.assert_not_called(); e.spawn.assert_not_called()

    def test_explicit_host_replacement_is_carried_into_locked_linux_envelope(self):
        self.status['current']['left_hand']['calibrated'] = True
        with self.environment() as e:
            e.service.start('left', SERIAL, replace=True)
            self.assertEqual(e.command.call_args.args[3:], ('Demo', True, SERIAL, 'left'))

    def test_non_native_capture_retains_serial_and_handedness_flags(self):
        with self.environment(native=False) as e:
            e.service.start('left', SERIAL)
            args = ['/fake/linux/wuji', '--jsonl', 'calib', 'hand-model', '--sn', SERIAL,
                    '--handedness', 'left', '--timeout-s', '900']
            e.command.assert_called_once_with(self.config, 'calibrate', args, 'Demo', False)


if __name__ == '__main__':
    unittest.main()
