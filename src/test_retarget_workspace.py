import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from retarget_workspace import RetargetWorkspace
from retarget_settings import defaults


class WorkspaceMappingTests(unittest.TestCase):
    def test_replacing_glove_or_hand_does_not_inherit_previous_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace=RetargetWorkspace(folder);profile=dict(generation='hand2',side='left')
            binding=dict(**profile,glove_serial='WG1',sdk_user='user1',hand_serial='WH1')
            workspace.select(binding,profile)
            values=defaults();values['gain'][0]=.5
            workspace.library.save(binding,values)
            workspace.select(dict(binding,glove_serial='WG2'),profile)
            self.assertEqual(workspace.context(profile)['settings']['gain'][0],1)
            workspace.select(dict(binding,hand_serial='WH2'),profile)
            self.assertEqual(workspace.context(profile)['settings']['gain'][0],1)
            workspace.select(binding,profile)
            self.assertEqual(workspace.context(profile)['settings']['gain'][0],.5)

    def test_profile_switch_does_not_mirror_or_copy_calibration(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace=RetargetWorkspace(folder);left=dict(generation='hand2',side='left')
            workspace.select(dict(**left,glove_serial='WG1',sdk_user='u',hand_serial='H'),left)
            result=workspace.context(dict(generation='hand1',side='right'))
            self.assertEqual(result['binding']['glove_serial'],'');self.assertFalse(result['saved'])
            with self.assertRaises(ValueError):workspace.select(result['binding'],left)

    def test_legacy_values_only_appear_in_unbound_context(self):
        with tempfile.TemporaryDirectory() as folder:
            values=defaults();values['offset_deg'][0]=10
            (Path(folder)/'retargeting.json').write_text(json.dumps(values))
            w=RetargetWorkspace(folder);p=dict(generation='hand2',side='left')
            self.assertEqual(w.context(p)['settings']['offset_deg'][0],10)
            w.select(dict(**p,glove_serial='WG',sdk_user='u',hand_serial=''),p)
            self.assertEqual(w.context(p)['settings']['offset_deg'][0],0)

    def test_apply_to_preview_does_not_enable_and_rejects_prepared_hand(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as folder:
            c=Controller(factory=lambda:None,reports=Path(folder)/'reports')
            with patch.object(c.glove,'snapshot',return_value={'connection':'receiving','feedback':{}}),patch.object(c.glove,'action') as action:
                c.action({'name':'retarget_apply'})
                self.assertEqual(action.call_args.args[0]['name'],'glove_retarget')
            with patch.object(c.glove,'snapshot',return_value={'connection':'receiving','feedback':{'device_id':'WH'}}),patch.object(c.glove,'action') as action:
                with self.assertRaises(ValueError):c.action({'name':'retarget_apply'})
                action.assert_not_called()


if __name__=='__main__':unittest.main()
