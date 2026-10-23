"""Independent firmware host boundaries; all hardware/CLI calls are fakes.

Actual ConPTY API proof is a separate `upgrade --help` software-only artifact.
These tests never run discovery, query, firmware writes, or motor operations.
"""
import copy
import io
import json
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

import cli_coordination
import firmware_host
from firmware_host import FirmwareHost, enforce_maintenance


SN = 'WH2KA01000000001'
TOKEN = 'a' * 32


class FakeProcess:
    pid = 123456789
    def __init__(self, *, alive=False, status=0, read_error=None):
        self.alive, self.status, self.read_error = alive, status, read_error
        self.pty = N(cancel_io=Mock())
        self.writes, self.sizes = [], []
        self.closed = False
    def isalive(self):return self.alive
    @property
    def exitstatus(self):return self.status
    def read(self, size):
        if self.read_error:raise self.read_error
        raise EOFError()
    def write(self, text):self.writes.append(text)
    def setwinsize(self, rows, cols):self.sizes.append((rows, cols))
    def close(self):self.closed=True
    def terminate(self):raise AssertionError('Firmware child must never be terminated')


class FirmwareHostTests(unittest.TestCase):
    def setUp(self):
        # Workspace location avoids Windows sandbox Temp virtualization denying
        # same-directory atomic replace; this is isolated and always removed.
        self.tmp = TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.controller = N(official_cli=N(idle_check=Mock()), calibration_devices=Mock(return_value=[
            dict(serial=SN, sn=SN, kind='hand', generation='hand2', side='right',
                 stale=False, selectable=True, observed_at=1000, conflict=False)]))
        self.host = FirmwareHost(self.controller, folder=self.folder)
        self.component = dict(binary='verified-wuji.exe', version='2026.10.12', binary_sha256='verified-digest')
        self.marker = dict(kind='firmware', token=TOKEN, serial=SN, pid=55, start='owner',
                           child_pid=123456789, child_start='child')

    def write_marker(self, **changes):
        marker = dict(self.marker, **changes)
        cli_coordination.atomic(self.folder/'session.json', marker)
        return marker

    def test_only_exact_selected_sn_read_and_interactive_commands_are_accepted(self):
        invalid = [[], ['upgrade'], ['upgrade', '--all'], ['upgrade', '--type', 'wuji_glove'],
                   ['upgrade', '--sn', SN, '--yes'], ['upgrade', '--sn', SN, '--force'],
                   ['upgrade', '--sn', SN, '--file', 'raw.bin'], ['upgrade', '--sn', SN, '--to', '2.5.1'],
                   ['upgrade', '--sn', SN, '--check', '--json', '--all'],
                   ['upgrade', '--sn', SN+'\n', '--check', '--json'], ['get', '--sn', SN]]
        with patch.object(self.host, '_component') as component:
            for argv in invalid:
                with self.subTest(argv=argv), self.assertRaises(ValueError):
                    self.host(argv, mutating=True, interactive=True)
        component.assert_not_called()

    def test_read_queries_are_bounded_and_never_mutating(self):
        runner = Mock(return_value=N(returncode=0,stdout='opaque official JSON',stderr=''))
        self.host.runner = runner
        with patch.object(self.host, '_component', return_value=self.component):
            result = self.host(['upgrade','--sn',SN,'--check','--json'])
        self.assertTrue(result['exited'])
        self.assertEqual(runner.call_args.args[0], ['verified-wuji.exe','upgrade','--sn',SN,'--check','--json'])
        self.assertEqual(runner.call_args.kwargs['timeout'],40)
        self.assertEqual(runner.call_args.kwargs['env']['WUJI_NO_UPDATE_CHECK'],'1')
        self.assertFalse(runner.call_args.kwargs.get('shell',False))

    def test_server_inventory_identity_is_required_not_client_metadata(self):
        self.assertEqual(self.host.device(SN)['serial'],SN)
        self.controller.calibration_devices.return_value=[]
        with self.assertRaises(ValueError):self.host.device(SN)
        self.controller.calibration_devices.return_value=[dict(serial=SN,conflict=True)]
        with self.assertRaises(ValueError):self.host.device(SN)
        with self.assertRaises(ValueError):self.host.device(dict(serial=SN))

    def test_shared_lease_blocks_another_workspace_but_stop_remains_available(self):
        self.write_marker()
        for name in ('connect','glove_connect','hardware_start','demo_start','session_close',
                     'official_viz_start','calibration_start','retarget_apply','runtime_install'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                enforce_maintenance(dict(name=name), self.folder)
        for name in ('hardware_stop','glove_stop','disconnect','glove_disconnect','program_stop','group_stop'):
            enforce_maintenance(dict(name=name), self.folder)

    def test_dead_owner_does_not_discard_unknown_flash_marker(self):
        self.write_marker()
        with patch('cli_coordination.identity',return_value=None):
            marker = cli_coordination.reservation(self.folder)
            self.assertEqual(marker['token'],TOKEN)
            with self.assertRaises(ValueError):
                cli_coordination.reserve('scan',self.folder)

    def test_unreadable_reservation_is_not_permission_to_start_or_close(self):
        (self.folder/'session.json').write_text('{invalid marker',encoding='utf-8')
        self.assertEqual(cli_coordination.reservation(self.folder),dict(kind='unknown'))
        for name in ('connect','glove_connect','hardware_start','demo_start','session_close'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                enforce_maintenance(dict(name=name),self.folder)
        # Safe stop remains possible without interpreting malformed ownership.
        enforce_maintenance(dict(name='hardware_stop'),self.folder)

    def test_all_idle_is_checked_while_shared_lease_already_exists(self):
        self.host.device(SN)
        seen=[]
        self.controller.official_cli.idle_check.side_effect=lambda:seen.append(cli_coordination.reservation(self.folder).get('kind'))
        token=self.host.acquire('firmware')
        self.assertEqual(seen,['firmware'])
        self.assertEqual(cli_coordination.reservation(self.folder)['serial'],SN)
        self.host.release(token)
        self.assertEqual(cli_coordination.reservation(self.folder),{})

    def test_idle_failure_releases_only_its_own_reservation(self):
        self.host.device(SN)
        self.controller.official_cli.idle_check.side_effect=ValueError('other workspace active')
        with self.assertRaises(ValueError):self.host.acquire('firmware')
        self.controller.official_cli.idle_check.assert_called_once()
        self.assertEqual(cli_coordination.reservation(self.folder),{})

    def test_release_cannot_clear_a_different_owner(self):
        self.write_marker()
        with self.assertRaises(ValueError):self.host.release('b'*32)
        self.assertEqual(cli_coordination.reservation(self.folder)['token'],TOKEN)

    def test_terminal_input_is_exact_run_and_never_sends_interrupts(self):
        self.write_marker()
        process=FakeProcess(alive=True)
        self.host.process=process
        self.host.session_token=TOKEN
        self.host.session=dict(id='b'*32,running=True,status='running',serial=SN,reservation_token=TOKEN)
        base=dict(name='firmware_terminal_input',run_id='b'*32,type='write')
        for text in ('\x03','\x04','\x1b','\x1b[200~y\x1b[201~','y\n','x'*65,''):
            with self.subTest(text=repr(text)),self.assertRaises(ValueError):
                self.host.terminal_input(dict(base,text=text))
        for text in ('y','n','\r','\n','\x1b[A','\x1b[B'):
            self.host.terminal_input(dict(base,text=text))
        self.assertEqual(process.writes,['y','n','\r','\n','\x1b[A','\x1b[B'])
        with self.assertRaises(ValueError):self.host.terminal_input(dict(base,text='y',run_id='c'*32))
        self.host.terminal_input(dict(name='firmware_terminal_input',run_id='b'*32,type='resize',rows=30,cols=100))
        self.assertEqual(process.sizes,[(30,100)])

    def test_terminal_read_is_bounded_and_reports_lost_cursor(self):
        self.host.session=dict(id='b'*32,running=True)
        self.host._append('x'*(4096*130))
        result=self.host.terminal_read('b'*32,0)
        self.assertTrue(result['gap'])
        self.assertEqual(len(result['chunks']),128)
        self.assertLessEqual(max(len(row['text']) for row in result['chunks']),4096)
        with self.assertRaises(ValueError):self.host.terminal_read('b'*32,True)

    def test_terminal_cannot_confirm_after_shared_lease_changes(self):
        self.write_marker()
        process=FakeProcess(alive=True,status=0)
        self.host.spawn=Mock(return_value=process)
        results=[]
        def execute():
            try:results.append(self.host(['upgrade','--sn',SN],mutating=True,interactive=True))
            except Exception as error:results.append(error)
        with patch.object(self.host,'_component',return_value=self.component),patch('firmware_host.identity',return_value='child'):
            thread=threading.Thread(target=execute,daemon=True);thread.start()
            try:
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    run=self.host.run_snapshot()
                    if run and run.get('status')=='running':break
                    time.sleep(.005)
                self.assertIsNotNone(run)
                changed=cli_coordination.reservation(self.folder);changed['token']='c'*32
                cli_coordination.atomic(self.folder/'session.json',changed)
                with self.assertRaises(ValueError):
                    self.host.terminal_input(dict(name='firmware_terminal_input',run_id=run['id'],type='write',text='y'))
                self.assertEqual(process.writes,[])
            finally:
                process.alive=False;thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_interactive_exit_is_actual_exit_and_never_writes_confirmation(self):
        self.write_marker()
        process=FakeProcess()
        self.host.spawn=Mock(return_value=process)
        with patch.object(self.host,'_component',return_value=self.component),patch('firmware_host.identity',return_value='child'):
            result=self.host(['upgrade','--sn',SN],mutating=True,interactive=True)
        self.assertTrue(result['exited'])
        self.assertEqual(result['returncode'],0)
        self.assertEqual(process.writes,[])
        self.assertFalse(process.closed)
        self.host.spawn.assert_called_once_with(['verified-wuji.exe','upgrade','--sn',SN])

    def test_unknown_transport_preserves_process_and_marker_without_killing(self):
        self.write_marker()
        process=FakeProcess(status=None,read_error=RuntimeError('read failed'))
        self.host.spawn=Mock(return_value=process)
        with patch.object(self.host,'_component',return_value=self.component),patch('firmware_host.identity',return_value='child'):
            result=self.host(['upgrade','--sn',SN],mutating=True,interactive=True)
        self.assertFalse(result['exited'])
        self.assertIs(self.host.process,process)
        self.assertFalse(process.closed)
        self.assertEqual(cli_coordination.reservation(self.folder)['token'],TOKEN)

    def test_spawned_process_is_retained_even_if_persistent_marker_write_fails(self):
        self.write_marker()
        process=FakeProcess(alive=True,status=None)
        self.host.spawn=Mock(return_value=process)
        original=firmware_host.atomic
        calls=[]
        def atomic(path,value):
            calls.append(copy.deepcopy(value))
            if 'child_pid' in value and len(calls)>1:
                raise OSError('disk full after child spawn')
            return original(path,value)
        # The first prelaunch marker can already have child_pid from restoration;
        # the second update is the new child registration we intentionally fail.
        with patch.object(self.host,'_component',return_value=self.component),patch('firmware_host.atomic',side_effect=atomic),patch('firmware_host.identity',return_value='child'):
            try:self.host(['upgrade','--sn',SN],mutating=True,interactive=True)
            except OSError:pass
        self.assertIs(self.host.process,process,'A dropped PtyProcess may terminate a live firmware child in __del__')
        self.assertFalse(process.closed)
        run=self.host.run_snapshot()
        self.assertEqual(run['status'],'unknown')
        with self.assertRaises(ValueError):
            self.host.terminal_input(dict(name='firmware_terminal_input',run_id=run['id'],type='write',text='y'))
        self.assertEqual(process.writes,[])

    def test_unknown_terminal_state_cannot_forward_confirmation_or_resize(self):
        self.write_marker()
        process=FakeProcess(alive=True)
        self.host.process=process;self.host.session_token=TOKEN
        self.host.session=dict(id='b'*32,running=True,status='unknown',serial=SN)
        for request in (dict(type='write',text='y'),dict(type='resize',rows=30,cols=100)):
            with self.subTest(request=request),self.assertRaises(ValueError):
                self.host.terminal_input(dict(name='firmware_terminal_input',run_id='b'*32,**request))
        self.assertEqual(process.writes,[]);self.assertEqual(process.sizes,[])

    def test_recovery_cannot_query_or_unlock_a_known_live_original_child(self):
        self.write_marker()
        with patch('firmware_host.process_state',return_value='live'),patch('native_cli.json_command') as getter:
            with self.assertRaises(ValueError):self.host.recovery_check(SN,reservation_token=TOKEN)
        getter.assert_not_called()

    def test_recovery_requires_matching_device_identity_and_actual_version(self):
        self.write_marker()
        with patch('firmware_host.process_state',return_value='dead'),patch('native_cli.json_command',return_value=dict(sn='other',value='2.5.1')):
            with self.assertRaises(ValueError):self.host.recovery_check(SN,reservation_token=TOKEN)
        self.assertEqual(cli_coordination.reservation(self.folder)['token'],TOKEN)

    def test_recovery_cannot_interpret_inaccessible_process_as_dead(self):
        self.write_marker()
        with patch('firmware_host.process_state',return_value='unknown'),patch('native_cli.json_command') as getter:
            with self.assertRaises(ValueError):self.host.recovery_check(SN,reservation_token=TOKEN)
        getter.assert_not_called()
        self.assertEqual(cli_coordination.reservation(self.folder)['token'],TOKEN)

    def test_recovery_requires_host_dead_process_and_exact_sn_version_readback(self):
        self.write_marker()
        with patch('firmware_host.process_state',return_value='dead'),patch('native_cli.json_command',return_value=dict(sn=SN,value='2.5.1')) as getter:
            result=self.host.recovery_check(SN,reservation_token=TOKEN)
        getter.assert_called_once_with('get','firmware_version','--sn',SN)
        self.assertEqual(result,dict(process_exited=True,identity_verified=True,sn=SN,
                                     reservation_token=TOKEN,firmware_version='2.5.1'))
        # A successful probe is evidence; only the service releases the lease.
        self.assertEqual(cli_coordination.reservation(self.folder)['token'],TOKEN)

    def test_windows_process_lookup_is_tristate_on_access_and_query_failures(self):
        import ctypes
        def kernel(handle=123,exit_ok=True,exit_code=259,times_ok=True):
            value=Mock()
            value.OpenProcess.return_value=handle
            def read_exit(unused,result):result._obj.value=exit_code;return exit_ok
            def read_times(unused,created,*others):
                created._obj.dwLowDateTime=42;created._obj.dwHighDateTime=0;return times_ok
            value.GetExitCodeProcess.side_effect=read_exit
            value.GetProcessTimes.side_effect=read_times
            return value
        cases=[(kernel(handle=0),5,'unknown'),(kernel(handle=0),87,'dead'),
               (kernel(exit_ok=False),0,'unknown'),(kernel(times_ok=False),0,'unknown'),
               (kernel(exit_code=0),0,'dead'),(kernel(),0,'live')]
        for api,error,expected in cases:
            with self.subTest(error=error,expected=expected),patch('firmware_host.sys.platform','win32'),patch('ctypes.WinDLL',return_value=api),patch('ctypes.get_last_error',return_value=error):
                self.assertEqual(firmware_host.process_state(9999,'42'),expected)

    def test_maintenance_refuses_desktop_reload_or_code_close(self):
        from desktop_tools import dispatch,idle
        state=dict(connection='disconnected',hardware=dict(active=False),maintenance=dict(busy=True,kind='firmware'))
        self.assertFalse(idle(state,{}),'Firmware maintenance is not an idle desktop')
        host=Mock();host.server.controller.snapshot.return_value=state
        host.server.controller.doctor.snapshot.return_value={}
        for operation in ('reload_ui','close_idle','prepare_update','preview'):
            payload=dict(operation=operation)
            if operation=='preview':payload.update(action='open',speed=1)
            with self.subTest(operation=operation),self.assertRaises(ValueError):dispatch(host,payload)
        host.reload_ui.assert_not_called();host.stop_and_close.assert_not_called();host.preview.assert_not_called()

    def test_normal_desktop_close_preserves_maintenance_and_does_not_cancel(self):
        from desktop_lifecycle import close_sessions,needs_confirmation
        state=dict(maintenance=dict(busy=True,kind='firmware'),connection='disconnected',hardware=dict(active=False))
        controller=Mock();controller.snapshot.return_value=state
        self.assertTrue(needs_confirmation(state))
        self.assertFalse(close_sessions(controller)['ok'])
        controller.action.assert_not_called();controller.official_cli.cancel_and_wait.assert_not_called()

    def test_task_view_cannot_dispatch_maintenance_even_with_other_valid_context(self):
        from task_view import execute
        controller=Mock()
        for name in ('firmware_check','firmware_upgrade','firmware_terminal_input','sdk_update_start','sdk_release_refresh'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                execute(controller,'a'*24,'b'*32,dict(name=name))
        controller.snapshot.assert_not_called();controller.action.assert_not_called()

    def test_http_routes_maintenance_to_root_only_and_reject_task_or_fleet_scope(self):
        from console_server import Handler,ORIGIN
        command=dict(name='firmware_check',serial=SN)
        body=json.dumps(command).encode()
        def handler(path='/api/action',scope=None):
            item=Handler.__new__(Handler)
            item.path=path
            item.headers={'Origin':ORIGIN,'X-Console-Token':'csrf','Content-Length':str(len(body)),'Content-Type':'application/json'}
            item.rfile=io.BytesIO(body);item.allowed_host=lambda:True
            item.reply=Mock();item._task_view_headers=Mock(return_value=scope)
            item.server=N(controller=N(csrf='csrf',action=Mock()),maintenance_action=Mock(return_value=dict(maintenance={})))
            return item
        with patch.dict('os.environ',{},clear=False):
            import os
            previous=os.environ.pop('WUJI_FLEET_PARENT',None)
            try:
                item=handler();item.do_POST()
                item.server.maintenance_action.assert_called_once_with(command)
                item.server.controller.action.assert_not_called()
                self.assertEqual(item.reply.call_args.args[0],200)
                for item in (handler(scope=('a'*24,'b'*64)),handler('/api/desktop')):
                    item.do_POST();item.server.maintenance_action.assert_not_called()
                    self.assertEqual(item.reply.call_args.args[0],400)
                with patch.dict('os.environ',WUJI_FLEET_PARENT='http://127.0.0.1:8781'):
                    item=handler();item.do_POST();item.server.maintenance_action.assert_not_called()
                    self.assertEqual(item.reply.call_args.args[0],400)
            finally:
                if previous is not None:os.environ['WUJI_FLEET_PARENT']=previous


if __name__=='__main__':unittest.main()
