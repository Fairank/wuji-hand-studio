"""Exact start/stop ownership with real Controller paths and synthetic streams."""
import copy
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

from console_server import Controller
from desktop_lifecycle import close_sessions

SN='SYNTHETIC-HAND-PENDING'


class PendingStartTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.c=Controller(factory=Mock(side_effect=AssertionError('No real transport')),
                          reports=Path(self.folder.name)/'reports')
        self.c.stdin=io.StringIO()
        self.c.state.update(connection='connected',device_id=SN,
                            identified_device=dict(source='sdk_identity',serial=SN,profile='hand2_left'),
                            latest={'joints':[]},metrics={'age_ms':0})
        self.c.state['hardware'].update(probe_ready=True,trial_controls_version=3,stop_confirmed=True)
        self.c.last_update=time.monotonic()
        self.c.session_devices[self.c.generation]=SN
        self.command=dict(name='hardware_probe',index=6,direction=1,workspace_clear=True)

    def start(self):return self.c.action(self.command)['lease']

    def observe(self,*,active=False,confirmed=True,handled=None,generation=None,closed=False,serial=SN):
        hardware=dict(active=active,stop_confirmed=confirmed,probe_ready=True,trial_controls_version=3)
        if handled is not None:hardware['handled_lease']=handled
        event=dict(type='closed' if closed else 'state',device_id=serial,hardware=hardware)
        if not closed:
            event.update(connection='connected',message='synthetic feedback',latest={'joints':[]},
                         metrics={'age_ms':0},recording={'active':False},joint_rates=[])
        self.c.event(event,self.c.generation if generation is None else generation)

    def test_pending_start_is_unknown_in_live_state_and_persistent_journal(self):
        self.start()
        state=self.c.snapshot()
        self.assertIs(state['hardware']['start_pending'],True)
        self.assertIsNone(state['hardware']['active'])
        self.assertIs(state['hardware']['stop_confirmed'],False)
        self.assertIs(state['read_only'],False)
        self.assertEqual([(r['device_id'],r['session']) for r in state['unconfirmed_devices']],
                         [(SN,self.c.session_epochs[self.c.generation])])

    def test_old_idle_or_mismatched_handled_lease_cannot_expire_start(self):
        lease=self.start();self.c.hardware_requested_at=time.monotonic()-60
        for handled in (None,'b'*32):
            with self.subTest(handled=handled):
                self.observe(handled=handled)
                self.assertEqual(self.c.hardware_lease,lease)
                self.assertIsNone(self.c.state['hardware']['active'])
                self.assertTrue(self.c.termination.snapshot())
                with self.assertRaises(ValueError):self.c.action(self.command)

    def test_acknowledged_rejection_releases_only_this_start(self):
        self.c.termination.observe('hand','SYNTHETIC-OLDER',{'active':None},0)
        lease=self.start();self.observe(handled=lease)
        self.assertIsNone(self.c.hardware_lease)
        self.assertIs(self.c.snapshot()['hardware']['start_pending'],False)
        self.assertNotIn('handled_lease',self.c.snapshot()['hardware'])
        self.assertEqual([r['device_id'] for r in self.c.termination.snapshot()],['SYNTHETIC-OLDER'])
        self.assertIs(self.c.state['read_only'],True)

    def test_old_session_or_wrong_device_ack_cannot_release_current_start(self):
        lease=self.start()
        self.observe(handled=lease,generation=self.c.generation-1)
        self.assertEqual(self.c.hardware_lease,lease)
        self.observe(handled=lease,serial='SYNTHETIC-OTHER')
        self.assertEqual(self.c.hardware_lease,lease)
        self.assertIn(SN,[r['device_id'] for r in self.c.termination.snapshot()])

    def test_previously_active_unknown_stop_retains_lease_and_journal(self):
        lease=self.start();self.observe(active=True,confirmed=False,handled=lease)
        self.observe(active=None,confirmed=False,handled=lease)
        self.assertEqual(self.c.hardware_lease,lease)
        self.assertTrue(self.c.termination.snapshot())
        with self.assertRaises(ValueError):self.c.action(dict(name='disconnect'))
        self.observe(handled=lease)
        self.assertIsNone(self.c.hardware_lease)
        self.assertFalse(self.c.termination.snapshot())

    def test_exact_terminal_close_releases_pending_without_fabricating_old_close(self):
        lease=self.start()
        self.observe(closed=True,generation=self.c.generation-1)
        self.assertEqual(self.c.hardware_lease,lease)
        self.observe(closed=True)
        self.assertIsNone(self.c.hardware_lease)
        self.assertFalse(self.c.termination.snapshot())
        self.assertIs(self.c.state['read_only'],True)

    def test_pending_disconnect_and_reconnect_do_not_drop_stream_or_reset_owner(self):
        lease=self.start();stream=self.c.stdin;generation=self.c.generation
        self.c.state['connection']='error'
        with patch('console_server.threading.Timer') as timer,patch('console_server.threading.Thread') as thread:
            for name in ('disconnect','connect'):
                with self.subTest(name=name),self.assertRaises(ValueError):self.c.action(dict(name=name))
            timer.assert_not_called();thread.assert_not_called()
        self.assertIs(self.c.stdin,stream)
        self.assertEqual(self.c.generation,generation)
        self.assertEqual(self.c.hardware_lease,lease)
        self.assertEqual([__import__('json').loads(line)['name'] for line in stream.getvalue().splitlines()],['hardware_probe'])

    def test_legacy_shared_preview_is_blocked_but_independent_display_preview_survives(self):
        self.start()
        for command in (dict(name='view_source',source='preview'),
                        dict(name='demo_start',action='fist',speed=.5,cycles=1)):
            with self.subTest(command=command),self.assertRaises(ValueError):self.c.action(command)
        result=self.c.action(dict(name='demo_start',display_profile='hand1_right',action='fist',speed=.5,cycles=1))
        self.assertTrue(result['demonstration_playback']['hand1_right']['active'])
        self.assertEqual(self.c.state['view_source'],'feedback')

    def test_partial_stream_flush_failure_retains_exact_unknown_ownership(self):
        class DeliveredButUnconfirmed(io.StringIO):
            def flush(self):raise OSError('synthetic lost flush receipt')
        self.c.stdin=DeliveredButUnconfirmed()
        with self.assertRaises(OSError):self.start()
        self.assertIn('hardware_probe',self.c.stdin.getvalue())
        self.assertIsNotNone(self.c.hardware_lease)
        self.assertIsNone(self.c.state['hardware']['active'])
        self.assertTrue(self.c.termination.snapshot())
        self.assertIs(self.c.snapshot()['hardware']['start_pending'],True)

    def test_unsent_journal_failure_preserves_prior_evidence_and_allows_explicit_retry(self):
        self.c.termination.observe('hand','SYNTHETIC-OLDER',{'active':None},0)
        rows=copy.deepcopy(self.c.termination.rows)
        confirmed=dict(self.c.termination.confirmed)
        hardware=copy.deepcopy(self.c.state['hardware'])
        with patch.object(self.c.termination,'save',side_effect=OSError('synthetic full disk')):
            with self.assertRaises(OSError):self.start()
            self.assertTrue(self.c.termination.dirty)
        self.assertEqual(self.c.termination.rows,rows)
        self.assertEqual(self.c.termination.confirmed,confirmed)
        self.assertEqual(self.c.state['hardware'],hardware)
        self.assertIsNone(self.c.hardware_lease)
        self.assertEqual(self.c.stdin.getvalue(),'')
        self.assertIsInstance(self.start(),str)


class PendingExitTests(unittest.TestCase):
    def state(self,pending):
        return dict(connection='connected',hardware=dict(active=False,stop_confirmed=True,start_pending=pending),
                    glove={'busy':False},recording={},parameter_sync={})

    def controller(self,state):
        c=Mock();c.snapshot.return_value=state;c.doctor.snapshot.return_value={}
        return c

    def test_exit_waits_through_pre_request_idle_confirmation(self):
        pending=self.state(True);c=self.controller(pending)
        c.snapshot.side_effect=[pending,pending,self.state(False)]
        self.assertTrue(close_sessions(c,sleep=lambda _:None)['ok'])
        self.assertEqual(c.snapshot.call_count,3)
        self.assertEqual([call.args[0]['name'] for call in c.action.call_args_list],['hardware_stop','disconnect'])

    def test_pending_exit_timeout_never_disconnects(self):
        c=self.controller(self.state(True));ticks=iter([0,0,1])
        self.assertFalse(close_sessions(c,timeout=.5,clock=lambda:next(ticks),sleep=lambda _:None)['ok'])
        self.assertEqual([call.args[0]['name'] for call in c.action.call_args_list],['hardware_stop'])


if __name__=='__main__':unittest.main()
