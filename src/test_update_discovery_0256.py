"""Actual update/discovery/close flow, synthetic HTTP and OS boundaries only."""
from contextlib import ExitStack
import copy
import io
import json
import socket
import subprocess
import threading
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import ConsoleHTTPServer,Controller,Handler
from cli_coordination import reservation,root_folder
from desktop_tools import dispatch
from desktop_lifecycle import close_sessions
from device_fleet import DeviceFleet
from native_desktop import NativeDesktop
from official_device_discovery import DiscoveryDeferred
from official_visualization import OfficialVisualization
import test_workbench_update_service as fixtures

EDITION=fixtures.EDITION

REAL_THREAD=threading.Thread


class UpdateDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('Actual network forbidden')))
        self.stack.enter_context(patch.object(subprocess,'Popen',side_effect=AssertionError('Actual processes forbidden')))
        self.env=env=self.stack.enter_context(fixtures.ServiceTests().environment())
        self.stack.enter_context(patch('threading.Thread',REAL_THREAD))
        self.stack.enter_context(patch('managed_runtime.status',return_value=dict(busy=False)))
        self.stack.enter_context(patch('bridge_config.load_config',return_value=dict(mode='local')))
        self.stack.enter_context(patch('official_device_discovery.use_native',return_value=True))
        self.stack.enter_context(patch('runtime_paths.EDITION',EDITION))
        self.stack.enter_context(patch('console_server.DATA',env.data))
        self.timer=self.stack.enter_context(patch('native_desktop.threading.Timer'))
        self.viewer=viewer=OfficialVisualization(root_folder())
        self.state=dict(connection='disconnected',hardware=dict(active=False,stop_confirmed=True),glove=dict(busy=False),
            calibration={},parameter_sync={},official_studio={},maintenance={})
        self.controller=controller=N(lock=threading.RLock(),desktop_closing=False,csrf='synthetic-token',
            official_cli=N(closed=threading.Event(),cancel_and_wait=Mock(return_value=True)),official_viz=viewer,
            calibration_handover=N(internal=False),calibration=N(),glove=N(busy=False),
            official_tools=N(snapshot=Mock(return_value=dict(check=dict(busy=False)))),
            doctor=N(snapshot=Mock(return_value=dict(running=False))),calibration_devices=Mock(),action=Mock(),
            studio=N(stop=Mock()),player=N(command=Mock()))
        controller.snapshot=lambda:dict(copy.deepcopy(self.state),official_viz=viewer.snapshot())
        self.server=server=object.__new__(ConsoleHTTPServer);server.controller=controller
        self.host=host=object.__new__(NativeDesktop)
        host.session_lock=threading.Lock();host.allow_close=False;host.server=server;host.window=N(destroy=Mock())
        host.data_dir=str(env.data);host.edition=EDITION;host.origin=env.host.origin;host.info=env.host.info
        server.desktop=host;server.configure_official_cli(startup=False)
        self.service=server.updates;self.service.store=env.store
        self.service.latest=copy.deepcopy(env.latest);self.service.state.update(phase='ready',prepared=copy.deepcopy(env.prepared),available=True)
        env.source.parent.mkdir(parents=True);env.source.write_bytes(b'MZ SYNTHETIC UPDATE FIXTURE - DO NOT EXECUTE\n')
        self.service._stage_installer(env.prepared)
        self.scans=[]
        self.stack.enter_context(patch('calibration_cli.cli_json',side_effect=self.scan_transport))
        self.addCleanup(self.finish_scans)

    def scan_transport(self,config,*args,cancelled=None):
        entered,finish=self.scans[-1];entered.set();limit=time.monotonic()+3
        while not finish.is_set():
            if cancelled and cancelled():raise DiscoveryDeferred('Synthetic discovery yielded')
            if time.monotonic()>limit:raise AssertionError('Synthetic discovery watchdog')
            time.sleep(.002)
        return dict(devices=[])

    def begin_scan(self):
        row=(threading.Event(),threading.Event());self.scans.append(row)
        self.viewer.scan();self.assertTrue(row[0].wait(1))

    def finish_scans(self):
        for entered,finish in self.scans:finish.set()
        if self.scans:self.assertTrue(self.viewer.scan_done.wait(3),'Synthetic discovery remained alive')

    def post(self,payload,path='/api/desktop'):
        raw=json.dumps(payload).encode();handler=object.__new__(Handler)
        handler.path=path;handler.server=self.server;handler.rfile=io.BytesIO(raw)
        handler.headers={'Content-Length':str(len(raw)),'Content-Type':'application/json','Origin':'http://127.0.0.1:8781','X-Console-Token':'synthetic-token'}
        handler.reply=Mock();handler.allowed_host=lambda:True;handler._task_view_headers=lambda:None
        handler.do_POST();return handler.reply.call_args.args

    def prepare_command(self):
        return dict(operation='prepare_update',version=self.env.prepared['version'],sha256=self.env.prepared['sha256'])

    def test_getter_allows_only_owned_normal_scan_without_cancelling_it(self):
        self.begin_scan();marker=reservation(self.viewer.folder);token=self.viewer.scan_token
        for _ in range(3):self.assertTrue(self.service.snapshot()['can_apply'])
        self.assertFalse(self.viewer.scan_cancel.is_set());self.assertEqual(reservation(self.viewer.folder),marker)
        self.assertEqual(self.viewer.scan_token,token);self.env.popen.assert_not_called()
        self.viewer._set(scan_cleanup_required=True)
        self.assertFalse(self.service.snapshot()['can_apply'])
        self.viewer._set(scan_cleanup_required=False)
        self.state['connection']='connected'
        self.assertFalse(self.service.snapshot()['can_apply'])
        self.assertFalse(self.viewer.scan_cancel.is_set())

    def test_apply_while_scanning_yields_read_then_launches_one_fake_helper(self):
        self.begin_scan();token=self.viewer.scan_token
        code,body=self.post(dict(name='workbench_update_apply'),'/api/action')
        self.assertEqual(code,200,body);self.assertEqual(body['workbench_update']['phase'],'applying')
        self.assertTrue(self.viewer.scan_done.is_set());self.assertNotEqual(self.viewer.scan_token,token)
        self.assertEqual(reservation(self.viewer.folder),{});self.assertEqual(self.env.popen.call_count,1)
        self.assertFalse(self.viewer.snapshot()['active']);self.assertEqual(self.viewer.scan_pauses,0)
        self.controller.action.assert_not_called();self.host.window.destroy.assert_not_called()

    def test_helper_prepare_and_close_each_yield_a_new_scan_without_any_reconnect(self):
        self.begin_scan()
        code,body=self.post(self.prepare_command());self.assertEqual(code,200,body)
        self.begin_scan()
        code,body=self.post(dict(operation='close_idle'));self.assertEqual(code,200,body);self.assertTrue(body['ok'])
        self.assertTrue(self.viewer.scan_done.is_set());self.assertEqual(self.viewer.scan_pauses,0)
        self.controller.calibration_devices.assert_not_called();self.env.popen.assert_not_called()
        self.controller.action.assert_called_once_with(dict(name='disconnect'))
        self.host.window.destroy.assert_not_called();self.assertEqual(self.timer.call_count,1)

    def test_active_viewer_sdk_and_other_workspace_are_not_closed_by_update(self):
        for state in (dict(active=True,busy=True,phase='running',session_id='b'*32),dict(active=False,busy=False,phase='idle',session_id=None)):
            self.viewer._set(**state)
            if not state['active']:self.state['connection']='connected'
            code,body=self.post(self.prepare_command());self.assertEqual(code,400,body)
        self.state['connection']='disconnected';self.viewer._set(active=False,busy=False)
        child=dict(connection='connected',hardware=dict(active=False))
        self.server._fleet=N(lock=threading.RLock(),children={'synthetic-child':dict(id='synthetic-child')},
            get=Mock(side_effect=lambda row,path:child if path=='/api/state' else dict(running=False)),close=Mock())
        code,body=self.post(dict(name='workbench_update_apply'),'/api/action');self.assertEqual(code,400,body)
        self.env.popen.assert_not_called();self.controller.action.assert_not_called()
        self.server._fleet.close.assert_not_called();self.host.window.destroy.assert_not_called()

    def test_unknown_discovery_cleanup_is_bounded_and_keeps_update_unstarted(self):
        self.viewer._set(busy=True,scanning=True,scan_cleanup_required=True)
        done=self.viewer.scan_done;self.viewer.scan_done=Mock();self.viewer.scan_done.wait.return_value=False
        try:
            self.assertFalse(self.service.snapshot()['can_apply'])
            code,body=self.post(dict(name='workbench_update_apply'),'/api/action')
            self.assertEqual(code,400,body)
            self.assertLessEqual(self.viewer.scan_done.wait.call_args.args[0],2.)
            self.assertEqual(self.viewer.scan_pauses,0);self.env.popen.assert_not_called()
            self.host.window.destroy.assert_not_called()
        finally:self.viewer.scan_done=done

    def test_slow_apply_hash_cannot_start_helper_after_request_deadline(self):
        tick=[100.];self.service.clock=lambda:tick[0];stage=self.service._stage_installer
        def slow(*args):
            value=stage(*args);tick[0]+=11.;return value
        self.service._stage_installer=slow
        with self.assertRaisesRegex(ValueError,'timed out'):self.service.apply()
        self.env.popen.assert_not_called();self.host.window.destroy.assert_not_called()
        self.assertFalse(self.service.state['busy']);self.assertEqual(self.service.state['prepared'],self.env.prepared)
        self.service._stage_installer=stage
        self.assertEqual(self.service.apply()['phase'],'applying')
        self.assertEqual(self.env.popen.call_count,1)

    def test_prepare_hash_and_gate_delay_share_original_budget(self):
        tick=[100.]
        import desktop_tools
        verify=desktop_tools.verify_update
        def slow(*args):
            value=verify(*args);tick[0]+=11.;return value
        with patch('desktop_tools.verify_update',side_effect=slow),self.assertRaisesRegex(ValueError,'timed out'):
            dispatch(self.host,self.prepare_command(),clock=lambda:tick[0])
        self.controller.action.assert_not_called();self.host.window.destroy.assert_not_called()

    def test_slow_close_preflight_or_update_cancellation_cannot_later_close_any_workspace(self):
        for boundary in ('preflight','cancel'):
            with self.subTest(boundary=boundary):
                tick=[100.];original=self.controller.official_cli.idle_check
                if boundary=='preflight':
                    self.controller.official_cli.idle_check=lambda:tick.__setitem__(0,111.)
                else:
                    self.server._workbench_updates=N(cancel_and_wait=Mock(side_effect=lambda **kw:tick.__setitem__(0,111.) or True))
                fleet=N(lock=threading.RLock(),children={},snapshot=Mock(return_value=[]),close=Mock())
                self.server._fleet=fleet
                result=self.host.stop_and_close(require_idle=True,deadline=110.,clock=lambda:tick[0])
                self.assertFalse(result['ok']);fleet.close.assert_not_called()
                self.controller.action.assert_not_called();self.timer.assert_not_called()
                self.host.window.destroy.assert_not_called();self.assertFalse(self.controller.desktop_closing)
                self.controller.official_cli.idle_check=original

    def test_delayed_close_timer_cannot_destroy_after_original_deadline_and_retry_is_explicit(self):
        tick=[100.]
        result=self.host.stop_and_close(require_idle=True,deadline=110.,clock=lambda:tick[0])
        self.assertTrue(result['ok']);callback=self.timer.call_args.args[1]
        tick[0]=111.;callback()
        self.host.window.destroy.assert_not_called();self.assertFalse(self.host.allow_close)
        self.assertFalse(self.controller.desktop_closing)
        result=self.host.stop_and_close(require_idle=True,deadline=121.,clock=lambda:tick[0])
        self.assertTrue(result['ok']);self.timer.call_args.args[1]()
        self.host.window.destroy.assert_called_once()


class FleetUpdateDeadlineTests(unittest.TestCase):
    def fleet(self,processes):
        fleet=object.__new__(DeviceFleet);fleet.lock=threading.RLock();fleet._closing=False
        fleet.children={str(i):dict(id=str(i),port=9000+i,process=p) for i,p in enumerate(processes)}
        fleet._beat_pool=N(shutdown=Mock());return fleet

    def test_expired_child_post_stops_before_next_post_or_termination_and_retains_records(self):
        tick=[100.];processes=[Mock(),Mock()];fleet=self.fleet(processes)
        fleet.post=Mock(side_effect=lambda *a,**kw:tick.__setitem__(0,111.) or dict(ok=True))
        self.assertFalse(fleet.close(deadline=110.,clock=lambda:tick[0])['ok'])
        self.assertEqual(fleet.post.call_count,1)
        for process in processes:process.terminate.assert_not_called();process.kill.assert_not_called()
        self.assertEqual(len(fleet.children),2);self.assertFalse(fleet._closing)

    def test_wait_timeout_does_not_issue_kill_after_deadline_or_erase_exit_facts(self):
        tick=[100.];process=Mock();process.poll.return_value=None;fleet=self.fleet([process])
        fleet.post=Mock(return_value=dict(ok=True))
        def wait(**kw):tick[0]=111.;raise subprocess.TimeoutExpired('synthetic-child',kw['timeout'])
        process.wait.side_effect=wait
        self.assertFalse(fleet.close(deadline=110.,clock=lambda:tick[0])['ok'])
        process.terminate.assert_called_once();process.kill.assert_not_called()
        self.assertLessEqual(process.wait.call_args.kwargs['timeout'],5.)
        self.assertEqual(len(fleet.children),1);self.assertFalse(fleet._closing)

    def test_expired_child_state_read_cannot_send_a_late_close_request(self):
        tick=[100.];fleet=self.fleet([])
        fleet.get=Mock(side_effect=lambda *a,**kw:tick.__setitem__(0,111.) or dict(csrf='synthetic-token'))
        with patch('device_fleet.urlopen') as request,self.assertRaisesRegex(ValueError,'timed out'):
            fleet.post(dict(port=9000),dict(name='session_close'),deadline=110.,clock=lambda:tick[0])
        request.assert_not_called()

    def test_child_post_carries_same_absolute_deadline_and_bounded_transport_timeout(self):
        fleet=self.fleet([]);fleet.get=Mock(return_value=dict(csrf='synthetic-token'))
        response=io.BytesIO(b'{"ok":true}')
        with patch('device_fleet.urlopen',return_value=response) as request:
            self.assertTrue(fleet.post(dict(port=9000),dict(name='session_close'),deadline=110.,clock=lambda:105.)['ok'])
        wire=json.loads(request.call_args.args[0].data)
        self.assertEqual(wire,dict(name='session_close',update_close_deadline=110.))
        self.assertEqual(request.call_args.kwargs['timeout'],5.)

    def test_expired_child_program_cleanup_cannot_cancel_calibration_or_dispatch_more_actions(self):
        tick=[100.]
        controller=object.__new__(Controller);controller.lock=threading.RLock();controller.desktop_closing=False
        controller.snapshot=Mock(return_value=dict(connection='disconnected',hardware=dict(active=False)))
        controller.doctor=N(snapshot=Mock(return_value=dict(running=False)))
        controller.program=N(stop=Mock(side_effect=lambda:tick.__setitem__(0,111.)))
        controller.group=N(active=False,stop=Mock());controller.calibration=N(cancel=Mock())
        import desktop_tools
        remaining=desktop_tools.update_remaining
        with patch('desktop_tools.update_remaining',side_effect=lambda deadline:remaining(deadline,clock=lambda:tick[0])), \
                patch('studio_component.enforce_session_owner'),patch('desktop_lifecycle.close_sessions') as close:
            with self.assertRaisesRegex(ValueError,'timed out'):
                controller.action(dict(name='session_close',update_close_deadline=110.))
        controller.program.stop.assert_called_once();controller.group.stop.assert_not_called()
        controller.calibration.cancel.assert_not_called();close.assert_not_called()
        self.assertFalse(controller.desktop_closing)

    def test_close_sessions_stops_after_late_player_cleanup_without_disconnect(self):
        tick=[100.]
        controller=N(snapshot=Mock(return_value=dict(connection='disconnected',hardware=dict(active=False))),
            doctor=N(snapshot=Mock(return_value=dict(running=False))),
            official_cli=N(closed=threading.Event(),cancel_and_wait=Mock(return_value=True)),
            player=N(command=Mock(side_effect=lambda command:tick.__setitem__(0,111.))),action=Mock())
        result=close_sessions(controller,deadline=110.,clock=lambda:tick[0])
        self.assertFalse(result['ok']);self.assertTrue(controller.official_cli.closed.is_set())
        controller.player.command.assert_called_once();controller.action.assert_not_called()
        controller.official_cli.cancel_and_wait.assert_not_called()


if __name__=='__main__':unittest.main()
