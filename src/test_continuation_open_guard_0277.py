"""Linearize exact continuation cancellation with the existing final open gate."""
from contextlib import contextmanager
import copy
import io
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from calibration_portability import model_provenance
from console_server import Controller
from glove_bridge import GloveBridge
import test_calibrated_teleoperation as continuation_fixture
import test_task_control as controller_fixture


class ContinuationOpenGuardTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        fixture=continuation_fixture.ContinuationTests()
        with fixture.environment() as e:
            c=N(lock=threading.RLock(),calibrated_teleoperation=e.service,connection_switch=N(_config=lambda:'fixture-config'),
                glove=N(start_calibrated=Mock(return_value=17)),_continuation_profile='hand2_right',
                sdk_session_configs={},_continuation_model_ready=Mock(return_value={'timeout_ms':250}))
            c.glove.snapshot=lambda:dict(session_id=17,busy=True,
                session_cleanup=dict(session_id=17,cleanup_confirmed=False))
            e.service.start_session=lambda bundle:Controller._start_calibrated_session(c,bundle)
            with patch('bridge_config.load_config',return_value={}),patch('official_device_discovery.use_native',return_value=False):
                e.service.execute(fixture.command(e),synchronous=True)
            call=c.glove.start_calibrated.call_args
            guard=call.kwargs.get('open_guard')
            self.assertTrue(callable(guard),'Fresh continuation must use the existing final Bridge open guard')
            bridge=GloveBridge(Mock());bridge.session=17;bridge.thread=N(is_alive=lambda:True)
            bridge.stdin=io.StringIO();bridge.state['profile']='hand2_right'
            context=copy.deepcopy(call.args[0]);bridge._continuation=context
            bridge._on_model_ready=call.args[1];bridge._open_guard=guard;bridge._send_open=Mock()
            receipt=model_provenance(context,context['source_user_id'],True)
            e.controller=c;e.bridge=bridge;e.context=context;e.event=dict(type='calibrated_ready',receipt=receipt)
            e.expected={key:e.service.state[key] for key in ('run_id','attempt_id')}
            yield e

    def test_completed_start_return_still_allows_normal_late_ready_without_scope_in_wire(self):
        with self.environment() as e:
            self.assertFalse(e.service.state['busy'])
            e.bridge._calibrated_ready(e.event,e.context)
            e.bridge._send_open.assert_called_once()
            command=e.bridge._send_open.call_args.args[0]
            self.assertEqual(set(command),{'name','serial','user_id','timeout_ms'})
            self.assertEqual(command['serial'],e.context['serial'])
            e.controller._continuation_model_ready.assert_called_once();e.stop.assert_not_called()

    def test_cancel_before_late_ready_prevents_settings_callback_and_open(self):
        with self.environment() as e:
            e.service.cancel(e.expected)
            with self.assertRaisesRegex(ValueError,'cancelled'):
                e.bridge._calibrated_ready(e.event,e.context)
            e.controller._continuation_model_ready.assert_not_called();e.bridge._send_open.assert_not_called()
            e.stop.assert_called_once_with(17,pending_only=True)

    def test_cancel_after_settings_callback_still_prevents_the_final_open(self):
        with self.environment() as e:
            def ready(receipt,**kwargs):
                e.service.cancel(e.expected)
                return {'timeout_ms':250}
            e.controller._continuation_model_ready.side_effect=ready
            with self.assertRaisesRegex(ValueError,'cancelled'):
                e.bridge._calibrated_ready(e.event,e.context)
            e.controller._continuation_model_ready.assert_called_once();e.bridge._send_open.assert_not_called()
            e.stop.assert_called_once_with(17,pending_only=True)

    def test_old_attempt_ready_cannot_change_or_stop_a_replacement(self):
        with self.environment() as e:
            with e.service.lock:e.service.state.update(attempt_id='e'*32,sdk_session=18,busy=False)
            with self.assertRaisesRegex(ValueError,'changed'):
                e.bridge._calibrated_ready(e.event,e.context)
            e.controller._continuation_model_ready.assert_not_called();e.bridge._send_open.assert_not_called()
            e.stop.assert_not_called();self.assertEqual(e.service.state['sdk_session'],18)

    def test_cancel_ack_waits_for_an_already_admitted_open_write(self):
        with self.environment() as e:
            write=threading.Event();release=threading.Event();cancel_started=threading.Event();cancel_done=threading.Event()
            ordering=[];errors=[]
            def send(command):
                write.set()
                if not release.wait(3):raise AssertionError('Test did not release write')
                ordering.append('open-write-returned')
            e.bridge._send_open.side_effect=send
            def ready():
                try:e.bridge._calibrated_ready(e.event,e.context)
                except Exception as error:errors.append(error)
            def cancel():
                cancel_started.set()
                try:e.service.cancel(e.expected);ordering.append('cancel-ack')
                except Exception as error:errors.append(error)
                finally:cancel_done.set()
            first=threading.Thread(target=ready);second=threading.Thread(target=cancel)
            first.start()
            try:
                self.assertTrue(write.wait(2));second.start();self.assertTrue(cancel_started.wait(2))
                self.assertFalse(cancel_done.wait(.05),'Cancel cannot ACK ahead of the open write already holding its guard')
                release.set();first.join(3);second.join(3)
                self.assertFalse(first.is_alive());self.assertFalse(second.is_alive());self.assertEqual(errors,[])
                self.assertEqual(ordering,['open-write-returned','cancel-ack'])
            finally:
                release.set();first.join(3)
                if second.ident is not None:second.join(3)

    def test_cancel_after_owner_validation_still_prevents_mapping_file_mutation(self):
        fixture=controller_fixture.ControllerContinuationTests()
        with fixture.environment() as e:
            c=e.c;receipt=fixture.receipt(c);service=c.calibrated_teleoperation
            with service.lock:service.state.update(run_id=receipt['source_run_id'],attempt_id='b'*32,sdk_session=17,busy=False)
            original_lock=c.glove.lock
            @contextmanager
            def cancelled_after_glove_read():
                with original_lock:yield
                service._cancel.set()
            guard=lambda:service.open_guard(receipt['source_run_id'],'b'*32)
            before={str(path.relative_to(e.root)):path.read_bytes() for path in e.root.rglob('*') if path.is_file()}
            camera=c.state['view_source']
            with patch.object(c.glove,'lock',cancelled_after_glove_read()):
                with self.assertRaisesRegex(ValueError,'cancelled'):
                    c._continuation_model_ready(receipt,_open_guard=guard)
            self.assertEqual({str(path.relative_to(e.root)):path.read_bytes() for path in e.root.rglob('*') if path.is_file()},before)
            self.assertEqual(c.state['view_source'],camera)


if __name__=='__main__':unittest.main()
