"""Explicit stale retry reopens the exact SDK owner; all external I/O is fake."""
import copy
import unittest
from unittest.mock import Mock

from test_connection_switch_0264 import PurposeSwitchTests, SN, USER
from connection_switch import owner


class StaleRetryTests(unittest.TestCase):
    environment=PurposeSwitchTests.environment
    command=PurposeSwitchTests.command

    def saved(self,e):
        proof=dict(source_kind='saved_profile',source_run_id=None,transfer_id='f'*32,
            serial=SN,side='left',source_profile='Original wearer',source_user_id=USER)
        e.service._saved_resume=dict(serial=SN,profile='hand2_left',user_id=USER,
            source_profile='Original wearer',config=e.service._config())
        order=[];stop=e.c.glove.stop_session_confirmed.side_effect
        def release(*args,**kw):order.append('stop');return stop(*args,**kw)
        e.c.glove.stop_session_confirmed.side_effect=release
        def preflight(serial,side,profile=None,cancel_check=None):
            self.assertEqual((serial,side,profile),(SN,'left','Original wearer'))
            self.assertTrue(e.gs['busy']);cancel_check();order.append('preflight')
            return copy.deepcopy(proof)
        def prepare(actual,*,cancel_check=None):
            self.assertEqual(actual,proof);self.assertFalse(e.gs['busy'])
            cancel_check();order.append('prepare');return dict(proof)
        def start(bundle,actual,*,expected_profile,timeout_ms=250):
            self.assertEqual((bundle,actual,expected_profile),(proof,proof,'hand2_left'))
            order.append('open');e.gs['session_id']+=1;e.sdk(SN,USER)
            e.gs['stream'].update(solver=dict(engine='sdk',applied=True),timeout_ms=timeout_ms)
            return e.gs['session_id']
        e.c._saved_profile_preflight=Mock(side_effect=preflight)
        e.c._prepare_saved_profile=Mock(side_effect=prepare)
        e.c._start_saved_profile_session=Mock(side_effect=start)
        return order

    def test_explicit_stale_saved_retry_reopens_once_after_exact_preflight_and_stop(self):
        with self.environment() as e:
            e.gs['stream'].update(fresh=False,timeout_ms=650);old=e.gs['session_id'];order=self.saved(e)
            result=e.service.start(self.command(e,'teleoperation'))
            self.assertFalse(result['connection_switch']['unchanged'])
            self.assertEqual(order,['preflight','stop','prepare','open'])
            self.assertEqual(e.gs['session_id'],old+1);self.assertTrue(e.gs['stream']['fresh'])
            self.assertEqual(e.gs['stream']['timeout_ms'],650);self.assertEqual(e.calls,[])
            self.assertEqual(e.gs['user']['user_id'],USER)

    def test_fresh_current_saved_session_still_reuses_without_profile_reads_or_commands(self):
        with self.environment() as e:
            order=self.saved(e);before=copy.deepcopy(e.gs)
            result=e.service.start(self.command(e,'teleoperation'))
            self.assertTrue(result['connection_switch']['unchanged']);self.assertEqual(e.gs,before)
            self.assertEqual(order,[]);self.assertEqual(e.calls,[])

    def test_stale_legacy_sdk_retry_uses_original_exact_scan_and_open_without_guessed_profile(self):
        with self.environment() as e:
            e.gs['stream']['fresh']=False;old=e.gs['session_id']
            result=e.service.start(self.command(e,'teleoperation'))
            self.assertFalse(result['connection_switch']['unchanged'])
            self.assertEqual([c['name'] for c in e.calls],['glove_scan','glove_open'])
            self.assertEqual(e.gs['session_id'],old+1);self.assertEqual(e.gs['user']['user_id'],USER)

    def test_changed_saved_owner_refuses_before_closing_the_current_stale_session(self):
        with self.environment() as e:
            e.gs['stream']['fresh']=False;self.saved(e);before=copy.deepcopy(e.gs)
            proof=e.c._saved_profile_preflight.side_effect
            e.c._saved_profile_preflight.side_effect=lambda *a,**kw:{**proof(*a,**kw),'source_user_id':'different-owner'}
            with self.assertRaisesRegex(ValueError,'Saved calibration device or wearer differs'):
                e.service.start(self.command(e,'teleoperation'))
            e.c.glove.stop_session_confirmed.assert_not_called()
            e.c._start_saved_profile_session.assert_not_called();self.assertEqual(e.gs,before)

    def test_unknown_paired_hand_disable_still_blocks_retry_without_any_stop_or_open(self):
        with self.environment() as e:
            e.gs['stream']['fresh']=False;e.gs['feedback']={'device_id':'SYNTHETIC-HAND'}
            e.gs['hardware']={'active':None,'stop_confirmed':False};order=self.saved(e)
            with self.assertRaises(ValueError):e.service.start(self.command(e,'teleoperation'))
            self.assertEqual(order,[]);self.assertEqual(e.calls,[])
            e.c.glove.stop_session_confirmed.assert_not_called()

    def test_retry_cannot_reopen_when_original_sdk_release_is_unconfirmed(self):
        with self.environment() as e:
            e.gs['stream']['fresh']=False;self.saved(e)
            e.c.glove.stop_session_confirmed.side_effect=ValueError('Original SDK cleanup unconfirmed')
            with self.assertRaisesRegex(ValueError,'Original SDK cleanup unconfirmed'):
                e.service.start(self.command(e,'teleoperation'))
            e.c._prepare_saved_profile.assert_not_called();e.c._start_saved_profile_session.assert_not_called()
            self.assertTrue(e.gs['busy']);self.assertEqual(e.calls,[])

    def test_stale_owner_cannot_lend_a_saved_hint_to_an_explicit_different_wearer_or_model(self):
        for target_profile,target_user in (('hand1_left',USER),('hand2_left','another-sdk-wearer')):
            with self.subTest(profile=target_profile,user=target_user),self.environment() as e:
                e.gs['stream']['fresh']=False
                e.service._saved_resume=dict(serial=SN,profile=target_profile,user_id=target_user,
                    source_profile='Old unrelated native profile',config=e.service._config())
                e.service.catalog['users'].append(dict(user_id=target_user))
                e.c._saved_profile_preflight=Mock(side_effect=AssertionError('The old hint is not this SDK owner'))
                command=self.command(e,'teleoperation',profile=target_profile,user_id=target_user)
                state=e.c.snapshot();target=e.service._target(command,state,owner(state))
                self.assertNotIn('_saved_profile',target)
                self.assertEqual((target['profile'],target['user_id']),(target_profile,target_user))
                e.c._saved_profile_preflight.assert_not_called()
                e.c.glove.stop_session_confirmed.assert_not_called()


del PurposeSwitchTests
