"""Final open dispatch shares the exact purpose switch's cancel lock.

Real Switch and Bridge methods, isolated ZIP and in-memory command stream only.
No SDK, native process, device or network access occurs in this test module.
"""
import copy
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from calibration_portability import model_provenance
from connection_switch import ConnectionSwitch
from glove_bridge import GloveBridge
from test_calibration_portability import bundle_context


ATTEMPT='b'*32
REQUEST='c'*32


class DeferredThread:
    def __init__(self,*args,**kwargs):pass
    def start(self):pass
    def is_alive(self):return False


class OpenGuardTests(unittest.TestCase):
    def environment(self,folder,*,guard=True):
        controller=N(calibration_handover=N(workflow_lock=threading.Lock()),snapshot=lambda:{})
        switch=ConnectionSwitch(controller,clock=lambda:10.)
        switch.state.update(busy=True,phase='connecting',request_id=REQUEST,attempt_id=ATTEMPT)
        switch.deadline=50.
        # Cancellation's reply formatting is outside this lock-order test.
        switch.snapshot=Mock(side_effect=lambda state:copy.deepcopy(switch.state))
        bridge=GloveBridge(lambda:None)
        context=bundle_context(Path(folder)/'transfer')
        callback=Mock(return_value={'timeout_ms':650})
        with patch('glove_bridge.threading.Thread',DeferredThread):
            bridge.start_calibrated(context,callback,expected_profile='hand2_right',
                                    open_guard=(lambda:switch.open_guard(ATTEMPT)) if guard else None)
        bridge.stdin=io.StringIO()
        return switch,bridge,callback,context,{'receipt':model_provenance(context,'owner-1',False)}

    def test_cancel_after_model_ready_before_dispatch_sends_no_open(self):
        with tempfile.TemporaryDirectory() as folder:
            switch,bridge,callback,context,event=self.environment(folder)
            def ready(receipt):
                switch.cancel({'name':'connection_switch_cancel','request_id':REQUEST,'attempt_id':ATTEMPT})
                return {'timeout_ms':650}
            callback.side_effect=ready
            with self.assertRaisesRegex(ValueError,'cancel'):
                bridge._calibrated_ready(event,context)
            callback.assert_called_once_with(event['receipt'])
            self.assertEqual(bridge.stdin.getvalue(),'')
            self.assertIsNone(bridge._open_intent)
            self.assertFalse(bridge._open_pending)

    def test_retired_finished_and_expired_attempts_never_dispatch(self):
        for change in ({'attempt_id':'d'*32},{'phase':'connected'},{'busy':False},{'deadline':9.}):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as folder:
                switch,bridge,callback,context,event=self.environment(folder)
                if 'deadline' in change:switch.deadline=change['deadline']
                else:switch.state.update(change)
                with self.assertRaises(ValueError):bridge._calibrated_ready(event,context)
                self.assertEqual(bridge.stdin.getvalue(),'')
                callback.assert_called_once()

    def test_dispatch_already_holding_guard_finishes_before_cancel_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            switch,bridge,callback,context,event=self.environment(folder)
            entered=threading.Event();release=threading.Event();cancel_entered=threading.Event();cancel_done=threading.Event()
            wire=[];errors=[]
            class Stream:
                def write(self,data):
                    entered.set()
                    if not release.wait(2):raise AssertionError('QA dispatch barrier was not released')
                    wire.append(data);return len(data)
                def flush(self):pass
            bridge.stdin=Stream()
            def open_now():
                try:bridge._calibrated_ready(event,context)
                except Exception as error:errors.append(error)
            def cancel_now():
                cancel_entered.set()
                try:switch.cancel({'name':'connection_switch_cancel','request_id':REQUEST,'attempt_id':ATTEMPT})
                except Exception as error:errors.append(error)
                finally:cancel_done.set()
            opening=threading.Thread(target=open_now);cancelling=threading.Thread(target=cancel_now)
            opening.start()
            try:
                self.assertTrue(entered.wait(2))
                acquired=switch.lock.acquire(blocking=False)
                if acquired:switch.lock.release()
                self.assertFalse(acquired,'Open dispatch must still hold its switch guard')
                cancelling.start();self.assertTrue(cancel_entered.wait(2))
                self.assertFalse(cancel_done.is_set(),'Cancel cannot acknowledge ahead of an already-dispatched open')
            finally:
                release.set();opening.join(2)
                if cancelling.ident is not None:cancelling.join(2)
            self.assertFalse(opening.is_alive());self.assertFalse(cancelling.is_alive())
            self.assertEqual(errors,[]);self.assertTrue(cancel_done.is_set())
            self.assertEqual(len(wire),1);self.assertIn('"name": "glove_open"',wire[0])

    def test_legacy_calibrated_start_without_guard_keeps_existing_behavior(self):
        with tempfile.TemporaryDirectory() as folder:
            switch,bridge,callback,context,event=self.environment(folder,guard=False)
            bridge._calibrated_ready(event,context)
            self.assertIn('"name": "glove_open"',bridge.stdin.getvalue())
            callback.assert_called_once()

    def test_invalid_guard_factory_is_refused_before_starting_a_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge=GloveBridge(lambda:None);context=bundle_context(Path(folder)/'transfer')
            with patch('glove_bridge.threading.Thread') as worker,self.assertRaises(ValueError):
                bridge.start_calibrated(context,lambda receipt:{},open_guard=True)
            worker.assert_not_called()


if __name__=='__main__':unittest.main()
