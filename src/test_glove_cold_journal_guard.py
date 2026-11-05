"""Persisted unknown stops block a new follow before any dispatch mutation."""
import copy
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from device_termination import TerminationJournal
from glove_bridge import GloveBridge
from glove_protocol import CancellationNotDispatched
from sdk_release import requirement
from test_sdk_lifecycle import proof_fixture, release_fixture


class GloveColdJournalGuardTests(unittest.TestCase):
    def bridge(self, journal=None, serial='CURRENT-HAND'):
        bridge=GloveBridge(Mock(side_effect=AssertionError('No transport open')),journal,
                           release_resolver=release_fixture)
        bridge.thread=SimpleNamespace(is_alive=lambda:True)
        bridge.stdin=io.StringIO()
        bridge.last_update=time.monotonic()
        bridge.state.update(connection='receiving',profile='hand2_left',glove_serial='GLOVE-A',
            user={'user_id':'person-A'},can_prepare=True,
            stream=dict(fresh=True,age_ms=0,timeout_ms=250,solver=dict(engine='sdk',applied=True)),
            hardware=dict(active=False,probe_ready=True,stop_confirmed=False),
            feedback=dict(device_id=serial,metrics=dict(age_ms=0)))
        bridge._open_intent=dict(session=bridge.session,serial='GLOVE-A',user_id='person-A',profile='hand2_left')
        bridge._sdk_proof=proof_fixture()
        bridge._follow_sdk_requirement=requirement(release_fixture())
        bridge._follow_sdk_proof=copy.deepcopy(bridge._sdk_proof)
        bridge._follow_sdk_session=bridge.session
        return bridge

    def journal(self, path, device='OLD-HAND'):
        journal=TerminationJournal(path)
        session=journal.new_session()
        journal.observe('hand',device,dict(active=None,stop_confirmed=False),session)
        return TerminationJournal(path),session

    def refused_without_dispatch(self, bridge,reason='Device disable is unconfirmed'):
        before={name:copy.deepcopy(getattr(bridge,name)) for name in (
            'session','action_revision','lease','_open_intent','_follow_device','_follow_session',
            '_follow_unconfirmed','_follow_sdk_requirement','_follow_sdk_proof','_follow_sdk_session')}
        had_requested=hasattr(bridge,'requested')
        original=bridge.stdin.getvalue()
        with patch('glove_bridge.secrets.token_hex',return_value='a'*32) as token:
            with self.assertRaisesRegex(ValueError,reason):
                bridge.action(dict(name='glove_follow',workspace_clear=True))
        token.assert_not_called()
        self.assertEqual(bridge.stdin.getvalue(),original)
        self.assertEqual(hasattr(bridge,'requested'),had_requested)
        self.assertEqual({name:getattr(bridge,name) for name in before},before)

    def test_cold_foreign_uncertainty_refuses_before_lease_intent_or_send(self):
        with tempfile.TemporaryDirectory() as folder:
            journal,_=self.journal(Path(folder)/'journal.json')
            bridge=self.bridge(journal)
            rows=journal.snapshot()
            self.refused_without_dispatch(bridge)
            self.assertEqual(journal.snapshot(),rows)
            bridge.factory.assert_not_called()

    def test_fresh_same_serial_readonly_feedback_cannot_resolve_cold_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            journal,_=self.journal(Path(folder)/'journal.json','CURRENT-HAND')
            bridge=self.bridge(journal)
            self.assertFalse(bridge.snapshot()['feedback']['stale'])
            self.assertTrue(bridge.snapshot()['hardware']['probe_ready'])
            self.refused_without_dispatch(bridge)
            self.assertEqual(journal.snapshot()[0]['device_id'],'CURRENT-HAND')

    def test_corrupt_journal_and_serial_less_uncertainty_both_refuse(self):
        for corrupt in (True,False):
            with self.subTest(corrupt=corrupt),tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'journal.json'
                if corrupt:
                    path.write_text('{incomplete','utf8')
                    journal=TerminationJournal(path)
                else:journal,_=self.journal(path,None)
                bridge=self.bridge(journal)
                self.refused_without_dispatch(bridge)
                self.assertTrue(journal.snapshot())

    def test_only_exact_confirmed_stop_allows_explicit_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            journal,old_session=self.journal(Path(folder)/'journal.json')
            bridge=self.bridge(journal)
            journal.observe('hand','OTHER-HAND',dict(active=False,stop_confirmed=True),bridge.session)
            self.refused_without_dispatch(bridge)
            journal.observe('hand','OLD-HAND',dict(active=False,stop_confirmed=True),old_session-1)
            self.refused_without_dispatch(bridge)
            journal.observe('hand','OLD-HAND',dict(active=False,stop_confirmed=False),bridge.session)
            self.refused_without_dispatch(bridge)
            journal.observe('hand','OLD-HAND',dict(active=False,stop_confirmed=True),bridge.session)
            self.assertEqual(journal.snapshot(),[])
            result=bridge.action(dict(name='glove_follow',workspace_clear=True))
            sent=json.loads(bridge.stdin.getvalue())
            self.assertEqual(sent,dict(name='glove_follow',workspace_clear=True,lease=result['lease']))
            self.assertEqual(bridge._follow_device,'CURRENT-HAND')
            self.assertEqual(bridge._follow_session,bridge.session)
            self.assertEqual(bridge.action_revision,1)

    def test_uncertainty_added_after_sdk_verification_is_checked_at_follow(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TerminationJournal(Path(folder)/'journal.json')
            bridge=self.bridge(journal)
            bridge.verify_follow_sdk()
            journal.observe('hand','LATE-HAND',dict(active=None),journal.new_session())
            self.refused_without_dispatch(bridge)

    def test_stop_keepalive_and_readonly_preview_remain_available(self):
        for name in ('glove_stop','glove_keepalive','glove_hand_scan','glove_prepare'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as folder:
                journal,_=self.journal(Path(folder)/'journal.json')
                bridge=self.bridge(journal);rows=journal.snapshot();command=dict(name=name)
                if name=='glove_keepalive':
                    bridge.lease='a'*32;command['lease']=bridge.lease
                elif name=='glove_hand_scan':
                    bridge.state.update(hardware={},feedback={})
                elif name=='glove_prepare':command.update(serial='CURRENT-HAND',address='')
                bridge.action(command)
                self.assertEqual(json.loads(bridge.stdin.getvalue()),command)
                self.assertEqual(journal.snapshot(),rows)

    def test_clean_and_standalone_bridge_still_allow_explicit_follow(self):
        with tempfile.TemporaryDirectory() as folder:
            for journal in (None,TerminationJournal(Path(folder)/'journal.json')):
                with self.subTest(persistent=journal is not None):
                    bridge=self.bridge(journal)
                    result=bridge.action(dict(name='glove_follow',workspace_clear=True))
                    self.assertEqual(json.loads(bridge.stdin.getvalue())['lease'],result['lease'])
                    self.assertEqual(bridge._follow_device,'CURRENT-HAND')

    def test_old_browser_owner_is_refused_before_current_journal_or_send(self):
        with tempfile.TemporaryDirectory() as folder:
            journal,_=self.journal(Path(folder)/'journal.json');bridge=self.bridge(journal)
            with patch.object(journal,'snapshot',side_effect=AssertionError('Old request must not reach gate')):
                with self.assertRaises(CancellationNotDispatched):
                    bridge.action(dict(name='glove_follow',workspace_clear=True,
                                       expected_session=bridge.session+1,expected_revision=bridge.action_revision))
            self.assertEqual(bridge.stdin.getvalue(),'')
            self.assertIsNone(bridge.lease)

    def test_pending_follow_is_durable_before_first_transport_write(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'journal.json';journal=TerminationJournal(path)
            bridge=self.bridge(journal);during_write=[]
            class Stream(io.StringIO):
                def write(self,value):
                    during_write.append(TerminationJournal(path).snapshot())
                    return super().write(value)
            bridge.stdin=Stream()
            bridge.action(dict(name='glove_follow',workspace_clear=True))
            expected=dict(source='glove',device_id='CURRENT-HAND',session=bridge.session)
            self.assertTrue(any(all(row.get(key)==value for key,value in expected.items())
                                for row in during_write[0]))
            self.assertTrue(TerminationJournal(path).snapshot())

    def test_ordinary_prior_inactive_ack_cannot_erase_new_pending_follow(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TerminationJournal(Path(folder)/'journal.json');bridge=self.bridge(journal)
            bridge.action(dict(name='glove_follow',workspace_clear=True));lease=bridge.lease
            # Seed the required pending row independently to exercise receipt
            # handling even while the separate pre-dispatch-write test is red.
            journal.observe('glove','CURRENT-HAND',dict(active=None),bridge.session)
            state=copy.deepcopy(bridge.state)
            state['hardware'].update(active=False,stop_confirmed=True)
            for handled in (None,'b'*32):
                with self.subTest(handled=handled):
                    if handled is not None:state['hardware']['handled_lease']=handled
                    bridge.receive(dict(type='glove_state',state=state),session=bridge.session)
                    self.assertEqual(bridge.lease,lease)
                    self.assertTrue(journal.snapshot())

    def test_exact_pre_forward_rejection_releases_only_its_pending_row(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TerminationJournal(Path(folder)/'journal.json');bridge=self.bridge(journal)
            bridge.action(dict(name='glove_follow',workspace_clear=True));lease=bridge.lease
            journal.observe('glove','CURRENT-HAND',dict(active=None),bridge.session)
            journal.observe('hand','OTHER-HAND',dict(active=None),journal.new_session())
            confirmed=dict(journal.confirmed)
            bridge.receive(dict(type='glove_follow_rejected',device_id='CURRENT-HAND',
                                lease=lease,message='Synthetic pre-forward refusal'),session=bridge.session)
            self.assertIsNone(bridge.lease)
            self.assertEqual([row['device_id'] for row in journal.snapshot()],['OTHER-HAND'])
            self.assertEqual(journal.confirmed,confirmed)
            self.assertIs(bridge.state['hardware']['stop_confirmed'],False)

    def test_pending_save_failure_preserves_proof_and_never_allocates_or_sends(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'journal.json';journal=TerminationJournal(path);bridge=self.bridge(journal)
            proof=copy.deepcopy(bridge._follow_sdk_requirement);confirmed=dict(journal.confirmed)
            with patch.object(journal,'save',side_effect=OSError('Synthetic journal refusal')), \
                    patch('glove_bridge.secrets.token_hex') as token:
                with self.assertRaisesRegex(OSError,'journal refusal'):
                    bridge.action(dict(name='glove_follow',workspace_clear=True))
            token.assert_not_called()
            self.assertEqual(bridge.stdin.getvalue(),'');self.assertIsNone(bridge.lease)
            self.assertEqual(bridge.action_revision,0);self.assertIsNone(bridge._follow_device)
            self.assertEqual(bridge._follow_sdk_requirement,proof)
            self.assertEqual(journal.rows,[]);self.assertEqual(journal.confirmed,confirmed)
            self.assertTrue(journal.dirty);self.assertEqual(TerminationJournal(path).snapshot(),[])

    def test_partial_transport_failure_retains_pending_in_cold_journal(self):
        for boundary in ('write','flush'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'journal.json';journal=TerminationJournal(path);bridge=self.bridge(journal)
                class Stream(io.StringIO):
                    def write(self,value):
                        if boundary=='write':raise OSError('Synthetic partial delivery')
                        return super().write(value)
                    def flush(self):raise OSError('Synthetic partial delivery')
                bridge.stdin=Stream()
                with self.assertRaisesRegex(OSError,'partial delivery'):
                    bridge.action(dict(name='glove_follow',workspace_clear=True))
                rows=TerminationJournal(path).snapshot()
                self.assertEqual([(row['source'],row['device_id'],row['session']) for row in rows],
                                 [('glove','CURRENT-HAND',bridge.session)])
                self.assertTrue(bridge.snapshot()['follow_unconfirmed'])
                self.assertIsNone(bridge.snapshot()['hardware']['active'])

    def test_handled_start_ack_requires_current_epoch_serial_and_lease(self):
        for change in ('old_epoch','other_serial','wrong_lease','current'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as folder:
                journal=TerminationJournal(Path(folder)/'journal.json');bridge=self.bridge(journal)
                bridge.action(dict(name='glove_follow',workspace_clear=True));lease=bridge.lease
                state=copy.deepcopy(bridge.state);state['hardware'].update(active=False,stop_confirmed=True,handled_lease=lease)
                epoch=bridge.session
                if change=='old_epoch':epoch-=1
                elif change=='other_serial':state['feedback']['device_id']='OTHER-HAND'
                elif change=='wrong_lease':state['hardware']['handled_lease']='b'*32
                bridge.receive(dict(type='glove_state',state=state),session=epoch)
                if change=='current':
                    self.assertIsNone(bridge.lease);self.assertEqual(journal.snapshot(),[])
                    self.assertIs(bridge.snapshot()['hardware']['stop_confirmed'],True)
                else:self.assertEqual(journal.snapshot()[0]['device_id'],'CURRENT-HAND')

    def test_late_rejection_for_a_cannot_discard_b_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TerminationJournal(Path(folder)/'journal.json');bridge=self.bridge(journal)
            bridge.action(dict(name='glove_follow',workspace_clear=True))
            old=dict(type='glove_follow_rejected',device_id='CURRENT-HAND',lease=bridge.lease)
            bridge.receive(old,session=bridge.session)
            bridge.verify_follow_sdk();bridge.action(dict(name='glove_follow',workspace_clear=True));new=bridge.lease
            self.assertNotEqual(new,old['lease'])
            bridge.receive(old,session=bridge.session)
            self.assertEqual(bridge.lease,new);self.assertTrue(journal.snapshot())

    def test_rejection_save_failure_retains_lease_pending_and_foreign_row(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TerminationJournal(Path(folder)/'journal.json');bridge=self.bridge(journal)
            bridge.action(dict(name='glove_follow',workspace_clear=True));lease=bridge.lease
            journal.observe('glove','CURRENT-HAND',dict(active=None),journal.new_session())
            rows=journal.snapshot();confirmed=dict(journal.confirmed)
            event=dict(type='glove_follow_rejected',device_id='CURRENT-HAND',lease=lease)
            with patch.object(journal,'save',side_effect=OSError('Synthetic rollback save failure')):
                with self.assertRaisesRegex(OSError,'rollback save failure'):bridge.receive(event,session=bridge.session)
            self.assertEqual(bridge.lease,lease);self.assertEqual(journal.rows,rows)
            self.assertEqual(journal.confirmed,confirmed);self.assertTrue(journal.dirty)
            self.assertIsNone(bridge.snapshot()['hardware']['active'])

    def test_exact_terminal_cleanup_resolves_pending_but_retains_newer_row(self):
        from test_glove_cleanup_confirmation import cleanup
        with tempfile.TemporaryDirectory() as folder:
            journal=TerminationJournal(Path(folder)/'journal.json');bridge=self.bridge(journal)
            bridge.action(dict(name='glove_follow',workspace_clear=True))
            newer=journal.new_session();journal.observe('hand','CURRENT-HAND',dict(active=None),newer)
            bridge.receive(dict(type='glove_closed',device_id='CURRENT-HAND',hand_cleanup_confirmed=True,
                hardware=dict(active=False,stop_confirmed=True),
                session_cleanup=cleanup(glove_serial='GLOVE-A',sdk_user_id='person-A')),
                session=bridge.session)
            self.assertIsNone(bridge.lease);self.assertFalse(bridge.snapshot()['follow_unconfirmed'])
            self.assertEqual([(row['source'],row['session']) for row in journal.snapshot()],[('hand',newer)])

    def test_missing_target_identity_refuses_before_pending_or_dispatch(self):
        for serial in (None,'','   ',17):
            with self.subTest(serial=serial),tempfile.TemporaryDirectory() as folder:
                journal=TerminationJournal(Path(folder)/'journal.json');bridge=self.bridge(journal)
                bridge.state['feedback']['device_id']=serial
                with patch('glove_bridge.secrets.token_hex') as token:
                    with self.assertRaisesRegex(ValueError,'exact hand identity'):
                        bridge.action(dict(name='glove_follow',workspace_clear=True))
                token.assert_not_called();self.assertEqual(journal.snapshot(),[])
                self.assertEqual(bridge.stdin.getvalue(),'');self.assertIsNone(bridge.lease)

    def test_unknown_arriving_between_preflight_and_journal_transaction_refuses(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TerminationJournal(Path(folder)/'journal.json');bridge=self.bridge(journal)
            original=bridge._save_follow_pending
            def late_row(device):
                journal.observe('hand','LATE-HAND',dict(active=None),journal.new_session())
                return original(device)
            with patch.object(bridge,'_save_follow_pending',side_effect=late_row):
                self.refused_without_dispatch(bridge)
            self.assertEqual([row['device_id'] for row in journal.snapshot()],['LATE-HAND'])

    def test_newer_ack_for_same_serial_refuses_expired_preview_before_send(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'journal.json';journal=TerminationJournal(path);bridge=self.bridge(journal)
            newer=journal.new_session()
            journal.observe('hand','CURRENT-HAND',dict(active=False,stop_confirmed=True),newer)
            confirmed=dict(journal.confirmed)
            self.assertEqual(journal.snapshot(),[])
            self.refused_without_dispatch(bridge,'SDK preview session expired')
            self.assertEqual(journal.confirmed,confirmed)
            self.assertEqual(TerminationJournal(path).snapshot(),[])

    def test_newer_ack_for_another_serial_does_not_expire_current_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'journal.json';journal=TerminationJournal(path);bridge=self.bridge(journal)
            newer=journal.new_session()
            journal.observe('hand','OTHER-HAND',dict(active=False,stop_confirmed=True),newer)
            bridge.action(dict(name='glove_follow',workspace_clear=True))
            self.assertEqual([(r['device_id'],r['session']) for r in TerminationJournal(path).snapshot()],
                             [('CURRENT-HAND',bridge.session)])
            self.assertEqual(journal.confirmed['OTHER-HAND'],newer)

    def test_same_epoch_confirmed_stop_allows_next_explicit_follow(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=TerminationJournal(Path(folder)/'journal.json');bridge=self.bridge(journal)
            journal.observe('hand','CURRENT-HAND',dict(active=False,stop_confirmed=True),bridge.session)
            bridge.action(dict(name='glove_follow',workspace_clear=True));old=bridge.lease
            state=copy.deepcopy(bridge.state);state['hardware'].update(active=False,stop_confirmed=True,handled_lease=old)
            bridge.receive(dict(type='glove_state',state=state),session=bridge.session)
            bridge.verify_follow_sdk();bridge.action(dict(name='glove_follow',workspace_clear=True))
            self.assertNotEqual(bridge.lease,old)
            self.assertEqual(journal.snapshot()[0]['session'],bridge.session)


if __name__=='__main__':unittest.main()
