"""Failed worker construction/start must not strand software controls or locks.

All workers, software probes and runtime installers are inert doubles. No
native process, network, device or control environment is started.
"""
from contextlib import ExitStack
import copy
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import managed_runtime
import macos_runtime
import official_cli
import official_tools
import workbench_update


class Deferred:
    def __init__(self,target=None,args=(),**_):self.target=target;self.args=args;self.alive=False
    def start(self):self.alive=True
    def is_alive(self):return self.alive
    def join(self,timeout=None):pass
    def finish(self):
        try:self.target(*self.args)
        finally:self.alive=False


def failing(boundary):
    if boundary=='construct':return Mock(side_effect=RuntimeError('synthetic worker construction failed'))
    def create(**kwargs):
        worker=Deferred(**kwargs);worker.start=Mock(side_effect=RuntimeError('synthetic worker start failed'));return worker
    return create


class SoftwareLaunchRecovery(unittest.TestCase):
    def test_application_update_failure_is_truthful_and_retry_completes(self):
        for boundary in ('construct','start'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as folder:
                store=Mock();store.check.return_value=dict(available=False,latest_version='0.2.52')
                owner=workbench_update.WorkbenchUpdate(folder,dict(name='workbench',version='0.2.52'),store=store)
                with patch.object(workbench_update.threading,'Thread',failing(boundary)),self.assertRaisesRegex(RuntimeError,'synthetic worker'):
                    owner.start('check')
                self.assertFalse(owner.state['busy']);self.assertEqual(owner.state['phase'],'error')
                self.assertEqual(owner.state['error_stage'],'check');self.assertIn('synthetic worker',owner.state['error'])
                self.assertTrue(owner.cancel_and_wait());store.check.assert_not_called()
                with patch.object(workbench_update.threading,'Thread',Deferred):owner.start('check')
                owner.thread.finish()
                self.assertFalse(owner.snapshot()['busy']);self.assertEqual(owner.state['phase'],'up_to_date')

    def test_official_cli_failed_worker_is_removed_before_cancel_and_retry(self):
        for boundary in ('construct','start'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
                store=Mock();store.fetch_release.return_value={'tag_name':'v2026.10.26'}
                owner=official_cli.OfficialCLI(Path(folder),store=store,native_store=Mock())
                stack.enter_context(patch('bridge_config.load_config',return_value=dict(mode='local',cli='wuji')))
                stack.enter_context(patch('official_device_discovery.use_native',return_value=False))
                with patch.object(official_cli.threading,'Thread',failing(boundary)),self.assertRaisesRegex(RuntimeError,'synthetic worker'):
                    owner.start(check_only=True)
                self.assertFalse(owner.snapshot()['busy']);self.assertFalse(owner.state['checking']);self.assertFalse(owner.state['preparing'])
                self.assertEqual(owner.state['phase'],'error');self.assertIsNone(owner.thread)
                self.assertTrue(owner.cancel_and_wait());store.fetch_release.assert_not_called()
                with patch.object(official_cli.threading,'Thread',Deferred):owner.start(check_only=True)
                owner.thread.finish()
                self.assertEqual(owner.state['phase'],'checked');self.assertFalse(owner.snapshot()['busy'])

    def test_component_check_failure_retries_using_existing_read_only_probes(self):
        for boundary in ('construct','start'):
            with self.subTest(boundary=boundary),ExitStack() as stack:
                owner=official_tools.OfficialTools()
                stack.enter_context(patch('managed_runtime.status',return_value=dict(supported=True,installed=True,busy=False)))
                stack.enter_context(patch('managed_runtime.args',side_effect=lambda command:command))
                stack.enter_context(patch.object(official_tools,'target_versions',return_value=dict(cli_version='2026.10.12',sdk_version='2026.10.12')))
                probes=stack.enter_context(patch.object(official_tools.subprocess,'run',side_effect=[
                    SimpleNamespace(returncode=0,stdout=b'{"sdk_version":"2026.10.12","sdk_imported":true}'),
                    SimpleNamespace(returncode=0,stdout=b'wuji 2026.10.12')]))
                with patch.object(official_tools.threading,'Thread',failing(boundary)),self.assertRaisesRegex(RuntimeError,'synthetic worker'):
                    owner.start_check()
                self.assertFalse(owner.check['busy']);self.assertEqual(owner.check['status'],'failed')
                self.assertFalse(owner.check['available']);probes.assert_not_called()
                started=[]
                def capture(**kwargs):worker=Deferred(**kwargs);started.append(worker);return worker
                with patch.object(official_tools.threading,'Thread',capture):owner.start_check()
                started[0].finish()
                self.assertFalse(owner.check['busy']);self.assertTrue(owner.check['compatible'])

    def test_owned_runtime_launch_failure_releases_lock_without_installing(self):
        for module in (managed_runtime,macos_runtime):
            for boundary in ('construct','start'):
                with self.subTest(module=module.__name__,boundary=boundary),ExitStack() as stack:
                    lock=threading.Lock();state=dict(busy=False,stage='idle',error=None)
                    stack.enter_context(patch.object(module,'_lock',lock));stack.enter_context(patch.object(module,'_state',state))
                    stack.enter_context(patch.object(module,'status',side_effect=lambda:copy.deepcopy(state)))
                    probe_name='_image' if module is managed_runtime else 'verify_payload'
                    probe=stack.enter_context(patch.object(module,probe_name,side_effect=ValueError('synthetic missing payload')))
                    if module is macos_runtime:stack.enter_context(patch.object(module,'supported',return_value=True))
                    else:stack.enter_context(patch.object(module.sys,'platform','win32'))
                    process=stack.enter_context(patch.object(module.subprocess,'run'))
                    with patch.object(module.threading,'Thread',failing(boundary)),self.assertRaisesRegex(RuntimeError,'synthetic worker'):
                        module.start_install()
                    self.assertFalse(lock.locked());self.assertFalse(state['busy']);self.assertEqual(state['stage'],'failed')
                    self.assertIn('synthetic worker',state['error']);probe.assert_not_called();process.assert_not_called()
                    started=[]
                    def capture(**kwargs):worker=Deferred(**kwargs);started.append(worker);return worker
                    with patch.object(module.threading,'Thread',capture):module.start_install()
                    self.assertTrue(state['busy']);self.assertTrue(lock.locked())
                    started[0].finish()
                    self.assertFalse(state['busy']);self.assertFalse(lock.locked());self.assertEqual(state['stage'],'failed')
                    probe.assert_called_once();process.assert_not_called()

    def test_failed_application_startup_can_be_requested_again(self):
        for boundary in ('construct','start'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as folder:
                owner=workbench_update.WorkbenchUpdate(folder,dict(name='workbench',version='0.2.52'),store=Mock())
                with patch.dict('os.environ',{'WUJI_APP_AUTO_CHECK':'1'}),patch.object(owner,'snapshot',return_value={'supported':True}):
                    with patch.object(workbench_update.threading,'Thread',failing(boundary)),self.assertRaises(RuntimeError):owner.startup()
                    self.assertFalse(owner.started);self.assertFalse(owner.state['busy'])
                    with patch.object(workbench_update.threading,'Thread',Deferred):owner.startup()
                    self.assertTrue(owner.started);self.assertTrue(owner.thread.is_alive())

    def test_failed_cli_startup_can_be_requested_again(self):
        for boundary in ('construct','start'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as folder:
                owner=official_cli.OfficialCLI(Path(folder),store=Mock(),native_store=Mock())
                with patch.dict('os.environ',{'WUJI_CLI_AUTO_CHECK':'1'}),patch.object(owner,'snapshot',return_value={}):
                    with patch.object(official_cli.threading,'Thread',failing(boundary)),self.assertRaises(RuntimeError):owner.startup()
                    self.assertFalse(owner.started);self.assertIsNone(owner.thread);self.assertIsNone(owner.monitor)
                    with patch.object(official_cli.threading,'Thread',Deferred):owner.startup()
                    self.assertTrue(owner.started);self.assertTrue(owner.thread.is_alive());self.assertTrue(owner.monitor.is_alive())

    def test_cli_schedule_retry_does_not_duplicate_the_already_started_check(self):
        for boundary in ('construct','start'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as folder:
                owner=official_cli.OfficialCLI(Path(folder),store=Mock(),native_store=Mock());created=[]
                def factory(**kwargs):
                    if created:return failing(boundary)(**kwargs)
                    worker=Deferred(**kwargs);created.append(worker);return worker
                with patch.dict('os.environ',{'WUJI_CLI_AUTO_CHECK':'1'}),patch.object(owner,'snapshot',return_value={}):
                    with patch.object(official_cli.threading,'Thread',factory),self.assertRaises(RuntimeError):owner.startup()
                    original=owner.thread;self.assertTrue(original.is_alive());self.assertFalse(owner.started)
                    next_workers=[]
                    def capture(**kwargs):worker=Deferred(**kwargs);next_workers.append(worker);return worker
                    with patch.object(official_cli.threading,'Thread',capture):owner.startup()
                    self.assertIs(owner.thread,original);self.assertEqual(len(next_workers),1);self.assertIs(owner.monitor,next_workers[0])

    def test_cli_schedule_retry_preserves_a_check_that_already_completed(self):
        for boundary in ('construct','start'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as folder:
                owner=official_cli.OfficialCLI(Path(folder),store=Mock(),native_store=Mock());created=[]
                class Immediate(Deferred):
                    def start(self):super().start();self.finish()
                owner._work=Mock()
                def factory(**kwargs):
                    if created:return failing(boundary)(**kwargs)
                    worker=Immediate(**kwargs);created.append(worker);return worker
                with patch.dict('os.environ',{'WUJI_CLI_AUTO_CHECK':'1'}),patch.object(owner,'snapshot',return_value={}):
                    with patch.object(official_cli.threading,'Thread',factory),self.assertRaises(RuntimeError):owner.startup()
                    self.assertFalse(owner.thread.is_alive());self.assertTrue(owner.initial_check_started)
                    with patch.object(official_cli.threading,'Thread',Deferred):owner.startup()
                    self.assertTrue(owner.monitor.is_alive());owner._work.assert_called_once()


if __name__=='__main__':unittest.main()
