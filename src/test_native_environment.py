"""Native CLI environment and preflight contracts; every process is a fake.

No real CLI, discovery, SDK, console, window, network, or firmware operation.
"""
from contextlib import ExitStack, nullcontext
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent

import native_cli
import official_device_discovery as discovery
import native_calibration as calibration
import official_visualization as visualization
import firmware_host

TOKEN = 'a' * 32
SN = 'WG_SYNTHETIC_ONLY'
COMPONENT = dict(binary='C:/never-execute/synthetic-wuji.exe', version='2026.10.12')


class DeferredThread:
    def __init__(self, *args, **kwargs): pass
    def start(self): pass
    def is_alive(self): return False
    def join(self, timeout=None): pass


class Process:
    def __init__(self, output='{}'):
        self.output = output
        self.returncode = 0
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
    def communicate(self, timeout=None): return self.output, ''
    def poll(self): return 0
    def wait(self, timeout=None): return 0


def owned(process):
    value = Mock(process=process)
    value.close.return_value = True
    value.__enter__ = Mock(return_value=value)
    value.__exit__ = Mock(return_value=False)
    return value


def capture_launch(point, folder, *, rejected=False):
    """Invoke production entry points, replacing every process boundary."""
    with ExitStack() as stack:
        stack.enter_context(patch.object(native_cli, 'prepare', return_value=copy.deepcopy(COMPONENT)))
        if point == 'metadata':
            spawn = stack.enter_context(patch('windows_process_job.OwnedProcess', return_value=owned(Process())))
            action = lambda: native_cli.json_command('user', 'show')
        elif point == 'discovery_finite':
            spawn = stack.enter_context(patch.object(discovery.subprocess, 'run', return_value=N(returncode=0, stdout='{"devices":[{"sn":"SYNTHETIC"}]}', stderr='')))
            action = discovery.native_devices
        elif point == 'discovery_cancellable':
            spawn = stack.enter_context(patch('windows_process_job.OwnedProcess', return_value=owned(Process('{"devices":[{"sn":"SYNTHETIC"}]}'))))
            action = lambda: discovery.native_devices(cancelled=lambda: False)
        elif point == 'calibration_worker':
            stack.enter_context(patch.object(calibration, 'root_folder', return_value=folder))
            stack.enter_context(patch.object(calibration, 'gate', side_effect=lambda *a: nullcontext()))
            stack.enter_context(patch.object(calibration, 'reserve', return_value=TOKEN))
            stack.enter_context(patch.object(calibration, 'reservation', return_value=dict(token=TOKEN)))
            stack.enter_context(patch.object(calibration, 'atomic'))
            stack.enter_context(patch.object(calibration.threading, 'Thread', DeferredThread))
            owner = N(lock=threading.RLock(), metadata_check=Mock(), run=dict(running=False), process=None, thread=None,reports=None)
            owner.snapshot = lambda **kwargs: dict(owner.run)
            service = calibration.NativeCalibration(owner)
            stack.enter_context(patch.object(service, 'recover'))
            spawn = stack.enter_context(patch('windows_process_job.OwnedProcess', return_value=owned(Process())))
            action = lambda: service.start_human(SN, 'left')
        elif point == 'viewer':
            stack.enter_context(patch.object(visualization, 'gate', side_effect=lambda *a: nullcontext()))
            stack.enter_context(patch.object(visualization, 'release'))
            stack.enter_context(patch.object(visualization, 'reservation', return_value=dict(token=TOKEN, job_name='Local\\HandWorkbench-OfficialViz-' + TOKEN)))
            stack.enter_context(patch.object(visualization.threading, 'Thread', DeferredThread))
            service = visualization.OfficialVisualization(folder)
            service.token = TOKEN
            spawn = stack.enter_context(patch('windows_process_job.OwnedProcess', return_value=owned(Process())))
            action = lambda: service._run_native(SN, 'light')
        elif point == 'firmware_read':
            stack.enter_context(patch.object(firmware_host, 'reservation', return_value=dict(kind='firmware_check', token=TOKEN, serial=SN)))
            spawn = Mock(return_value=N(returncode=0, stdout='{}', stderr=''))
            host = firmware_host.FirmwareHost(N(), folder=folder, runner=spawn)
            stack.enter_context(patch.object(host, '_component', return_value=copy.deepcopy(COMPONENT)))
            action = lambda: host(['upgrade', '--sn', SN, '--check', '--json'])
        else:
            raise AssertionError(point)
        try:
            action()
        except ValueError:
            if not rejected: raise
            spawn.assert_not_called()
            return None
        if rejected:
            spawn.assert_not_called()
            if point != 'viewer' or service.state.get('phase') != 'error' or not service.state.get('error'):
                raise AssertionError('Invalid HOME was not surfaced as an error')
            return None
        spawn.assert_called_once()
        return spawn.call_args.kwargs['env']


class LaunchEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ROOT.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='.native-env-用户 ', dir=ROOT)
        cls.folder = Path(cls.temp.name)
        cls.explicit = cls.folder / 'explicit SDK user'
        cls.explicit.mkdir()
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def verify_launch(self, point, mode):
        baseline = dict(USERPROFILE=str(self.folder), LOCALAPPDATA=str(self.folder / 'app-data'),
                        PATH=os.defpath, WUJI_NO_UPDATE_CHECK='0', WUJI_TELEMETRY_DISABLE='0')
        if mode == 'empty': baseline['HOME'] = ''
        elif mode == 'explicit': baseline['HOME'] = str(self.explicit)
        with patch.dict(os.environ, baseline, clear=True):
            before = dict(os.environ)
            child = capture_launch(point, self.folder)
            self.assertEqual(dict(os.environ), before, 'parent environment changed')
        self.assertEqual(child.get('HOME'), str(self.explicit if mode == 'explicit' else self.folder))
        self.assertEqual(child.get('USERPROFILE'), str(self.folder))
        self.assertEqual(child.get('LOCALAPPDATA'), str(self.folder / 'app-data'))
        self.assertEqual(child.get('WUJI_NO_UPDATE_CHECK'), '1')
        self.assertEqual(child.get('WUJI_TELEMETRY_DISABLE'), '1')

    def verify_invalid_launch(self, point):
        with patch.dict(os.environ, dict(HOME='relative-sdk-home', USERPROFILE=str(self.folder)), clear=True):
            before = dict(os.environ)
            capture_launch(point, self.folder, rejected=True)
            self.assertEqual(dict(os.environ), before)

    def test_helper_preserves_base_and_parent_with_explicit_home(self):
        from native_environment import official_cli_environment
        base = dict(HOME=str(self.explicit), USERPROFILE=str(self.folder), WUJI_NO_UPDATE_CHECK='0')
        original = dict(base)
        parent = dict(os.environ)
        child = official_cli_environment(base)
        self.assertEqual(base, original)
        self.assertIsNot(child, base)
        self.assertEqual(dict(os.environ), parent)
        self.assertEqual(child['HOME'], str(self.explicit))
        self.assertEqual(child['WUJI_NO_UPDATE_CHECK'], '1')
        self.assertEqual(child['WUJI_TELEMETRY_DISABLE'], '1')

    def test_helper_rejects_invalid_explicit_home_without_fallback(self):
        from native_environment import official_cli_environment
        for value in ('relative-home', 'C:relative', '\\root-relative', 'C:/invalid\x00home', 4):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    official_cli_environment(dict(HOME=value, USERPROFILE=str(self.folder)))

    def test_non_windows_helper_keeps_controller_home(self):
        from native_environment import official_cli_environment
        with patch('native_environment.sys.platform', 'linux'):
            self.assertEqual(official_cli_environment(dict(HOME='/home/controller'))['HOME'], '/home/controller')
            self.assertNotIn('HOME', official_cli_environment({}))

    def test_helper_to_existing_conpty_bridge_keeps_original_parent(self):
        from native_environment import official_cli_environment
        import native_terminal_worker
        spawn = Mock(return_value=object())
        module = N(__version__='3.1.0', Backend=N(ConPTY=0), PtyProcess=N(spawn=spawn))
        with patch.dict(os.environ, dict(USERPROFILE=str(self.folder), PYWINPTY_BLOCK='retained'), clear=True), patch.dict(sys.modules, winpty=module):
            before = dict(os.environ)
            child = official_cli_environment()
            native_terminal_worker._spawn(['synthetic.exe', 'Chinese 用户 argument'], env=child)
            self.assertEqual(dict(os.environ), before)
            forwarded = spawn.call_args.kwargs['env']
            self.assertEqual(forwarded, child)
            self.assertIsNot(forwarded, child)
            self.assertEqual(forwarded['HOME'], str(self.folder))
            self.assertEqual(forwarded['WUJI_NO_UPDATE_CHECK'], '1')
            self.assertEqual(forwarded['WUJI_TELEMETRY_DISABLE'], '1')

    def test_invalid_home_is_rejected_before_cli_identity_prepares_software(self):
        host = firmware_host.FirmwareHost(N(), folder=self.folder)
        with patch.dict(os.environ, dict(HOME='relative-sdk-home', USERPROFILE=str(self.folder)), clear=True), patch.object(host, '_component') as component:
            with self.assertRaises(ValueError): host.cli_identity()
            component.assert_not_called()

    def firmware_service_fixture(self):
        from firmware_service import FirmwareService, _identity
        component = dict(COMPONENT, binary_sha256='synthetic-verified-digest')
        host = firmware_host.FirmwareHost(N(), folder=self.folder)
        host._component = Mock(return_value=component)
        executor, acquire, release, idle = Mock(), Mock(return_value=TOKEN), Mock(), Mock()
        row = dict(sn=SN, kind='glove', side='left', observed_at=1000, stale=False, selectable=True, conflict=False)
        service = FirmwareService(executor, host.cli_identity, idle, acquire, release,
                                  lambda serial: dict(row), clock=lambda: 1000)
        device = service._device(SN)
        preview = dict(token='synthetic-preview', created_at=900, expires_at=1100, device=device,
                       cli=_identity(dict(version=component['version'], sha256=component['binary_sha256'], platform='windows-native')))
        return service, host, executor, acquire, release, preview

    def test_invalid_home_check_preflight_never_reserves_launches_or_locks(self):
        service, host, executor, acquire, release, preview = self.firmware_service_fixture()
        with patch.dict(os.environ, dict(HOME='relative-sdk-home', USERPROFILE=str(self.folder)), clear=True), patch.object(service, '_launch') as launch:
            with self.assertRaises(ValueError): service.start_check(SN)
        state = service.snapshot()
        self.assertEqual(state['phase'], 'check_failed')
        self.assertFalse(state['locked'])
        self.assertFalse(state['recovery_required'])
        self.assertIsNone(service._reservation)
        host._component.assert_not_called()
        executor.assert_not_called(); acquire.assert_not_called(); release.assert_not_called(); launch.assert_not_called()

    def test_invalid_home_upgrade_preflight_never_reserves_launches_or_locks(self):
        service, host, executor, acquire, release, preview = self.firmware_service_fixture()
        service._preview = preview
        with patch.dict(os.environ, dict(HOME='relative-sdk-home', USERPROFILE=str(self.folder)), clear=True), patch.object(service, '_launch') as launch:
            with self.assertRaises(ValueError): service.start_upgrade(preview['token'], confirmed=True)
        state = service.snapshot()
        self.assertEqual(state['phase'], 'failed')
        self.assertFalse(state['locked'])
        self.assertFalse(state['recovery_required'])
        self.assertIsNone(service._reservation)
        host._component.assert_not_called()
        executor.assert_not_called(); acquire.assert_not_called(); release.assert_not_called(); launch.assert_not_called()

    def test_invalid_home_final_preflight_releases_reserved_upgrade_without_unknown(self):
        service, host, executor, acquire, release, preview = self.firmware_service_fixture()
        service._reservation = TOKEN
        service.state.update(busy=True, phase='upgrading', operation='upgrade')
        with patch.dict(os.environ, dict(HOME='relative-sdk-home', USERPROFILE=str(self.folder)), clear=True):
            service._upgrade(preview['device'], preview)
        state = service.snapshot()
        self.assertEqual(state['phase'], 'failed')
        self.assertFalse(state['locked'])
        self.assertFalse(state['recovery_required'])
        self.assertIsNone(service._reservation)
        host._component.assert_not_called()
        executor.assert_not_called(); acquire.assert_not_called()
        release.assert_called_once_with(TOKEN)

    def test_software_check_keeps_isolated_home_and_forwards_flags(self):
        calls = []
        def runner(argv, **options):
            calls.append((argv, options))
            text = 'version ' + native_cli.PINNED_VERSION if argv[-1] == '--version' else '--jsonl --no-zero --no-open --sn --timeout-s'
            return N(returncode=0, stdout=text)
        with tempfile.TemporaryDirectory(prefix='software-check-', dir=ROOT) as tmp:
            stage = Path(tmp)
            with patch.dict(os.environ, {'HOME': str(self.explicit), 'USERPROFILE': str(self.folder), 'PRIVATE_SYNTHETIC_TOKEN': 'not-forwarded'}):
                native_cli.NativeCLIStore(stage / 'store', runner=runner)._validate_software(stage / 'fake.exe', stage, calibration=True)
            self.assertEqual([argv[1:] for argv, _ in calls], [['--version'], ['--help'], ['viz', '--help'], ['calib', 'hand-model', '--help']])
            for argv, options in calls:
                child = options['env']
                self.assertEqual(child['HOME'], str(stage / 'software-check-home'))
                self.assertEqual(child['HOME'], child['USERPROFILE'])
                self.assertEqual(child['WUJI_NO_UPDATE_CHECK'], '1')
                self.assertEqual(child['WUJI_TELEMETRY_DISABLE'], '1')
                self.assertNotIn('PRIVATE_SYNTHETIC_TOKEN', child)
                self.assertEqual(options['cwd'], str(stage))
                self.assertFalse(options['shell'])
            self.assertFalse((stage / 'software-check-home').exists())


for _point in ('metadata', 'discovery_finite', 'discovery_cancellable', 'calibration_worker', 'viewer', 'firmware_read'):
    for _mode in ('missing', 'empty', 'explicit'):
        setattr(LaunchEnvironmentTests, 'test_' + _point + '_' + _mode,
                lambda self, point=_point, mode=_mode: self.verify_launch(point, mode))
    setattr(LaunchEnvironmentTests, 'test_' + _point + '_relative_home_rejected',
            lambda self, point=_point: self.verify_invalid_launch(point))


if __name__ == '__main__': unittest.main(verbosity=2)
