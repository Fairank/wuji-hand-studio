"""Cold stop uncertainty gates motion before dispatch or reservation changes.

The transport models SDK status/ACK delivery only. Actual worker/vendor enable
ordering is independently reproduced by the installed-code SDK actors.
"""
import copy
import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from console_server import Controller
from device_profiles import profile
from device_termination import TerminationJournal
from gesture_library import CATALOG


SERIAL='SYNTHETIC-COLD-UNKNOWN'
OTHER='SYNTHETIC-COLD-OTHER'


class DeferredThread:
    """Thread-creation boundary; no playlist/watchdog body runs in this test."""
    def __init__(self,**kwargs):self.kwargs=kwargs
    def start(self):pass
    def is_alive(self):return False
    def join(self,timeout=None):pass


class StatusTransport:
    agent_command='synthetic-cold-journal-status'
    parameters_in_launch=True

    def __init__(self,serial):
        self.serial=serial;self.lines=queue.Queue();self.sent=[]
        self.enable_receipts=0;self.disable_receipts=0;self.closed=False
        self.lease=None;self.sequence=0;self.channel=type('Channel',(),{'closed':False})()

    def emit(self,event):self.lines.put('WUJI_JSON:'+json.dumps(event)+'\n')

    def ready(self,*,active=False,confirmed=False):
        self.sequence+=1
        hardware=dict(active=active,stop_confirmed=confirmed,ready=True,actions=['fist','open'],
            probe_ready=True,trial_ready=True,trial_controls_version=3,gesture_library_version=4,
            group_sync_version=1,controller_clock_id='synthetic-cold-guard',handled_lease=self.lease,
            reason='Synthetic SDK feedback')
        self.emit(dict(type='state',device_id=self.serial,connection='connected',message='Synthetic SDK ready',recording=dict(active=False),
            latest=dict(seq=self.sequence,host_s=time.monotonic(),device_timestamp_us=self.sequence*1000,
                joints=[dict(nid=i+1,position_rad=0.,velocity_rad_s=0.,effort_A=0.) for i in range(20)]),
            metrics=dict(host_hz=100.,device_hz=100.,age_ms=0.),joint_rates=[],
            hardware=hardware))

    def exec_command(self,command,timeout=None):
        if command!=self.agent_command:raise AssertionError('Unexpected transport command')
        transport=self
        class Input:
            def write(self,text):
                for line in text.splitlines():
                    if not line:continue
                    item=json.loads(line);transport.sent.append(item)
                    if item['name']=='connect':
                        transport.emit(dict(type='identity',device_id=transport.serial,profile='hand2_left'))
                        transport.ready()
                    elif item['name']=='hardware_stop':
                        transport.disable_receipts+=1;transport.ready(confirmed=True)
                    elif item['name'] in ('hardware_start','hardware_probe','hardware_trial'):
                        transport.enable_receipts+=1;transport.lease=item['lease']
                        transport.ready(active=True)
                return len(text)
            def flush(self):pass
        class Output:
            channel=transport.channel
            def __iter__(self):
                while True:
                    line=transport.lines.get()
                    if line is None:return
                    yield line
        return Input(),Output(),io.BytesIO()

    def close(self):
        if not self.closed:
            self.closed=True;self.channel.closed=True;self.lines.put(None)
        return True


class ColdStopJournalGuardTests(unittest.TestCase):
    def setUp(self):
        metadata=patch('managed_runtime.registered',return_value=None);metadata.start();self.addCleanup(metadata.stop)
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        seed=Controller(factory=lambda:None,reports=self.root/'reports')
        old_epoch=seed.session_epochs[0]
        seed.termination.observe('hand',SERIAL,dict(active=None,stop_confirmed=False),old_epoch)
        self.persisted=copy.deepcopy(seed.termination.snapshot())
        self.assertTrue(self.persisted)
        self.transport=StatusTransport(SERIAL)
        self.c=Controller(factory=lambda:self.transport,reports=self.root/'reports')
        self.assertEqual(self.c.termination.snapshot(),self.persisted)
        # A view/configuration boundary, not a motion or ownership replacement.
        self.c.state['device_profile']=profile('hand2_left')
        self.addCleanup(self.close_reader)
        self.c.action(dict(name='connect',serial=SERIAL,address='',auto_detect=True))
        self.wait(lambda:self.c.state['connection']=='connected' and not self.c.snapshot()['stale'])
        self.assertEqual(self.c.termination.snapshot(),self.persisted)

    def close_reader(self):
        self.transport.close()
        for record in list(self.c.feedback_sessions.values()):record['thread'].join(3)

    def wait(self,predicate):
        deadline=time.monotonic()+3
        while not predicate():
            if time.monotonic()>deadline:self.fail('Synthetic SDK status was not received')
            time.sleep(.001)

    def refresh(self):
        before=self.transport.sequence;self.transport.ready()
        self.wait(lambda:(self.c.state.get('latest') or {}).get('seq',0)>before)
        self.assertFalse(self.c.snapshot()['stale'])

    def evidence(self):
        return dict(rows=self.c.termination.snapshot(),disk=self.c.termination.path.read_bytes(),
            generation=self.c.generation,epochs=dict(self.c.session_epochs),
            devices=dict(self.c.session_devices),lease=self.c.hardware_lease,
            sent=copy.deepcopy(self.transport.sent),enable=self.transport.enable_receipts,
            disable=self.transport.disable_receipts)

    def acknowledge(self):
        self.c.action(dict(name='hardware_stop'))
        self.wait(lambda:self.c.state['hardware'].get('stop_confirmed') is True and not self.c.hardware_lease)
        self.assertEqual(self.c.termination.snapshot(),[])
        self.assertEqual(TerminationJournal(self.c.termination.path).snapshot(),[])

    def group_command(self,mode='hardware'):
        return dict(name='group_prepare',token='a'*32,mode=mode,kind='pair',action='pair_wave',
            speed=1,cycles=1,amplitude=.25,workspace_clear=True)

    def program_command(self,mode='hardware'):
        dance=next(row['id'] for row in CATALOG if row['group']=='dance')
        return dict(name='program_start',mode=mode,amplitude=.25,workspace_clear=True,
            plan=dict(entries=[dict(action=dance,speed=1,cycles=1)],repeats=1))

    def test_cold_read_only_ready_does_not_confirm_old_stop(self):
        self.assertIs(self.c.state['hardware']['active'],False)
        self.assertTrue(self.c.state['hardware']['probe_ready'])
        self.assertFalse(self.c.state['hardware']['stop_confirmed'])
        self.assertEqual(self.transport.enable_receipts,0)
        self.assertEqual(self.transport.disable_receipts,0)
        self.assertEqual(TerminationJournal(self.c.termination.path).snapshot(),self.persisted)

    def assert_motion_rejected(self,command):
        self.refresh();before=self.evidence()
        with self.assertRaises(ValueError):self.c.action(command)
        self.assertEqual(self.evidence(),before)

    def test_hardware_start_rejects_before_send_or_lease_change(self):
        self.assert_motion_rejected(dict(name='hardware_start',action='fist',speed=1,cycles=1))

    def test_hardware_probe_rejects_before_send_or_lease_change(self):
        self.assert_motion_rejected(dict(name='hardware_probe',index=6,direction=1,workspace_clear=True))

    def test_hardware_trial_rejects_before_send_or_lease_change(self):
        self.assert_motion_rejected(dict(name='hardware_trial',action='open',speed=1,amplitude=.25,cycles=1,workspace_clear=True))

    def test_trial_rechecks_new_uncertainty_after_profile_validation(self):
        from demonstration_scope import require_expected_profile
        self.acknowledge();self.refresh();before=self.evidence()
        checked=0;late_rows=[]
        def validate_then_observe(*args,**kwargs):
            nonlocal checked
            require_expected_profile(*args,**kwargs);checked+=1
            if checked==2:
                # The first stop gate passed; another device reports unknown
                # while the final target/profile check is being completed.
                self.c.termination.observe('glove','LATE',
                    dict(active=None,stop_confirmed=False),self.c.termination.new_session())
                late_rows.extend(self.c.termination.snapshot())
        command=dict(name='hardware_trial',action='open',speed=1,amplitude=.25,
            cycles=1,workspace_clear=True,expected_profile='hand2_left',expected_serial=SERIAL)
        with patch('demonstration_scope.require_expected_profile',side_effect=validate_then_observe):
            with self.assertRaisesRegex(ValueError,'unconfirmed'):self.c.action(command)
        self.assertEqual(checked,2)
        self.assertEqual(self.c.termination.snapshot(),late_rows)
        self.assertEqual(TerminationJournal(self.c.termination.path).snapshot(),late_rows)
        self.assertEqual([(row['source'],row['device_id']) for row in late_rows],[('glove','LATE')])
        after=self.evidence()
        for key in ('generation','epochs','devices','lease','sent','enable','disable'):
            self.assertEqual(after[key],before[key],key)

    def test_group_prepare_rejects_before_reservation_or_watchdog(self):
        self.refresh();before=self.evidence()
        reservation=(self.c.group.token,self.c.group.mode,self.c.group.active,
            copy.deepcopy(self.c.group.spec),self.c.group.watch,self.c.group.heartbeat)
        with patch('group_participant.threading.Thread',side_effect=AssertionError('Unexpected watchdog creation')) as factory:
            with self.assertRaises(ValueError):self.c.action(self.group_command())
            factory.assert_not_called()
        self.assertEqual(self.evidence(),before)
        self.assertEqual((self.c.group.token,self.c.group.mode,self.c.group.active,
            self.c.group.spec,self.c.group.watch,self.c.group.heartbeat),reservation)

    def test_program_rejects_before_run_lease_cancel_or_thread_changes(self):
        self.refresh();self.c.program.cancel.set();before=self.evidence()
        prior=(self.c.program.snapshot(),self.c.program.lease,self.c.program.thread,self.c.program.heartbeat)
        with patch('program_runner.threading.Thread',side_effect=AssertionError('Unexpected playlist thread')) as factory:
            with self.assertRaises(ValueError):self.c.action(self.program_command())
            factory.assert_not_called()
        self.assertTrue(self.c.program.cancel.is_set())
        self.assertEqual((self.c.program.snapshot(),self.c.program.lease,self.c.program.thread,self.c.program.heartbeat),prior)
        self.assertEqual(self.evidence(),before)

    def test_same_serial_explicit_disable_ack_allows_each_hardware_entry(self):
        self.acknowledge()
        commands=[dict(name='hardware_start',action='fist',speed=1,cycles=1),
            dict(name='hardware_probe',index=6,direction=1,workspace_clear=True),
            dict(name='hardware_trial',action='open',speed=1,amplitude=.25,cycles=1,workspace_clear=True)]
        for command in commands:
            with self.subTest(name=command['name']):
                self.refresh();before=self.transport.enable_receipts
                self.c.action(command)
                self.wait(lambda:self.c.state['hardware'].get('active') is True)
                self.assertEqual(self.transport.enable_receipts,before+1)
                self.acknowledge()

    def test_other_serial_disable_ack_cannot_clear_original_uncertainty(self):
        self.c.action(dict(name='disconnect'))
        self.close_reader()
        self.transport=StatusTransport(OTHER)
        self.c.factory=lambda:self.transport
        self.c.action(dict(name='connect',serial=OTHER,address='',auto_detect=True))
        self.wait(lambda:self.c.state.get('device_id')==OTHER and not self.c.snapshot()['stale'])
        self.assertEqual(self.c.termination.snapshot(),self.persisted)
        self.c.action(dict(name='hardware_stop'))
        self.wait(lambda:self.c.state['hardware'].get('stop_confirmed') is True)
        self.assertEqual(self.c.termination.snapshot(),self.persisted)
        self.refresh();before=self.evidence()
        with self.assertRaises(ValueError):
            self.c.action(dict(name='hardware_probe',index=6,direction=1,workspace_clear=True))
        self.assertEqual(self.evidence(),before)

    def test_same_serial_ack_allows_group_and_program_reservations(self):
        self.acknowledge();self.refresh()
        with patch('group_participant.threading.Thread',DeferredThread):
            result=self.c.action(self.group_command())
        self.assertEqual(result['group']['token'],'a'*32)
        self.c.action(dict(name='group_abort',token='a'*32))
        self.wait(lambda:self.c.state['hardware'].get('stop_confirmed') is True)
        with patch('program_runner.threading.Thread',DeferredThread):
            result=self.c.action(self.program_command())
        self.assertIn('program_run_id',result)
        self.assertTrue(self.c.program.snapshot()['active'])

    def test_preview_and_passive_keepalive_do_not_clear_or_block_on_history(self):
        before=self.c.termination.snapshot();sent=len(self.transport.sent)
        self.assertTrue(self.c.action(dict(name='session_keepalive'))['kept'])
        self.assertEqual(len(self.transport.sent),sent)
        with patch('group_participant.threading.Thread',DeferredThread):
            self.assertEqual(self.c.action(self.group_command('preview'))['group']['mode'],'preview')
        self.c.action(dict(name='group_abort',token='a'*32))
        with patch('program_runner.threading.Thread',DeferredThread):
            result=self.c.action(self.program_command('preview'))
        self.assertIn('program_run_id',result)
        self.assertEqual(self.c.termination.snapshot(),before)
        self.assertEqual(self.transport.enable_receipts,0)
        self.assertEqual(self.transport.disable_receipts,0)


if __name__=='__main__':unittest.main()
