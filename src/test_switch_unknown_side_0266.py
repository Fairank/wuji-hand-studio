"""Unknown discovery side is target intent, never a physical-side proof."""
import copy
import unittest
from unittest.mock import Mock

from device_profiles import profile
from test_connection_switch_0264 import PurposeSwitchTests,SN,USER


class UnknownSideTests(unittest.TestCase):
    environment=PurposeSwitchTests.environment
    command=PurposeSwitchTests.command

    def unknown_row(self,e):
        e.state['device_profile']=profile('hand2_right')
        e.rows[0]=dict(sn=SN,kind='glove',side=None,generation='glove1',stale=False,
            raw=dict(sn=SN,type='wuji_glove',transport='Udp',ip='192.0.2.90',address='192.0.2.90:50001'))

    def test_current_mapping_is_used_when_fresh_udp_discovery_has_no_side(self):
        with self.environment('none') as e:
            self.unknown_row(e);state=e.c.snapshot()
            target=e.service._target(self.command(e,'teleoperation',user_id=USER),state,None)
            self.assertEqual(target['profile'],'hand2_right')
            self.assertIsNone(e.rows[0]['side'])
            self.assertEqual(e.rows[0]['generation'],'glove1')
            e.c.action.assert_not_called()

    def test_explicit_mapping_is_respected_without_claiming_unknown_physical_side(self):
        with self.environment('none') as e:
            self.unknown_row(e);state=e.c.snapshot()
            target=e.service._target(self.command(e,'teleoperation',user_id=USER,profile='hand1_left'),state,None)
            self.assertEqual(target['profile'],'hand1_left')
            self.assertEqual(e.state['device_profile']['id'],'hand2_right')
            self.assertIsNone(e.rows[0]['side'])
            e.c.action.assert_not_called()

    def test_known_opposite_side_still_refuses_before_releasing_the_original_viewer(self):
        with self.environment('official') as e:
            self.unknown_row(e);e.rows[0]['side']='left'
            with self.assertRaises(ValueError):
                e.service.start(self.command(e,'teleoperation',user_id=USER,profile='hand2_right'))
            e.c.official_viz.stop_owned.assert_not_called()
            e.c.action.assert_not_called()

    def test_only_none_unknown_can_fall_back_not_invalid_side_values(self):
        for side in ('','unknown','Handedness.Right',False,0,['right']):
            with self.subTest(side=side),self.environment('none') as e:
                self.unknown_row(e);e.rows[0]['side']=copy.deepcopy(side)
                with self.assertRaises(ValueError):
                    e.service._target(self.command(e,'teleoperation',user_id=USER),e.c.snapshot(),None)
                e.c.action.assert_not_called()

    def test_running_viewer_with_unknown_side_keeps_current_mapping_as_intent(self):
        with self.environment('official') as e:
            self.unknown_row(e)
            e.c.official_viz.connection_record=Mock(return_value=dict(serial=SN,viewer_token='a'*32,side=None,source='native_viewer'))
            state=e.c.snapshot();source=dict(mode='official',kind='glove',serial=SN,session='a'*32)
            target=e.service._target(self.command(e,'teleoperation',user_id=USER),state,source)
            self.assertEqual(target['profile'],'hand2_right')
            e.c.official_viz.stop_owned.assert_not_called()
            e.c.action.assert_not_called()


del PurposeSwitchTests
if __name__=='__main__':unittest.main()
