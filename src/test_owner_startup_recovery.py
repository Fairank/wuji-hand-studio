"""Unlaunched reader/watchdog recovery, using local players and fake threads only."""
import copy
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from demo_player import DemoPlayer
from glove_bridge import GloveBridge
from group_participant import GroupParticipant
from test_calibration_portability import bundle_context


class DeferredThread:
    def __init__(self,target=None,args=(),**_):self.started=False
    def start(self):self.started=True
    def is_alive(self):return self.started
    def join(self,timeout=None):pass


class FailedThread(DeferredThread):
    def start(self):raise RuntimeError('synthetic startup unavailable')


def failure(kind):
    return dict(side_effect=RuntimeError('synthetic constructor unavailable')) if kind=='construct' else dict(side_effect=FailedThread)


class GloveReaderStartupRecoveryTests(unittest.TestCase):
    def test_construct_or_start_failure_has_no_connecting_claim_or_fake_sdk_ack_then_retry(self):
        for kind in ('construct','start'):
            with self.subTest(kind=kind):
                factory=Mock();bridge=GloveBridge(factory);first=bridge.session
                with patch('glove_bridge.threading.Thread',**failure(kind)),self.assertRaisesRegex(RuntimeError,'unavailable'):
                    bridge.action({'name':'glove_scan'})
                result=bridge.snapshot();self.assertEqual(result['connection'],'error');self.assertFalse(result['busy'])
                self.assertEqual(result['session_id'],first+1);self.assertIsNone(bridge.thread)
                self.assertTrue(result['session_cleanup']['reader_finished']);self.assertTrue(result['session_cleanup']['transport_closed'])
                self.assertFalse(result['session_cleanup']['acknowledged']);self.assertFalse(result['session_cleanup']['cleanup_confirmed'])
                self.assertFalse(bridge._worker_dispatch_attempted);self.assertIsNone(bridge._cleanup_error);factory.assert_not_called()
                with patch('glove_bridge.threading.Thread',DeferredThread):bridge.action({'name':'glove_scan'})
                self.assertEqual(bridge.session,first+2);self.assertTrue(bridge.busy);factory.assert_not_called()

    def test_live_reader_keeps_its_epoch_and_rejects_another_calibrated_session(self):
        factory=Mock();bridge=GloveBridge(factory)
        with patch('glove_bridge.threading.Thread',DeferredThread):bridge.action({'name':'glove_scan'})
        bridge.stdin=io.StringIO();before=bridge.snapshot();thread=bridge.thread
        with tempfile.TemporaryDirectory() as folder,patch('glove_bridge.threading.Thread') as spawn:
            context=bundle_context(Path(folder))
            with self.assertRaisesRegex(ValueError,'existing glove session'):
                bridge.start_calibrated(context,Mock(),expected_profile='hand2_right')
        spawn.assert_not_called();factory.assert_not_called();self.assertIs(bridge.thread,thread)
        self.assertEqual(bridge.snapshot()['session_id'],before['session_id']);self.assertEqual(bridge.stdin.getvalue(),'')

    def test_unconfirmed_previous_worker_cleanup_still_blocks_before_new_epoch(self):
        bridge=GloveBridge(Mock());bridge._worker_dispatch_attempted=True;bridge._cleanup_error='original release unknown'
        before=bridge.session
        with patch('glove_bridge.threading.Thread') as spawn,self.assertRaisesRegex(ValueError,'original release unknown'):
            bridge.action({'name':'glove_scan'})
        spawn.assert_not_called();self.assertEqual(bridge.session,before);self.assertTrue(bridge._worker_dispatch_attempted)


class ParticipantWatchStartupRecoveryTests(unittest.TestCase):
    def environment(self,mode='preview'):
        player=DemoPlayer();state=dict(connection='connected',stale=False,device_profile={'side':'left','generation':'hand2'},
            hardware={'active':False,'trial_ready':True,'group_sync_version':1},program={'active':False},glove={'busy':False},calibration={'running':False})
        controller=N(lock=threading.RLock(),snapshot=lambda:copy.deepcopy(state),state=state,hardware_lease=None,
            player=player,doctor=N(snapshot=lambda:{'running':False}))
        calls=[]
        def action(command):
            calls.append(copy.deepcopy(command))
            if command['name'].startswith('demo_'):player.command(command)
            else:raise AssertionError('No hardware command allowed in startup recovery')
        controller._action=action;participant=GroupParticipant(controller)
        command=dict(token='a'*32,mode=mode,kind='pair',action='pair_wave',speed=1.,cycles=1,amplitude=.5,workspace_clear=True)
        return participant,player,command,calls

    def test_construct_or_start_failure_releases_only_local_preview_or_readonly_prepare(self):
        for mode in ('preview','hardware'):
            for kind in ('construct','start'):
                with self.subTest(mode=mode,kind=kind):
                    participant,player,command,calls=self.environment(mode)
                    with patch('group_participant.threading.Thread',**failure(kind)),self.assertRaisesRegex(RuntimeError,'unavailable'):
                        participant.prepare(command)
                    self.assertFalse(participant.active);self.assertIsNone(participant.mode);self.assertIsNone(participant.watch)
                    self.assertFalse(player.active);self.assertFalse(participant.snapshot()['cleanup_required'])
                    self.assertEqual([row['name'] for row in calls],['demo_start'] if mode=='preview' else [])
                    before=len(calls);participant.handle({'name':'group_abort','token':command['token']})
                    self.assertEqual(len(calls),before)  # no motor stop after read-only prepare
                    with patch('group_participant.threading.Thread',DeferredThread):
                        participant.prepare(dict(command,token='b'*32))
                    self.assertTrue(participant.active);self.assertEqual(participant.token,'b'*32)
                    with self.assertRaisesRegex(ValueError,'Expired group token'):participant.handle({'name':'group_abort','token':'a'*32})

    def test_existing_active_participant_rejects_duplicate_without_touching_player(self):
        participant,player,command,calls=self.environment()
        with patch('group_participant.threading.Thread',DeferredThread):participant.prepare(command)
        old=player.run_id;token=participant.token;calls.clear()
        with patch('group_participant.threading.Thread') as spawn,self.assertRaisesRegex(ValueError,'Finish current sessions'):
            participant.prepare(dict(command,token='b'*32))
        spawn.assert_not_called();self.assertEqual(calls,[]);self.assertEqual(player.run_id,old);self.assertEqual(participant.token,token)

    def test_replacement_preview_is_not_stopped_or_committed_by_failed_old_prepare(self):
        participant,player,command,calls=self.environment();replacement={}
        def changed(**_):
            player.command({'name':'demo_start','action':'open','speed':1.,'cycles':1});replacement['id']=player.run_id
            raise RuntimeError('synthetic constructor unavailable')
        with patch('group_participant.threading.Thread',side_effect=changed),self.assertRaisesRegex(ValueError,'cleanup is unconfirmed'):
            participant.prepare(command)
        self.assertTrue(participant.active);self.assertTrue(participant.snapshot()['cleanup_required'])
        self.assertEqual(player.run_id,replacement['id']);self.assertTrue(player.active)
        for name in ('group_arm','group_commit','group_beat'):
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'cleanup is unconfirmed'):
                participant.handle({'name':name,'token':command['token']})
        with self.assertRaisesRegex(ValueError,'Preview changed'):participant.handle({'name':'group_abort','token':command['token']})
        self.assertEqual(player.run_id,replacement['id']);self.assertTrue(player.active)

    def test_local_stop_error_stays_protected_until_exact_preview_cleanup_can_retry(self):
        participant,player,command,calls=self.environment();original=player.command
        def failed_stop(value):
            if value['name']=='demo_stop':raise RuntimeError('synthetic local stop failure')
            return original(value)
        with patch.object(player,'command',side_effect=failed_stop),patch('group_participant.threading.Thread',FailedThread):
            with self.assertRaisesRegex(ValueError,'cleanup is unconfirmed'):participant.prepare(command)
        self.assertTrue(participant.active);self.assertTrue(player.active)
        participant.handle({'name':'group_abort','token':command['token']})
        self.assertFalse(player.active);self.assertFalse(participant.active);self.assertFalse(participant.snapshot()['cleanup_required'])
        with patch('group_participant.threading.Thread',DeferredThread):participant.prepare(dict(command,token='b'*32))
        self.assertTrue(participant.active)


if __name__=='__main__':unittest.main()
