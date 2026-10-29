"""Real controller dispatch with inert callbacks: no transport or hardware."""
import copy
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

import console_server
from parameter_store import ParameterStore
from test_task_launch_recovery import Deferred,failing


class ControllerLaunchRecovery(unittest.TestCase):
    def owner(self,folder):
        owner=object.__new__(console_server.Controller)
        owner.lock=threading.RLock();owner.group=N(active=False);owner.calibration=N(run={'running':False})
        owner.glove=N(busy=False)
        owner.doctor=N(snapshot=lambda:{'running':False})
        owner.program=N(snapshot=lambda:{'active':False},thread=None)
        source=Path(folder)/'motion_parameters.py';source.write_bytes((Path(__file__).parent/'motion_parameters.py').read_bytes())
        owner.parameters=ParameterStore(source);owner.parameter_worker=None
        owner.state=dict(connection='disconnected',hardware={'active':False,'ready':False},recording={'active':False},device_id=None)
        owner.generation=0;owner.hardware_generation=0;owner.session_epochs={0:10}
        owner.termination=Mock();owner.termination.new_session.side_effect=range(11,20)
        owner.feedback_sessions={0:{'serial':'prior-untouched'}};owner.hardware_lease=None
        owner.log=Mock();owner.read_session=Mock()
        owner.factory=Mock(return_value=N(parameters_in_launch=True,close=Mock()))
        return owner

    def test_feedback_reader_launch_failure_preserves_old_records_and_retries(self):
        for boundary in ('construct','start'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as folder:
                owner=self.owner(folder);previous=copy.deepcopy(owner.feedback_sessions)
                with patch.object(console_server.threading,'Thread',failing(boundary)),self.assertRaisesRegex(RuntimeError,'synthetic worker'):
                    owner._action(dict(name='connect',serial='WH-QA-ONLY'))
                self.assertEqual(owner.state['connection'],'disconnected');self.assertFalse(owner.state['hardware']['active'])
                self.assertEqual(owner.feedback_sessions,previous);self.assertEqual(owner.session_epochs,{0:10})
                self.assertEqual(owner.hardware_generation,0);owner.factory.assert_not_called();owner.read_session.assert_not_called()
                with patch.object(console_server.threading,'Thread',Deferred):owner._action(dict(name='connect',serial='WH-QA-ONLY'))
                self.assertEqual(owner.state['connection'],'connecting');self.assertEqual(len(owner.feedback_sessions),2)
                record=owner.feedback_sessions[owner.generation];self.assertTrue(record['thread'].is_alive())

    def test_failed_feedback_launch_does_not_erase_an_uncertain_previous_hardware_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            owner=self.owner(folder);owner.state['hardware']=dict(active=None,stop_confirmed=False,reason='original uncertain stop')
            with patch.object(console_server.threading,'Thread',failing('start')),self.assertRaises(RuntimeError):
                owner._action(dict(name='connect',serial='WH-QA-ONLY'))
            self.assertIsNone(owner.state['hardware']['active']);self.assertFalse(owner.state['hardware']['stop_confirmed'])
            self.assertEqual(owner.feedback_sessions,{0:{'serial':'prior-untouched'}});self.assertEqual(owner.hardware_generation,0)

    def test_parameter_sync_launch_failure_keeps_saved_values_and_retry_completes(self):
        for boundary in ('construct','start'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as folder:
                owner=self.owner(folder);before=owner.parameters.path.read_bytes();revision=owner.parameters.snapshot()['revision']
                with patch.object(console_server.threading,'Thread',failing(boundary)),self.assertRaisesRegex(RuntimeError,'synthetic worker'):
                    owner._action(dict(name='parameters_sync',revision=revision))
                self.assertFalse(owner.parameters.sync_status['busy']);self.assertEqual(owner.parameters.sync_status['state'],'failed')
                self.assertIsNone(owner.parameter_worker);self.assertEqual(owner.parameters.path.read_bytes(),before);owner.factory.assert_not_called()
                with patch.object(console_server.threading,'Thread',Deferred):owner._action(dict(name='parameters_sync',revision=revision))
                self.assertTrue(owner.parameters.sync_status['busy']);owner.parameter_worker.finish()
                self.assertFalse(owner.parameters.sync_status['busy']);self.assertEqual(owner.parameters.sync_status['state'],'synced')
                self.assertEqual(owner.parameters.path.read_bytes(),before);owner.factory.return_value.close.assert_called_once()


if __name__=='__main__':unittest.main()
