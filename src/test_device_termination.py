import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from device_termination import TerminationJournal,unconfirmed
from desktop_lifecycle import needs_confirmation,close_sessions
from desktop_tools import idle


class TerminationTests(unittest.TestCase):
    def test_ended_glove_unknown_is_not_idle_or_confirmed_exit(self):
        state=dict(connection='disconnected',hardware=dict(active=False),
                   glove=dict(busy=False,hardware=dict(active=None,stop_confirmed=False),feedback=dict(device_id='OLD-HAND')))
        self.assertTrue(needs_confirmation(state));self.assertFalse(idle(state,{}))
        c=Mock();c.snapshot.return_value=state;c.doctor.snapshot.return_value={}
        result=close_sessions(c)
        self.assertFalse(result['ok']);c.action.assert_not_called()
        self.assertEqual(unconfirmed(state)[0]['device_id'],'OLD-HAND')

    def test_read_only_connection_and_other_device_ack_cannot_clear_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'journal.json';j=TerminationJournal(path)
            j.observe('glove','OLD',dict(active=None,stop_confirmed=False))
            j.observe('hand','NEW',dict(active=False,stop_confirmed=True))
            j.observe('hand','OLD',dict(active=False,stop_confirmed=False))
            self.assertEqual([r['device_id'] for r in TerminationJournal(path).snapshot()],['OLD'])
            j.observe('hand','OLD',dict(active=False,stop_confirmed=True))
            self.assertEqual(j.snapshot(),[])

    def test_serial_less_session_cannot_clear_a_different_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=TerminationJournal(Path(tmp)/'journal.json')
            j.observe('hand',None,dict(active=None),3)
            j.observe('hand',None,dict(active=False,stop_confirmed=True),4)
            self.assertEqual(len(j.snapshot()),1)
            j.observe('hand',None,dict(active=False,stop_confirmed=True),3)
            self.assertEqual(j.snapshot(),[])

    def test_reconnect_keeps_old_device_and_ignores_late_old_ui_state(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(lambda:None,Path(tmp)/'reports')
            c.state.update(connection='error',device_id='OLD')
            c.state['hardware'].update(active=None,stop_confirmed=False)
            c.session_devices[0]='OLD'
            with patch('console_server.threading.Thread') as thread:
                c.action(dict(name='connect',address=''))
                thread.return_value.start.assert_called_once()
            self.assertEqual([r['device_id'] for r in c.snapshot()['unconfirmed_devices']],['OLD'])
            c.event(dict(type='identity',device_id='NEW',profile='hand2_left'),c.generation)
            c.event(dict(type='closed',hardware=dict(active=False,stop_confirmed=True)),0)
            self.assertEqual(c.snapshot()['unconfirmed_devices'],[])
            self.assertEqual(c.state['device_id'],'NEW')
            self.assertEqual(c.state['connection'],'connecting')

    def test_late_disconnect_ack_confirms_same_serial_without_reconnecting(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(lambda:None,Path(tmp)/'reports')
            c.state.update(connection='error',device_id='OLD')
            c.state['hardware'].update(active=True)
            c.session_devices[0]='OLD'
            c.action(dict(name='disconnect'))
            self.assertEqual(len(c.snapshot()['unconfirmed_devices']),1)
            c.event(dict(type='closed',hardware=dict(active=False,stop_confirmed=True)),0)
            self.assertEqual(c.snapshot()['unconfirmed_devices'],[])
            self.assertIs(c.state['hardware']['active'],False)

    def test_new_glove_scan_preserves_ended_session_uncertainty(self):
        from glove_bridge import GloveBridge
        with tempfile.TemporaryDirectory() as tmp:
            j=TerminationJournal(Path(tmp)/'journal.json');b=GloveBridge(lambda:None,j)
            b.state.update(hardware=dict(active=None,stop_confirmed=False),feedback=dict(device_id='OLD'))
            with patch('glove_bridge.threading.Thread'):b.action(dict(name='glove_scan'))
            self.assertEqual([r['device_id'] for r in j.snapshot()],['OLD'])
            self.assertEqual(b.state['hardware'],{})


class RenderDemandTests(unittest.TestCase):
    def test_hidden_view_waits_until_image_request_without_touching_controller(self):
        from pose_view import PoseView
        c=Mock();view=PoseView.__new__(PoseView)
        view.lock=threading.Lock();view.demand=threading.Event();view.requested_until=0.
        view.jpeg=None;view.meta={};view.controller=c
        done=threading.Event()
        thread=threading.Thread(target=lambda:(view.wait_for_demand(),done.set()),daemon=True);thread.start()
        self.assertFalse(done.wait(.03));view.get();self.assertTrue(done.wait(.5));thread.join(.5)
        c.assert_not_called();c.snapshot.assert_not_called()


if __name__=='__main__':unittest.main()
