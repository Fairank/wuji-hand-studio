"""Actual observed owners survive passive inventory reads, without fake freshness."""
import copy
import unittest

from device_inventory import build_inventory, merge_inventories


SN='WG1KA00000000009'
NOW=1000


class SessionIdentityTests(unittest.TestCase):
    def viewer(self,**changes):
        value=dict(active=True,busy=False,phase='running',backend='windows_native',session_id='a'*32,
                   device=dict(sn=SN,device_type='wuji_glove'),
                   reported_device=dict(sn=SN,device_type='wuji_glove',handedness='right'))
        value.update(changes);return value

    def calibration_run(self,**changes):
        value=dict(id='b'*32,serial=SN,running=True,side='left',expected_side='left',
                   source='official_jsonl_native',cleanup_required=False)
        value.update(changes);return value

    def test_native_reported_side_survives_without_refreshing_advertisement(self):
        viz=self.viewer(devices=[dict(sn=SN,device_type='wuji_glove')],scanned_at=NOW-31)
        before=copy.deepcopy(viz)
        result=build_inventory(viz=viz,now=NOW);row=result['devices'][0]
        self.assertEqual((row['kind'],row['generation'],row['side']),('glove','glove1','right'))
        self.assertTrue(row['in_use'] and row['stale'])
        self.assertFalse(row['identity_verified'] or row['selectable'] or row['official_available'])
        self.assertEqual(row['last_seen'],NOW-31);self.assertEqual(viz,before)

    def test_native_observation_requires_exact_running_owner_not_selected_side(self):
        for change in (dict(active=False),dict(busy=True),dict(backend='linux_controller'),
                       dict(phase='starting'),dict(session_id=None),dict(session_id='old'),
                       dict(device=dict(sn='WG_OTHER')),dict(reported_device={})):
            with self.subTest(change=change):
                result=build_inventory(viz=self.viewer(**change),now=NOW)
                self.assertEqual(result['devices'],[])
        row=build_inventory(viz=self.viewer(device=dict(sn=SN,side='left'),
               reported_device=dict(sn=SN,device_type='wuji_glove')),now=NOW)['devices'][0]
        self.assertIsNone(row['side']);self.assertFalse(row['identity_verified'])

    def test_receiving_sdk_supplies_side_but_connecting_profile_does_not(self):
        glove=dict(busy=True,connection='receiving',glove_serial=SN,profile='hand2_right',stream=dict(fresh=False))
        row=build_inventory(glove=glove,now=NOW)['devices'][0]
        self.assertEqual((row['generation'],row['side']),('glove1','right'))
        self.assertTrue(row['identity_verified'] and row['in_use'] and row['stale'])
        for phase in ('connecting','ready','error','closed'):
            with self.subTest(phase=phase):
                self.assertEqual(build_inventory(glove={**glove,'connection':phase},now=NOW)['devices'],[])

    def test_calibration_keeps_running_and_cleanup_owner_without_selected_side_claim(self):
        for run in (self.calibration_run(),self.calibration_run(running=False,cleanup_required=True)):
            with self.subTest(run=run):
                original=copy.deepcopy(run)
                row=build_inventory(controller=dict(calibration=run),now=NOW)['devices'][0]
                self.assertEqual((row['serial'],row['kind'],row['generation']),(SN,'glove','glove1'))
                self.assertIsNone(row['side']);self.assertIsNone(row['last_seen'])
                self.assertTrue(row['in_use'] and row['stale'])
                self.assertFalse(row['identity_verified'] or row['selectable'] or row['official_available'])
                self.assertEqual(run,original)

    def test_invalid_or_completed_calibration_cannot_recreate_owned_card(self):
        for change in (dict(id=None),dict(id='previous-run'),dict(id=True),dict(serial='bad\nserial'),
                       dict(running=False,cleanup_required=False),dict(running=False,cleanup_required='true')):
            with self.subTest(change=change):
                self.assertEqual(build_inventory(controller=dict(calibration=self.calibration_run(**change)),now=NOW)['devices'],[])

    def test_current_calibration_intent_does_not_conflict_with_actual_device_report(self):
        viz=dict(devices=[dict(sn=SN,device_type='wuji_glove',handedness='right')],scanned_at=NOW-31)
        result=build_inventory(viz=viz,controller=dict(calibration=self.calibration_run()),now=NOW)
        self.assertEqual(result['conflicts'],[])
        row=result['devices'][0];self.assertEqual(row['side'],'right')
        self.assertTrue(row['in_use'] and row['stale']);self.assertFalse(row['identity_verified'])

    def test_actual_viewer_and_sdk_conflicts_still_refuse_silent_side_selection(self):
        glove=dict(busy=True,connection='receiving',glove_serial=SN,profile='hand2_left',stream=dict(fresh=True))
        result=build_inventory(viz=self.viewer(),glove=glove,now=NOW)
        self.assertEqual(result['devices'],[])
        self.assertEqual(result['conflicts'][0]['fields'],['side'])

    def test_workspace_merge_preserves_cleanup_ownership_without_renewing_seen(self):
        local=build_inventory(controller=dict(calibration=self.calibration_run(running=False,cleanup_required=True)),now=NOW)
        merged=merge_inventories([local],now=NOW+5)
        row=merged['devices'][0]
        self.assertTrue(row['in_use'] and row['stale']);self.assertIsNone(row['last_seen'])
        self.assertIsNone(row['side']);self.assertFalse(row['identity_verified'] or row['official_available'])


if __name__=='__main__':unittest.main()
