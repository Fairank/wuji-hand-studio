"""Application-update admission vs close intent; all external work is fake."""
from contextlib import contextmanager
import copy,io,json,os,threading
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import Handler,ConsoleHTTPServer
import native_desktop
import workbench_update
import cli_coordination
import test_workbench_update_service as fixtures
from update_notifications import UpdateNotifications

RealThread=threading.Thread


def post(server,name):
    body=json.dumps(dict(name=name)).encode();response=[]
    h=N(path='/api/action',server=server,headers={'Origin':'http://127.0.0.1:8781',
        'X-Console-Token':server.controller.csrf,'Content-Type':'application/json','Content-Length':str(len(body))},
        rfile=io.BytesIO(body),allowed_host=lambda:True,_task_view_headers=lambda:None,
        reply=lambda status,value,*a,**kw:response.append((status,value)))
    Handler.do_POST(h)
    return response[0]


class BoundedLock:
    """Make a wrong production lock order fail instead of leaking a QA thread."""
    def __init__(self,on_enter=lambda:None):self.lock=threading.RLock();self.on_enter=on_enter
    def __enter__(self):
        self.on_enter()
        if not self.lock.acquire(timeout=1):raise ValueError('Test observed opposing update/controller lock order')
        return self
    def __exit__(self,*a):self.lock.release()


class UpdateCloseAdmissionTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with fixtures.ServiceTests().environment() as e:
            fixtures.ServiceTests().ready(e)
            c=N(csrf='synthetic-update-token',lock=threading.RLock(),desktop_closing=False)
            server=object.__new__(ConsoleHTTPServer);server.controller=c
            # Exercise the real server's constructor wiring, but retain the
            # already isolated store/native/worker fixture for every operation.
            with patch('workbench_update.WorkbenchUpdate',return_value=e.service) as factory:
                self.assertIs(server.updates,e.service)
            e.service.start_scope=factory.call_args.kwargs['start_scope']
            e.controller=c;e.server=server
            yield e

    def test_late_http_check_and_prepare_reject_before_clearing_cancel_or_starting_worker(self):
        for name in ('workbench_update_check','workbench_update_prepare'):
            with self.subTest(name=name),self.environment() as e:
                e.controller.desktop_closing=True;e.service.closed.set()
                before=copy.deepcopy(e.service.state)
                code,value=post(e.server,name)
                self.assertEqual(code,400);self.assertFalse(value['ok'])
                self.assertIn('closing',value['error'])
                self.assertTrue(e.service.closed.is_set());self.assertIsNone(e.service.thread)
                self.assertEqual(e.service.state,before)
                e.store.check.assert_not_called();e.store.prepare.assert_not_called();e.popen.assert_not_called()

    def test_native_close_after_cancel_cannot_admit_late_prepare_from_existing_ui(self):
        with self.environment() as e:
            c=e.controller;c.snapshot=lambda:dict(connection='disconnected',hardware=dict(active=False,stop_confirmed=True),glove={})
            c.doctor=N(snapshot=lambda:dict(running=False));host=native_desktop.NativeDesktop();host.window=N(destroy=Mock())
            replies=[]
            def fleet_close(**kw):
                self.assertTrue(c.desktop_closing);self.assertTrue(e.service.closed.is_set())
                replies.append(post(host.server,'workbench_update_prepare'))
                return dict(ok=True)
            host.server=N(controller=c,_workbench_updates=True,updates=e.service,_fleet=True,fleet=N(close=fleet_close))
            with patch.object(native_desktop,'close_sessions',return_value=dict(ok=True)),patch.object(native_desktop.threading,'Timer') as timer:
                self.assertTrue(host.stop_and_close()['ok']);timer.call_args.args[1]()
            self.assertEqual(replies[0][0],400);self.assertIsNone(e.service.thread)
            self.assertTrue(e.service.closed.is_set());host.window.destroy.assert_called_once()
            e.store.prepare.assert_not_called();e.popen.assert_not_called()

    def test_background_metadata_check_uses_same_admission_and_never_starts_download(self):
        with self.environment() as e,patch.dict(os.environ,WUJI_APP_AUTO_CHECK='1',WUJI_CLI_AUTO_CHECK='0'):
            e.controller.desktop_closing=True;e.service.closed.set();e.service.state['checked_at']=None
            sdk=Mock();monitor=UpdateNotifications(states=lambda:[],app=lambda:e.service,cli=Mock(),sdk=lambda:sdk,
                firmware=Mock(),devices=Mock(),idle=Mock(),clock=lambda:1000.)
            monitor.next_check['sdk']=2000.
            monitor._software(1000.)
            self.assertIsNone(e.service.thread);self.assertTrue(e.service.closed.is_set())
            e.store.check.assert_not_called();e.store.prepare.assert_not_called();sdk.check_background.assert_not_called()

    def test_failed_close_reset_allows_explicit_prepare_without_changing_existing_payload(self):
        with self.environment() as e:
            original=e.source.read_bytes();e.service.cancel_and_wait();e.controller.desktop_closing=True
            self.assertEqual(post(e.server,'workbench_update_prepare')[0],400)
            e.controller.desktop_closing=False
            self.assertEqual(post(e.server,'workbench_update_prepare')[0],200)
            self.assertFalse(e.service.closed.is_set());e.service.thread.run_to_end()
            self.assertEqual(e.service.snapshot()['phase'],'ready');self.assertEqual(e.source.read_bytes(),original)
            e.popen.assert_not_called()

    def test_start_accepted_before_close_is_captured_by_cancellation(self):
        with self.environment() as e:
            scope_entered=threading.Event();release_scope=threading.Event();errors=[];answers=[]
            @contextmanager
            def held_scope():
                with e.controller.lock:
                    if e.controller.desktop_closing:raise ValueError('Workbench is closing')
                    scope_entered.set()
                    if not release_scope.wait(2):raise AssertionError('Test did not release admission')
                    yield
            e.service.start_scope=held_scope
            def start():
                try:e.service.start('check')
                except Exception as error:errors.append(str(error))
            def close():
                with e.controller.lock:e.controller.desktop_closing=True
                answers.append(e.service.cancel_and_wait(timeout=.01))
            first=RealThread(target=start);second=RealThread(target=close)
            first.start()
            try:
                self.assertTrue(scope_entered.wait(1));second.start();release_scope.set()
                first.join(2);second.join(2)
                self.assertFalse(first.is_alive());self.assertFalse(second.is_alive());self.assertEqual(errors,[])
                self.assertEqual(answers,[False]);self.assertTrue(e.service.closed.is_set())
                e.service.thread.run_to_end()
                self.assertEqual(e.service.snapshot()['phase'],'error');self.assertTrue(e.service.state['cancelled'])
                self.assertTrue(e.service.cancel_and_wait(timeout=.01));e.popen.assert_not_called()
            finally:
                release_scope.set();first.join(2)
                if second.ident is not None:second.join(2)

    def test_apply_and_metadata_start_keep_update_then_controller_lock_order(self):
        with self.environment() as e:
            apply_idle=threading.Event();start_waiting=threading.Event();allow_idle=threading.Event();errors={}
            e.controller.lock=BoundedLock()
            def enter_update():
                if threading.current_thread().name=='admission-metadata':start_waiting.set()
            e.service.lock=BoundedLock(enter_update);e.service.availability_check=lambda:None
            def idle():
                apply_idle.set()
                if not allow_idle.wait(2):raise AssertionError('Test did not release installer idle check')
                with e.controller.lock:pass
            e.service.idle_check=idle
            def apply():
                try:e.service.apply()
                except Exception as error:errors['apply']=str(error)
            def start():
                try:e.service.start('check')
                except Exception as error:errors['start']=str(error)
            first=RealThread(target=apply,name='admission-apply');second=RealThread(target=start,name='admission-metadata')
            first.start()
            try:
                self.assertTrue(apply_idle.wait(1));second.start();self.assertTrue(start_waiting.wait(1));allow_idle.set()
                first.join(3);second.join(3)
                self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
                self.assertNotIn('apply',errors);self.assertIn('busy',errors.get('start',''))
                self.assertEqual(e.popen.call_count,1);self.assertEqual(e.service.snapshot()['phase'],'applying')
            finally:
                allow_idle.set();first.join(3)
                if second.ident is not None:second.join(3)

    def test_helper_close_and_duplicate_apply_keep_gate_then_update_lock_order(self):
        with self.environment() as e:
            c=e.controller;close_preflight=threading.Event();release_close=threading.Event();apply_gate=threading.Event()
            e.service.state.update(phase='applying',busy=True);e.service.availability_check=lambda:None
            e.service.lock=BoundedLock()
            def gate_enter():
                if threading.current_thread().name=='duplicate-apply':apply_gate.set()
            shared=BoundedLock(gate_enter)
            def snapshot():
                if threading.current_thread().name=='helper-close' and not close_preflight.is_set():
                    close_preflight.set()
                    if not release_close.wait(2):raise AssertionError('Test did not release helper preflight')
                return dict(connection='disconnected',hardware=dict(active=False,stop_confirmed=True),glove={})
            c.snapshot=snapshot;c.official_cli=N(idle_check=Mock());c.doctor=N(snapshot=lambda:dict(running=False))
            host=native_desktop.NativeDesktop();host.window=N(destroy=Mock());host.server=N(controller=c,_workbench_updates=True,updates=e.service)
            results={}
            def close():results['close']=host.stop_and_close(require_idle=True)
            def apply():
                try:e.service.apply()
                except Exception as error:results['apply_error']=str(error)
            first=RealThread(target=close,name='helper-close');second=RealThread(target=apply,name='duplicate-apply')
            with (patch.object(workbench_update,'gate',return_value=shared),patch.object(cli_coordination,'gate',return_value=shared),
                    patch.object(native_desktop,'close_sessions',return_value=dict(ok=True)),patch.object(native_desktop.threading,'Timer')):
                first.start()
                try:
                    self.assertTrue(close_preflight.wait(1));second.start();self.assertTrue(apply_gate.wait(1));release_close.set()
                    first.join(3);second.join(3)
                    self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
                    self.assertTrue(results['close']['ok'],results);self.assertIn('Download and verify',results.get('apply_error',''))
                    self.assertNotIn('opposing',results.get('apply_error',''));e.popen.assert_not_called()
                finally:
                    release_close.set();first.join(3)
                    if second.ident is not None:second.join(3)


if __name__=='__main__':unittest.main()
