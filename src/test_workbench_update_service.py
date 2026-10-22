"""Application-update orchestration only: no network, app, installer or SDK.

The service uses real isolated staging files with non-executable fixture bytes.
The HTTP methods are compiled from their real AST without importing the device
controller module; all services and native windows are mocks.
"""
import ast
from contextlib import contextmanager, ExitStack
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import cli_coordination
import native_desktop
import runtime_paths
import workbench_update as module


EDITION = {'name': 'workbench', 'version': '0.2.29'}
VERSION = '0.2.30'
NAME = 'HandWorkbench-' + VERSION + '-windows-x64-setup.exe'
PAYLOAD = b'MZ SYNTHETIC UPDATE FIXTURE - DO NOT EXECUTE\n'


class DeferredThread:
    def __init__(self, target=None, args=(), **_kwargs):
        self.target, self.args = target, args
        self.started = False
        self.alive = False
        self.joined = []

    def start(self):
        self.started = self.alive = True

    def run_to_end(self):
        try:
            self.target(*self.args)
        finally:
            self.alive = False

    def join(self, timeout=None):
        self.joined.append(timeout)

    def is_alive(self):
        return self.alive


class ServiceTests(unittest.TestCase):
    @contextmanager
    def environment(self, supported=True):
        root = Path(__file__).resolve().parents[3]/'outputs/cli-workspace-0229/update-service'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder, ExitStack() as stack:
            data = Path(folder)/'data'
            data.mkdir()
            resources = Path(folder)/'resources'
            resources.mkdir()
            (resources/'workbench_update_apply.ps1').write_text('# synthetic, never executed', encoding='utf-8')
            executable = Path(folder)/'installed/HandWorkbench.exe'
            executable.parent.mkdir()
            executable.write_bytes(b'NOT AN APPLICATION')
            stack.enter_context(patch.dict(os.environ, WUJI_OFFICIAL_CLI_DATA=str(data/'coordination')))
            stack.enter_context(patch.object(module.sys, 'platform', 'win32' if supported else 'linux'))
            stack.enter_context(patch.object(module.sys, 'frozen', supported, create=True))
            stack.enter_context(patch.object(module.sys, 'executable', str(executable)))
            stack.enter_context(patch.object(runtime_paths, 'RESOURCE', resources))
            stack.enter_context(patch.object(module.threading, 'Thread', DeferredThread))
            helper = SimpleNamespace(poll=Mock(return_value=None))
            popen = stack.enter_context(patch.object(module.subprocess, 'Popen', return_value=helper))
            host = Mock()
            host.origin = 'http://127.0.0.1:8781'
            host.info.return_value = dict(native=True, edition=EDITION['name'], pid=os.getpid(), data_dir=str(data))
            idle = Mock()
            store = Mock()
            latest = dict(available=True, latest_version=VERSION, release={'synthetic': True})
            store.check.return_value = copy.deepcopy(latest)
            source = data/'updates/versions'/VERSION/NAME
            prepared = dict(version=VERSION, sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                            installer=str(source), repository=module.REPOSITORY, size=len(PAYLOAD))
            store.prepare.return_value = copy.deepcopy(prepared)
            service = module.WorkbenchUpdate(data, dict(EDITION), idle_check=idle, desktop=lambda:host, store=store)
            yield SimpleNamespace(service=service, data=data, source=source, prepared=prepared,
                resources=resources, executable=executable, host=host, idle=idle, store=store,
                latest=latest, helper=helper, popen=popen)

    def ready(self, env):
        env.source.parent.mkdir(parents=True)
        env.source.write_bytes(PAYLOAD)
        env.service.latest = copy.deepcopy(env.latest)
        env.service.state.update(phase='ready', prepared=copy.deepcopy(env.prepared),
                                 latest_version=VERSION, available=True)

    def test_construction_and_snapshot_do_not_download_or_start_programs(self):
        with self.environment() as env:
            state = env.service.snapshot()
            self.assertEqual(state['phase'], 'idle')
            self.assertFalse((env.data/'updates').exists())
            env.store.check.assert_not_called()
            env.store.prepare.assert_not_called()
            env.popen.assert_not_called()
            env.host.stop_and_close.assert_not_called()

    def test_check_and_prepare_are_asynchronous_without_closing_sessions(self):
        with self.environment() as env:
            checking = env.service.start('check')
            self.assertEqual(checking['phase'], 'checking')
            self.assertTrue(checking['busy'])
            env.store.check.assert_not_called()
            env.service.thread.run_to_end()
            self.assertEqual(env.service.snapshot()['phase'], 'available')
            env.store.check.assert_called_once_with(cancel_event=env.service.closed)
            downloading = env.service.start('prepare')
            self.assertEqual(downloading['phase'], 'downloading')
            env.store.prepare.assert_not_called()
            env.service.thread.run_to_end()
            self.assertEqual(env.service.snapshot()['prepared'], env.prepared)
            env.store.prepare.assert_called_once_with(env.latest, cancel_event=env.service.closed)
            env.host.stop_and_close.assert_not_called()
            env.popen.assert_not_called()

    def test_invalid_prepare_and_busy_operations_never_start_another_worker(self):
        with self.environment() as env:
            for operation in ('prepare', 'install', '', None):
                with self.subTest(operation=operation), self.assertRaises(ValueError):
                    env.service.start(operation)
            env.service.start('check')
            first_thread = env.service.thread
            for operation in ('check', 'prepare'):
                with self.assertRaisesRegex(ValueError, 'busy'):
                    env.service.start(operation)
            self.assertIs(env.service.thread, first_thread)
            env.store.check.assert_not_called()
            env.store.prepare.assert_not_called()

    def test_check_and_download_failures_preserve_prepared_payload_for_retry(self):
        with self.environment() as env:
            self.ready(env)
            for operation, method in [('check', env.store.check), ('prepare', env.store.prepare)]:
                with self.subTest(operation=operation):
                    method.side_effect = OSError('synthetic update failure')
                    env.service.start(operation)
                    env.service.thread.run_to_end()
                    state = env.service.snapshot()
                    self.assertEqual(state['phase'], 'error')
                    self.assertFalse(state['busy'])
                    self.assertEqual(state['prepared'], env.prepared)
                    self.assertEqual(env.source.read_bytes(), PAYLOAD)
                    method.side_effect = None
            env.service.start('check')
            env.service.thread.run_to_end()
            self.assertEqual(env.service.snapshot()['phase'], 'ready')

    def test_startup_checks_once_and_honors_child_opt_out_and_platform(self):
        with self.environment() as env, patch.dict(os.environ, WUJI_FLEET_ID='', WUJI_APP_AUTO_CHECK='1'):
            env.service.startup()
            env.service.startup()
            self.assertTrue(env.service.started)
            self.assertEqual(env.service.state['phase'], 'checking')
            env.service.thread.run_to_end()
            env.store.check.assert_called_once()
        for key, value in [('WUJI_FLEET_ID', 'synthetic-child'), ('WUJI_APP_AUTO_CHECK', '0')]:
            with self.subTest(key=key), self.environment() as env, patch.dict(os.environ, {key:value}):
                env.service.startup()
                self.assertIsNone(env.service.thread)
        with self.environment(supported=False) as env:
            env.service.startup()
            self.assertIsNone(env.service.thread)

    def test_cancel_wait_is_bounded_and_only_signals_download_worker(self):
        with self.environment() as env:
            env.service.start('check')
            self.assertFalse(env.service.cancel_and_wait(timeout=.1))
            self.assertEqual(env.service.thread.joined, [.1])
            self.assertTrue(env.service.closed.is_set())
            env.service.thread.alive = False
            self.assertTrue(env.service.cancel_and_wait(timeout=.1))
            env.popen.assert_not_called()
            env.host.stop_and_close.assert_not_called()

    def test_apply_unsupported_busy_or_unprepared_never_stages_or_runs(self):
        with self.environment(supported=False) as env:
            self.ready(env)
            with self.assertRaisesRegex(ValueError, 'installed Windows'):
                env.service.apply()
            env.popen.assert_not_called()
        with self.environment() as env:
            with self.assertRaisesRegex(ValueError, 'Download'):
                env.service.apply()
            self.ready(env)
            env.service.state['busy'] = True
            with self.assertRaisesRegex(ValueError, 'Download'):
                env.service.apply()
            self.assertFalse((env.data/'updates'/NAME).exists())
            env.popen.assert_not_called()

    def test_active_other_workspace_and_cli_reservation_block_apply_without_closing(self):
        with self.environment() as env:
            self.ready(env)
            env.idle.side_effect = ValueError('Another workspace glove is active')
            self.assertFalse(env.service.snapshot()['can_apply'])
            with self.assertRaisesRegex(ValueError, 'workspace'):
                env.service.apply()
            env.popen.assert_not_called()
            env.host.stop_and_close.assert_not_called()
            self.assertFalse((env.data/'updates'/NAME).exists())
            env.idle.side_effect = None
            with cli_coordination.gate():
                token = cli_coordination.reserve('calibration')
            try:
                with self.assertRaisesRegex(ValueError, 'CLI session'):
                    env.service.apply()
            finally:
                with cli_coordination.gate():
                    cli_coordination.release(token)
            env.popen.assert_not_called()
            self.assertEqual(env.source.read_bytes(), PAYLOAD)

    def test_stage_rejects_unowned_path_repository_bad_hash_or_non_exe(self):
        with self.environment() as env:
            self.ready(env)
            outside = env.data.parent/'unowned.exe'
            outside.write_bytes(PAYLOAD)
            variants = [dict(env.prepared, installer=str(outside)),
                        dict(env.prepared, repository='Other/Repository'),
                        dict(env.prepared, sha256='0'*64),
                        dict(env.prepared, version='../0.2.30')]
            for prepared in variants:
                with self.subTest(prepared=prepared), self.assertRaises(ValueError):
                    env.service._stage_installer(prepared)
            env.source.write_bytes(b'NOT WINDOWS')
            changed = dict(env.prepared, sha256=hashlib.sha256(b'NOT WINDOWS').hexdigest())
            with self.assertRaisesRegex(ValueError, 'Windows installer'):
                env.service._stage_installer(changed)
            env.popen.assert_not_called()

    def test_copy_time_hash_change_fails_without_touching_old_staged_installer(self):
        with self.environment() as env:
            self.ready(env)
            destination = env.data/'updates'/NAME
            destination.write_bytes(b'PREVIOUS VERIFIED INSTALLER')
            def corrupt(_source, output):
                output.write(PAYLOAD + b'CORRUPT')
            with patch.object(module.shutil, 'copyfileobj', side_effect=corrupt):
                with self.assertRaisesRegex(ValueError, 'staging'):
                    env.service.apply()
            self.assertEqual(destination.read_bytes(), b'PREVIOUS VERIFIED INSTALLER')
            self.assertEqual(env.service.state['prepared'], env.prepared)
            self.assertEqual(list(destination.parent.glob('*.partial')), [])
            env.popen.assert_not_called()

    def test_host_identity_mismatch_never_starts_helper(self):
        with self.environment() as env:
            self.ready(env)
            for change in [dict(native=False), dict(edition='other'), dict(pid=os.getpid()+1),
                           dict(data_dir=str(env.data.parent/'wrong-data'))]:
                with self.subTest(change=change):
                    info = dict(native=True, edition='workbench', pid=os.getpid(), data_dir=str(env.data))
                    env.host.info.return_value = dict(info, **change)
                    with self.assertRaisesRegex(ValueError, 'identity'):
                        env.service.apply()
            env.popen.assert_not_called()
            self.assertEqual(env.source.read_bytes(), PAYLOAD)

    def test_apply_binds_hidden_helper_plan_same_data_and_exact_installer(self):
        with self.environment() as env, patch.dict(os.environ, PSMODULEPATH='synthetic-untrusted-modules'):
            self.ready(env)
            state = env.service.apply()
            self.assertEqual(state['phase'], 'applying')
            self.assertTrue(state['busy'])
            command = env.popen.call_args.args[0]
            kwargs = env.popen.call_args.kwargs
            self.assertEqual(command[-4:-2], ['-File', str(env.resources/'workbench_update_apply.ps1')])
            self.assertEqual(command[-2], '-PlanFile')
            self.assertFalse(any(k.upper()=='PSMODULEPATH' for k in kwargs['env']))
            self.assertTrue(kwargs['creationflags'] & module.subprocess.CREATE_NO_WINDOW)
            self.assertNotIn('shell', kwargs)
            plan = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
            self.assertEqual(plan['app_id'], module.APP_ID)
            self.assertEqual(plan['expected_app_pid'], os.getpid())
            self.assertEqual(plan['executable'], str(env.executable))
            self.assertEqual(plan['data_dir'], str(env.data))
            self.assertEqual(plan['installer'], str(env.data/'updates'/NAME))
            self.assertEqual(plan['sha256'], env.prepared['sha256'])
            self.assertEqual(plan['repository'], module.REPOSITORY)
            self.assertEqual(plan['origin'], env.host.origin)
            self.assertEqual(Path(plan['result_file']), env.service.result_file)
            self.assertEqual((env.data/'updates'/NAME).read_bytes(), PAYLOAD)
            self.assertEqual(env.service.state['prepared'], env.prepared)
            env.host.stop_and_close.assert_not_called()

    def test_missing_helper_or_spawn_failure_can_retry_prepared_same_directory(self):
        with self.environment() as env:
            self.ready(env)
            script = env.resources/'workbench_update_apply.ps1'
            script.unlink()
            with self.assertRaisesRegex(ValueError, 'updater is missing'):
                env.service.apply()
            script.write_text('# synthetic', encoding='utf-8')
            env.popen.side_effect = OSError('synthetic helper spawn failed')
            with self.assertRaisesRegex(OSError, 'spawn failed'):
                env.service.apply()
            self.assertFalse(env.service.state['busy'])
            self.assertEqual(env.service.state['prepared'], env.prepared)
            self.assertEqual(env.source.read_bytes(), PAYLOAD)
            env.popen.side_effect = None
            env.service.apply()
            self.assertEqual(env.service.state['phase'], 'applying')
            self.assertEqual(list((env.data/'updates').glob('*.partial')), [])
            self.assertEqual(list((env.data/'updates/versions').iterdir()), [env.source.parent])

    def test_reported_helper_failure_keeps_verified_package_and_can_retry(self):
        with self.environment() as env:
            self.ready(env)
            env.service.apply()
            old_token = env.service.apply_token
            env.service.result_file.write_text(json.dumps(dict(token=old_token, phase='error', error='synthetic preflight failed')), encoding='utf-8')
            failed = env.service.snapshot()
            self.assertEqual(failed['phase'], 'error')
            self.assertFalse(failed['busy'])
            self.assertEqual(failed['prepared'], env.prepared)
            self.assertIn('preflight failed', failed['error'])
            env.service.apply()
            self.assertNotEqual(env.service.apply_token, old_token)
            self.assertEqual(env.service.state['phase'], 'applying')
            self.assertEqual(env.source.read_bytes(), PAYLOAD)

    def test_dead_helper_missing_result_is_retryable_not_permanently_busy(self):
        with self.environment() as env:
            self.ready(env)
            env.service.apply()
            env.helper.poll.return_value = 1
            state = env.service.snapshot()
            self.assertEqual(state['phase'], 'error')
            self.assertFalse(state['busy'])
            self.assertEqual(state['prepared'], env.prepared)
            env.service.apply()
            self.assertEqual(env.popen.call_count, 2)

    def test_dead_helper_invalid_or_unmatched_result_is_retryable_and_keeps_package(self):
        for report in ([], {'token':'wrong', 'phase':'error', 'error':'stale'},
                       {'phase':'error', 'error':'missing token'}):
            with self.subTest(report=report), self.environment() as env:
                self.ready(env)
                env.service.apply()
                env.helper.poll.return_value = 1
                env.service.result_file.write_text(json.dumps(report), encoding='utf-8')
                state = env.service.snapshot()
                self.assertEqual(state['phase'], 'error')
                self.assertFalse(state['busy'])
                self.assertEqual(state['prepared'], env.prepared)
                self.assertEqual(env.source.read_bytes(), PAYLOAD)
                env.service.apply()
                self.assertEqual(env.popen.call_count, 2)


class DesktopCloseGuardTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        host = native_desktop.NativeDesktop()
        state = dict(connection='disconnected', hardware={'active':False}, glove={'busy':False})
        controller = SimpleNamespace(lock=threading.RLock(), snapshot=Mock(return_value=state),
            doctor=SimpleNamespace(snapshot=Mock(return_value={'running':False})),
            official_cli=SimpleNamespace(idle_check=Mock()), desktop_closing=False)
        fleet = SimpleNamespace(snapshot=Mock(return_value=[]), close=Mock(return_value={'ok':True}))
        updates = SimpleNamespace(cancel_and_wait=Mock(return_value=True))
        host.server = SimpleNamespace(controller=controller, _fleet=True, fleet=fleet,
                                      _workbench_updates=True, updates=updates)
        host.window = SimpleNamespace(destroy=Mock())
        held = []
        @contextmanager
        def guard():
            held.append(True)
            try:
                yield
            finally:
                held.pop()
        with patch.object(cli_coordination, 'gate', guard), \
                patch.object(native_desktop, 'close_sessions', return_value={'ok':True}) as close, \
                patch.object(native_desktop.threading, 'Timer') as timer, \
                patch.object(native_desktop.logging, 'exception'):
            yield SimpleNamespace(host=host, controller=controller, fleet=fleet, updates=updates,
                                  held=held, close=close, timer=timer, state=state)

    def test_update_close_idle_keeps_global_gate_during_all_workspace_check_and_close(self):
        with self.environment() as env:
            def require_held(*_args):
                self.assertTrue(env.held)
            env.controller.official_cli.idle_check.side_effect = require_held
            env.close.side_effect = lambda _: require_held() or {'ok':True}
            self.assertTrue(env.host.stop_and_close(require_idle=True)['ok'])
            env.controller.official_cli.idle_check.assert_called_once()
            env.fleet.close.assert_called_once()
            self.assertTrue(env.host.allow_close)
            env.timer.assert_called_once()

    def test_active_or_unavailable_other_workspace_cannot_be_closed_by_update(self):
        for problem in (ValueError('other glove active'), OSError('workspace state unavailable')):
            with self.subTest(problem=problem), self.environment() as env:
                env.controller.official_cli.idle_check.side_effect = problem
                self.assertFalse(env.host.stop_and_close(require_idle=True)['ok'])
                env.fleet.close.assert_not_called()
                env.close.assert_not_called()
                env.timer.assert_not_called()
                self.assertFalse(env.controller.desktop_closing)

    def test_download_shutdown_failure_or_unknown_fleet_state_never_closes(self):
        with self.environment() as env:
            env.updates.cancel_and_wait.return_value = False
            self.assertFalse(env.host.stop_and_close(require_idle=True)['ok'])
            env.controller.official_cli.idle_check.assert_not_called()
            env.fleet.close.assert_not_called()
            env.close.assert_not_called()
        with self.environment() as env:
            env.fleet.snapshot.return_value = [dict(connection='disconnected', hardware=None)]
            self.assertFalse(env.host.stop_and_close(require_idle=True)['ok'])
            env.fleet.close.assert_not_called()
            env.close.assert_not_called()


def handler_method(name):
    # Compile the actual bounded bridge without importing console_agent or an
    # SDK. No source rewriting and no socket/server are involved.
    tree = ast.parse(Path(__file__).with_name('console_server.py').read_text(encoding='utf-8'))
    handler = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name=='Handler')
    method = next(node for node in handler.body if isinstance(node, ast.FunctionDef) and node.name==name)
    namespace = dict(json=json, secrets=secrets, os=os, re=re, ORIGIN='http://127.0.0.1:8781',
                     PORT=8781, urlsplit=urlsplit, parse_qs=parse_qs)
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'actual-console-handler', 'exec'), namespace)
    return namespace[name]


class UpdateAPIBridgeTests(unittest.TestCase):
    def handler(self, payload=None):
        raw = json.dumps(payload).encode() if payload is not None else b''
        update = SimpleNamespace(start=Mock(return_value={'phase':'checking'}),
            apply=Mock(return_value={'phase':'applying'}), snapshot=Mock(return_value={'phase':'idle'}))
        headers = {'Host':'127.0.0.1:8781', 'Origin':'http://127.0.0.1:8781',
            'X-Console-Token':'synthetic-token', 'Content-Length':str(len(raw)), 'Content-Type':'application/json'}
        handler = SimpleNamespace(path='/api/action', headers=headers, allowed_host=lambda:True,
            rfile=io.BytesIO(raw), reply=Mock(), server=SimpleNamespace(updates=update,
                controller=SimpleNamespace(csrf='synthetic-token', action=Mock())))
        handler._task_view_headers=MethodType(handler_method('_task_view_headers'),handler)
        return handler, update

    def test_get_status_and_all_three_actions_use_update_service_only(self):
        handler, update = self.handler()
        handler.path = '/api/workbench-update'
        handler_method('do_GET')(handler)
        update.snapshot.assert_called_once_with()
        handler.reply.assert_called_once_with(200, {'phase':'idle'})
        for suffix in ('check', 'prepare', 'apply'):
            with self.subTest(suffix=suffix):
                handler, update = self.handler({'name':'workbench_update_'+suffix})
                handler_method('do_POST')(handler)
                if suffix=='apply':
                    update.apply.assert_called_once_with()
                    update.start.assert_not_called()
                else:
                    update.start.assert_called_once_with(suffix)
                    update.apply.assert_not_called()
                self.assertEqual(handler.reply.call_args.args[0], 200)
                handler.server.controller.action.assert_not_called()

    def test_missing_token_or_foreign_origin_rejects_apply_before_service(self):
        for change in [{'X-Console-Token':'wrong'}, {'Origin':'http://evil.invalid'}]:
            with self.subTest(change=change):
                handler, update = self.handler({'name':'workbench_update_apply'})
                handler.headers.update(change)
                handler_method('do_POST')(handler)
                self.assertEqual(handler.reply.call_args.args[0], 403)
                update.apply.assert_not_called()

    def test_unscoped_embedded_task_cannot_apply_a_global_update(self):
        handler, update=self.handler({'name':'workbench_update_apply'})
        handler.headers['Referer']='http://127.0.0.1:8781/?embedded=1&task_view=1&task_id='+'a'*24
        handler_method('do_POST')(handler)
        self.assertEqual(handler.reply.call_args.args[0],400)
        update.apply.assert_not_called();update.start.assert_not_called()
        handler.server.controller.action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
