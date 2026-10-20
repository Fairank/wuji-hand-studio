"""Synthetic release fault tests; never launch an app, installer or SDK."""
import ast
from contextlib import redirect_stdout
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
QA = Path(os.environ.get('WUJI_RELEASE_QA_ROOT', str(ROOT.parents[1]/'outputs/workbench-upgrade-0219/release')))
spec = importlib.util.spec_from_file_location('release_recovery_ctl', ROOT/'scripts/workbenchctl.py')
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


class UpgradeRecoveryTests(unittest.TestCase):
    def setUp(self):
        QA.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=QA)
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.data = self.root/'data'
        self.data.mkdir()
        self.profile = self.data/'existing-user-calibration.json'
        self.profile.write_bytes(b'EXISTING USER DATA MUST SURVIVE')
        self.source = self.root/'HandWorkbench-0.2.19-windows-x64-setup.exe'
        self.payload = b'MZ SYNTHETIC INSTALLER; NOT EXECUTABLE\n'*100
        self.source.write_bytes(self.payload)
        self.digest = ctl.checksum(self.source)
        self.destination = self.data/'updates'/self.source.name
        self.client = Mock()
        self.info = dict(native=True, edition='workbench', data_dir=str(self.data))
        self.client.get.return_value = self.info
        self.client.call.return_value = dict(ok=True, prepared=True)
        platform = patch.object(ctl.sys, 'platform', 'win32')
        platform.start()
        self.addCleanup(platform.stop)
        installer = patch.object(ctl.subprocess, 'run', return_value=SimpleNamespace(returncode=0))
        self.installer = installer.start()
        self.addCleanup(installer.stop)

    def stage(self):
        return ctl.upgrade(self.client, self.source, self.digest, False)

    def bad_cache(self):
        self.destination.parent.mkdir()
        self.destination.write_bytes(b'OLD INTERRUPTED COPY')

    def assert_untouched_after_failure(self):
        self.assertEqual(self.destination.read_bytes(), b'OLD INTERRUPTED COPY')
        self.assertEqual(list(self.destination.parent.glob('*.partial')), [])
        self.assertEqual(self.profile.read_bytes(), b'EXISTING USER DATA MUST SURVIVE')
        self.client.call.assert_not_called()
        self.installer.assert_not_called()

    def test_corrupt_existing_cache_is_replaced_and_retry_is_reusable(self):
        self.bad_cache()
        self.assertTrue(self.stage()['ok'])
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertEqual(list(self.destination.parent.glob('*.partial')), [])
        with patch.object(ctl.shutil, 'copyfileobj', side_effect=AssertionError('Valid cache must not be copied again')):
            self.assertTrue(self.stage()['ok'])
        self.assertEqual([c.args[0] for c in self.client.call.call_args_list], ['prepare_update', 'prepare_update'])
        self.installer.assert_not_called()
        self.assertEqual(self.profile.read_bytes(), b'EXISTING USER DATA MUST SURVIVE')

    def test_copy_interruption_preserves_old_cache_and_can_retry(self):
        self.bad_cache()
        def interrupted(src, dst):
            dst.write(b'PARTIAL NEW BYTES')
            raise OSError('injected copy interruption')
        with patch.object(ctl.shutil, 'copyfileobj', side_effect=interrupted), self.assertRaisesRegex(OSError, 'interruption'):
            self.stage()
        self.assert_untouched_after_failure()
        self.assertTrue(self.stage()['ok'])
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_first_copy_interruption_never_creates_final_installer(self):
        with patch.object(ctl.shutil, 'copyfileobj', side_effect=OSError('injected copy failure')), self.assertRaises(OSError):
            self.stage()
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.glob('*.partial')), [])
        self.client.call.assert_not_called()
        self.installer.assert_not_called()

    def test_fsync_failure_preserves_old_cache_and_cleans_partial(self):
        self.bad_cache()
        with patch.object(ctl.os, 'fsync', side_effect=OSError('injected fsync failure')), self.assertRaisesRegex(OSError, 'fsync'):
            self.stage()
        self.assert_untouched_after_failure()

    def test_replace_failure_preserves_old_cache_and_cleans_partial(self):
        self.bad_cache()
        with patch.object(ctl.os, 'replace', side_effect=OSError('injected replace failure')), self.assertRaisesRegex(OSError, 'replace'):
            self.stage()
        self.assert_untouched_after_failure()

    def test_changed_copy_is_rejected_before_publish_or_prepare(self):
        self.bad_cache()
        with patch.object(ctl.shutil, 'copyfileobj', side_effect=lambda src, dst: dst.write(b'UNTRUSTED CHANGED COPY')), self.assertRaisesRegex(ValueError, 'changed while staging'):
            self.stage()
        self.assert_untouched_after_failure()

    def test_verified_bytes_are_synced_before_atomic_publish(self):
        self.bad_cache()
        synced = []
        original_fsync, original_replace = ctl.os.fsync, ctl.os.replace
        def fsync(fd):
            original_fsync(fd)
            synced.append(True)
        def replace(source, destination):
            self.assertTrue(synced)
            self.assertEqual(Path(source).suffix, '.partial')
            self.assertEqual(Path(source).read_bytes(), self.payload)
            self.assertEqual(Path(destination).read_bytes(), b'OLD INTERRUPTED COPY')
            original_replace(source, destination)
        with patch.object(ctl.os, 'fsync', side_effect=fsync), patch.object(ctl.os, 'replace', side_effect=replace) as publish:
            self.stage()
        publish.assert_called_once()
        self.assertEqual(self.destination.read_bytes(), self.payload)

    def test_prepare_failure_keeps_verified_cache_for_retry_without_closing(self):
        self.client.call.side_effect = RuntimeError('injected prepare failure')
        with self.assertRaisesRegex(RuntimeError, 'prepare failure'):
            self.stage()
        self.assertEqual(self.destination.read_bytes(), self.payload)
        self.assertEqual([c.args[0] for c in self.client.call.call_args_list], ['prepare_update'])
        self.client.call.side_effect = None
        with patch.object(ctl.shutil, 'copyfileobj', side_effect=AssertionError('Retry must reuse verified bytes')):
            self.stage()
        self.installer.assert_not_called()

    def test_closed_app_does_not_allow_tampered_staged_installer(self):
        def call(operation, **values):
            if operation == 'close_idle':
                self.destination.write_bytes(b'TAMPERED AFTER PREPARE')
            return dict(ok=True)
        self.client.call.side_effect = call
        self.client.get.side_effect = [self.info, ConnectionResetError('synthetic server exit')]
        with self.assertRaisesRegex(ValueError, 'Staged installer changed'):
            ctl.upgrade(self.client, self.source, self.digest, True)
        self.installer.assert_not_called()

    def test_failed_close_does_not_start_installer(self):
        def call(operation, **values):
            if operation == 'close_idle':
                raise RuntimeError('synthetic app still busy')
            return dict(ok=True)
        self.client.call.side_effect = call
        with self.assertRaisesRegex(RuntimeError, 'still busy'):
            ctl.upgrade(self.client, self.source, self.digest, True)
        self.installer.assert_not_called()

    def test_app_exit_timeout_does_not_start_installer(self):
        with patch.object(ctl.time, 'monotonic', side_effect=[0., 21.]), self.assertRaisesRegex(RuntimeError, 'did not exit'):
            ctl.upgrade(self.client, self.source, self.digest, True)
        self.installer.assert_not_called()

    def test_installer_error_is_not_reported_as_success(self):
        self.client.get.side_effect = [self.info, ConnectionResetError('synthetic server exit')]
        self.installer.return_value = SimpleNamespace(returncode=7)
        with self.assertRaisesRegex(RuntimeError, 'code 7'):
            ctl.upgrade(self.client, self.source, self.digest, True)
        self.installer.assert_called_once()


class BuildIdentityTests(unittest.TestCase):
    def setUp(self):
        QA.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=QA)
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        (self.root/'src').mkdir()
        self.edition = json.loads((ROOT/'src/edition.json').read_text(encoding='utf-8'))
        (self.root/'src/edition.json').write_text(json.dumps(self.edition), encoding='utf-8')
        # Execute the actual build driver in a disposable tree with all child
        # processes mocked, package-only and binary-only. No PyInstaller import.
        self.script = self.root/'build.py'
        self.script.write_bytes((ROOT/'build.py').read_bytes())
        binary = self.root/'dist/HandWorkbench/HandWorkbench.exe'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'SYNTHETIC CACHED EXECUTABLE; NEVER EXECUTED')

    def run_build(self, identity=None, error=None, missing=False):
        previous = Path.cwd()
        def run(command, **kwargs):
            if error is not None:
                raise error
            self.assertEqual(command[:3], [str(self.root/'dist/HandWorkbench/HandWorkbench.exe'), '--self-check', '--self-check-output'])
            self.assertIs(kwargs.get('check'), True)
            if not missing:
                Path(command[3]).write_text(identity, encoding='utf-8')
            return SimpleNamespace(stdout=None, returncode=0)
        try:
            with patch.object(sys, 'argv', [str(self.script), '--package-only', '--binary-only']), patch.object(sys, 'platform', 'win32'), patch('importlib.metadata.distributions', return_value=[]), patch.object(subprocess, 'run', side_effect=run) as child, redirect_stdout(io.StringIO()):
                self.child = child
                return runpy.run_path(str(self.script), run_name='__main__')
        finally:
            os.chdir(previous)

    def assert_no_package(self):
        self.assertEqual(list(self.root.glob('dist/*.zip')), [])
        self.assertEqual(list(self.root.glob('build/package-*')), [])
        self.assertEqual(list(self.root.glob('build/identity-*')), [])

    def identity(self, **changes):
        row = dict(ok=True, edition=self.edition['name'], version=self.edition['version'])
        row.update(changes)
        return json.dumps(row)

    def test_matching_cache_without_build_directory_accepts_file_identity_and_no_stdout(self):
        self.assertFalse((self.root/'build').exists())
        with self.assertRaises(SystemExit) as exit_result:
            self.run_build(self.identity())
        self.assertEqual(exit_result.exception.code, 0)
        self.child.assert_called_once()
        self.assert_no_package()

    def test_wrong_edition_or_version_or_failed_identity_is_rejected(self):
        for changes in (dict(edition='WRONG_EDITION'), dict(version='0.0.0'), dict(ok=False), dict(version=None)):
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, 'differs'):
                self.run_build(self.identity(**changes))
            self.assert_no_package()

    def test_legacy_identity_without_version_is_rejected(self):
        row = dict(ok=True, edition=self.edition['name'])
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            self.run_build(json.dumps(row))

    def test_empty_or_malformed_self_check_file_cannot_package(self):
        for identity in ('', 'invalid-json'):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.run_build(identity)
            self.assert_no_package()

    def test_missing_self_check_file_cannot_package(self):
        with self.assertRaises((OSError, ValueError, RuntimeError)):
            self.run_build(missing=True)
        self.assert_no_package()

    def test_self_check_process_failure_cannot_package(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_build(error=subprocess.CalledProcessError(2, ['SYNTHETIC SELF CHECK']))
        self.assert_no_package()

    def test_source_self_check_emits_selected_release_version(self):
        tree = ast.parse((ROOT/'src/desktop.py').read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'self_check')
        result_file = self.root/'self-check-result.json'
        namespace = dict(json=json, Path=Path,
                         sys=SimpleNamespace(argv=['--self-check', '--self-check-output', str(result_file)], stdout=None),
                         EDITION=self.edition)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT/'src/desktop.py'), 'exec'), namespace)
        load = Mock(return_value=SimpleNamespace(nu=20))
        profiles = {'hand2_left': {}, 'hand2_right': {}}
        modules = {
            'pose_view': SimpleNamespace(load_left_model=load),
            'gesture_library': SimpleNamespace(catalog=lambda: dict(actions=list(range(40)))),
            'bridge_config': SimpleNamespace(validate=lambda config: None, DEFAULT={}),
            'numpy': SimpleNamespace(),
            'device_profiles': SimpleNamespace(PROFILES=profiles, load_native_model=load),
        }
        output = io.StringIO()
        with patch.dict(sys.modules, modules), redirect_stdout(output):
            self.assertEqual(namespace['self_check'](), 0)
        self.assertEqual(output.getvalue(), '')
        identity = json.loads(result_file.read_text(encoding='utf-8'))
        self.assertEqual(identity['edition'], self.edition['name'])
        self.assertEqual(identity['version'], self.edition['version'])
        self.assertIs(identity['ok'], True)
        self.assertIs(identity['hardware_connected'], False)
        self.assertEqual(load.call_count, len(profiles))


if __name__ == '__main__':
    unittest.main()
