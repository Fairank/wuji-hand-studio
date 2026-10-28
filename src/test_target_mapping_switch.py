"""Target mapping handover reuses the real transaction with synthetic I/O."""
from contextlib import contextmanager
import copy
import unittest
from unittest.mock import Mock,patch

from retarget_settings import defaults
from retarget_workspace import RetargetWorkspace
from retarget_tuning import TuningStore
from test_connection_switch import SwitchTests,SN,HAND,USER


class TargetMappingSwitchTests(unittest.TestCase):
    @contextmanager
    def environment(self,different=True):
        fixture=SwitchTests()
        with fixture.environment() as e:
            e.c.retarget=RetargetWorkspace(e.folder/'mapping')
            e.c.tuning=TuningStore(e.folder/'tuning');e.c.glove.lease=None
            binding=dict(e.state['retarget_binding'],hand_serial='')
            e.c.retarget.select(binding,e.state['device_profile']);e.state['retarget_binding']=binding
            target=dict(binding,hand_serial=HAND);values=defaults()
            if different:values['gain'][0]=.5
            e.c.retarget.library.save(target,values,0)
            e.gs['stream'].update(retarget=defaults(),solver={'engine':'sdk','applied':True})
            original=e.c.action.side_effect
            def action(command):
                name=command['name']
                if name=='retarget_binding_save':
                    e.calls.append(copy.deepcopy(command));result=e.c.retarget.select(command['binding'],e.state['device_profile'])
                    e.state['retarget_binding']=result['binding'];return {'mapping':result}
                if name=='glove_prepare':
                    e.calls.append(copy.deepcopy(command));e.gs['feedback']=dict(connection='connected',device_id=command['serial'],stale=False,
                        identified_device=dict(source='sdk_identity',serial=command['serial'],profile='hand2_left'))
                    e.gs['hardware']={'active':False};return {}
                result=original(command)
                if name=='glove_open':e.gs['stream'].update(retarget=e.c.retarget.context(e.state['device_profile'])['settings'],solver={'engine':'sdk','applied':True})
                return result
            e.c.action=Mock(side_effect=action)
            e.command=lambda **extra:fixture.command(e,'teleoperation',SN,user_id=USER,hand_serial=HAND,**extra)
            yield e

    def test_changed_target_reconnects_once_loads_mapping_and_only_prepares_feedback(self):
        with self.environment() as e:
            old=e.gs['session_id'];result=e.service.start(e.command())
            self.assertEqual(e.gs['session_id'],old+1);e.c.glove.stop_session_confirmed.assert_called_once()
            self.assertEqual([c['name'] for c in e.calls],['retarget_binding_save','glove_scan','glove_open','glove_prepare'])
            self.assertEqual(e.gs['stream']['retarget']['gain'][0],.5)
            self.assertEqual(e.gs['user']['user_id'],USER);self.assertFalse(e.gs['hardware']['active'])
            receipt=result['connection_switch_receipt'];self.assertEqual(receipt['target']['hand_serial'],HAND)
            self.assertEqual(receipt['sdk_session'],old+1)

    def test_equivalent_target_attaches_without_restarting_glove(self):
        with self.environment(False) as e:
            old=e.gs['session_id'];result=e.service.start(e.command())
            self.assertEqual(e.gs['session_id'],old);e.c.glove.stop_session_confirmed.assert_not_called()
            self.assertEqual([c['name'] for c in e.calls],['retarget_binding_save','glove_prepare'])
            self.assertFalse(result['connection_switch']['source_released'])

    def test_unknown_or_wrong_side_hand_retains_original_session_and_binding(self):
        for change in ({'side':'right'},{'stale':True},{'workspace_owners':['other']}):
            with self.subTest(change=change),self.environment() as e:
                e.rows[-1].update(change);before=e.c.retarget.path.read_bytes()
                with self.assertRaisesRegex(ValueError,'same model'):e.service.start(e.command())
                e.c.glove.stop_session_confirmed.assert_not_called();self.assertEqual(e.c.retarget.path.read_bytes(),before)

    def test_mapping_change_during_release_stops_before_scan_or_open(self):
        with self.environment() as e:
            original=e.c.glove.stop_session_confirmed.side_effect
            def stop(*args,**kwargs):
                result=original(*args,**kwargs);binding=dict(e.state['retarget_binding'],hand_serial=HAND)
                values=defaults();values['gain'][1]=.7;e.c.retarget.library.save(binding,values,1);return result
            e.c.glove.stop_session_confirmed.side_effect=stop
            with self.assertRaisesRegex(ValueError,'mapping changed'):e.service.start(e.command())
            self.assertFalse(e.calls);self.assertTrue(e.service.snapshot(e.c.snapshot())['source_released'])

    def test_new_hand_failure_cleans_exact_new_session_and_does_not_retry(self):
        with self.environment() as e:
            original=e.c.action.side_effect
            def action(command):
                if command['name']=='glove_prepare':raise ValueError('Synthetic hand unavailable')
                return original(command)
            e.c.action.side_effect=action
            with self.assertRaisesRegex(ValueError,'hand unavailable'):e.service.start(e.command())
            self.assertEqual(e.c.glove.stop_session_confirmed.call_count,2)
            self.assertEqual([c['name'] for c in e.calls].count('glove_open'),1)
            self.assertFalse(e.gs['busy']);self.assertIsNone(e.service.snapshot(e.c.snapshot())['receipt'])

    def test_explicit_retry_uses_new_request_and_same_selected_mapping(self):
        with self.environment() as e:
            e.service.start(e.command())
            command=e.command();command['request_id']='c'*32
            result=e.service.start(command)
            self.assertTrue(result['connection_switch']['unchanged'])
            self.assertEqual([c['name'] for c in e.calls].count('glove_open'),1)

    def test_current_verified_hand_ignores_old_discovery_but_never_foreign_owner(self):
        with self.environment() as e:
            e.service.start(e.command());e.rows[-1]['stale']=True
            command=e.command();command['request_id']='c'*32
            result=e.service.start(command);self.assertTrue(result['connection_switch']['unchanged'])
            e.rows[-1]['workspace_owners']=[{'id':'other'}]
            command=e.command();command['request_id']='d'*32
            with self.assertRaisesRegex(ValueError,'same model'):e.service.start(command)
            self.assertEqual(e.c.glove.stop_session_confirmed.call_count,1)

    def test_private_pair_adapter_uses_same_switch_after_exact_target_release(self):
        from console_server import Controller
        from retarget_workspace import MappingSwitchRequired
        with self.environment() as e:
            original=e.c.action.side_effect
            def action(command):
                if command['name']=='retarget_binding_save' and e.gs['connection']=='receiving':
                    raise MappingSwitchRequired('different mapping settings')
                return original(command)
            e.c.action.side_effect=action;e.rows.pop()  # Released target no longer advertises an owned session.
            result=Controller._task_context_dispatch(e.c,'pair_prepare',dict(hand_serial=HAND,profile='hand2_left',sdk_user=USER),e.c.snapshot())
            self.assertEqual(result['connection_switch_receipt']['target']['hand_serial'],HAND)
            self.assertFalse(e.gs['hardware']['active']);self.assertEqual(e.gs['stream']['retarget']['gain'][0],.5)

    def test_manual_target_address_is_validated_before_release_and_forwarded(self):
        with self.environment() as e:
            with self.assertRaises(ValueError):e.service.start(e.command(address=['invalid']))
            e.c.glove.stop_session_confirmed.assert_not_called()
            e.service.start(e.command(address='127.0.0.1'))
            self.assertEqual(e.calls[-1]['address'],'127.0.0.1')


if __name__=='__main__':unittest.main()
