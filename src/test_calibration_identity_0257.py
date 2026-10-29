"""Official registry/user objects remain bound across calibration and export."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from calibration_guided import OfficialEvents,validate_result
from calibration_identity import read_current_sdk_user,require_sdk_user,sdk_user_identity

USER=dict(user_id='u_synthetic_0257',display_name='Synthetic')
SN='WG_SYNTHETIC_0257'


def result(user=None):
    return dict(schema_version=2,event='result',operation='calibrate',calibration='hand_model',status='ok',
        device=dict(sn=SN),user=copy.deepcopy(USER),result=dict(handedness='right',
        calibrated_urdf='/synthetic/right_hand.urdf',sdk_user=copy.deepcopy(USER if user is None else user)))


class CalibrationIdentityTests(unittest.TestCase):
    def registry(self,current='u_synthetic_0257',users=None):
        if users is None:users=[dict(user_id='',display_name='Default',is_default=True),dict(USER,is_default=False)]
        rows=['current_user_id = '+json.dumps(current)]
        for user in users:
            rows.append('\n[[users]]')
            for key,value in user.items():rows.append(key+' = '+json.dumps(value))
        return '\n'.join(rows).encode()

    def read(self,raw):
        base=Path(os.environ.get('WUJI_CONTROL_QA_ROOT',str(Path(__file__).resolve().parents[2]/'update-flow-0257/calibration-audit/test-temp')))
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='registry-',dir=base) as folder:
            path=Path(folder)/'registry.toml';path.write_bytes(raw)
            return read_current_sdk_user('Synthetic',registry=path)

    def test_official_registry_captures_exact_current_id_without_modification(self):
        raw=self.registry()
        self.assertEqual(self.read(raw),USER)

    def test_duplicate_name_or_id_default_and_changed_current_are_rejected(self):
        original=[dict(user_id='',display_name='Default',is_default=True),dict(USER,is_default=False)]
        cases=[self.registry(''),self.registry('unknown'),
            self.registry(users=[*original,dict(user_id='u_other',display_name='Synthetic',is_default=False)]),
            self.registry(users=[*original,dict(user_id=USER['user_id'],display_name='Other',is_default=False)]),
            self.registry(users=[dict(user_id='',display_name='Synthetic',is_default=True)])]
        for raw in cases:
            with self.subTest(raw=raw),self.assertRaises(ValueError):self.read(raw)

    def test_unrelated_valid_sdk_display_name_does_not_change_selected_identity(self):
        users=[dict(user_id='',display_name='Default',is_default=True),dict(USER,is_default=False),
               dict(user_id='u_other',display_name='Other Wearer',is_default=False)]
        self.assertEqual(self.read(self.registry(users=users)),USER)

    def test_registry_malformed_oversize_or_wrong_schema_rejected(self):
        for raw in (b'not toml',b'x'*65537,b'users = []',b'current_user_id = 7\nusers = []'):
            with self.subTest(size=len(raw)),self.assertRaises(ValueError):self.read(raw)

    def test_registry_path_redirect_is_never_followed(self):
        with patch.object(Path,'is_symlink',return_value=True),self.assertRaisesRegex(ValueError,'path'):
            read_current_sdk_user('Synthetic',registry=Path.cwd()/'registry.toml')

    def test_real_sdk_object_shape_matches_id_and_profile_not_only_display_name(self):
        event=result(dict(USER,is_default=False,created_at='2026-10-29T00:00:00Z',description=None,external_id=None))
        decoder=OfficialEvents(SN,'right','Synthetic',USER)
        self.assertEqual(decoder.feed(json.dumps(event)+'\n'),[event])
        self.assertEqual(decoder.result,event)
        same_name_other_id=result(dict(USER,user_id='u_other'))
        with self.assertRaisesRegex(ValueError,'wearer identity'):
            OfficialEvents(SN,'right','Synthetic',USER).feed(json.dumps(same_name_other_id)+'\n')

    def test_legacy_string_requires_matching_exact_terminal_user_object(self):
        event=result('Synthetic')
        self.assertEqual(validate_result(event,SN,'right','Synthetic',USER),USER)
        event.pop('user')
        with self.assertRaisesRegex(ValueError,'exact wearer identity'):validate_result(event,SN,'right','Synthetic',USER)
        event['user']=dict(USER,user_id='u_other')
        with self.assertRaisesRegex(ValueError,'wearer identity'):validate_result(event,SN,'right','Synthetic',USER)

    def test_missing_preflight_identity_cannot_be_inferred_from_result(self):
        with self.assertRaises(ValueError):OfficialEvents(SN,'right','Synthetic').feed(json.dumps(result())+'\n')

    def test_wrong_progress_owner_or_device_rejected_before_later_result(self):
        for change in (dict(user=dict(USER,user_id='u_other')),dict(device=dict(sn='WG_OTHER'))):
            event=dict(schema_version=2,event='progress',operation='calibrate',calibration='hand_model',user=copy.deepcopy(USER),device=dict(sn=SN))
            event.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                OfficialEvents(SN,'right','Synthetic',USER).feed(json.dumps(event)+'\n')

    def test_identity_source_and_decoder_snapshot_do_not_follow_mutated_input(self):
        captured=copy.deepcopy(USER);decoder=OfficialEvents(SN,'right','Synthetic',captured)
        captured['user_id']='u_other'
        self.assertEqual(decoder.feed(json.dumps(result())+'\n'),[result()])
        with self.assertRaises(ValueError):require_sdk_user(dict(USER,user_id='u_other'),USER,'Synthetic')

    def test_user_model_side_and_device_have_distinct_error_reasons(self):
        cases=[(lambda e:e['result'].update(sdk_user=dict(USER,user_id='u_other')),'wearer identity'),
               (lambda e:e['result'].update(handedness='left'),'hand side'),
               (lambda e:e['result'].update(calibrated_urdf=''),'model location'),
               (lambda e:e['device'].update(sn='WG_OTHER'),'another glove')]
        for mutate,reason in cases:
            event=result();mutate(event)
            with self.subTest(reason=reason),self.assertRaisesRegex(ValueError,reason):validate_result(event,SN,'right','Synthetic',USER)


if __name__=='__main__':unittest.main()
