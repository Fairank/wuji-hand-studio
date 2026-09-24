import hashlib
import json
from pathlib import Path
import unittest
from device_profiles import PROFILES, load_native_model
from joint_reference import catalog, image_path

class JointReferenceTest(unittest.TestCase):
    def test_all_model_indices_names_ranges_and_images(self):
        root=Path(__file__).resolve().parent
        manifest=json.loads((root/'web/joints/manifest.json').read_text())
        for pid,p in PROFILES.items():
            model=load_native_model(pid)
            ref=catalog(pid)
            self.assertFalse(ref['hardware_mapping_verified'])
            self.assertEqual(len(ref['items']),20)
            self.assertEqual(manifest[pid]['model_sha256'],hashlib.sha256((root/p['model']).read_bytes()).hexdigest())
            for index,item in enumerate(ref['items']):
                jid=int(model.actuator_trnid[index,0])
                self.assertEqual(item['modelName'],model.joint(jid).name)
                self.assertEqual(item['actuatorName'],model.actuator(index).name)
                self.assertEqual(item['range_rad'],model.jnt_range[jid].tolist())
                self.assertNotIn('nid',item)
                self.assertTrue(image_path(pid,index).read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))

    def test_anatomy_distinguishes_spread_and_flexion(self):
        rows=catalog('hand2_left')['items']
        self.assertEqual(rows[4]['jointLabel'],'指根屈伸')
        self.assertEqual(rows[5]['jointLabel'],'指根侧摆')
        self.assertEqual(rows[2]['jointLabel'],'拇指近节')
        self.assertEqual(rows[6]['jointLabel'],'中节屈伸')
        self.assertEqual(catalog('hand2_right','en')['items'][19]['jointLabel'],'Tip joint')

    def test_no_invented_hand1_semantic_or_node_mapping(self):
        rows=catalog('hand1_right')['items']
        self.assertEqual(rows[0]['jointLabel'],'根部轴 1')
        self.assertIn('核对设备',rows[0]['movement'])

    def test_bounded_paths(self):
        for pid in ['../hand2_left','hand3_left','hand2_left/../../']:
            with self.assertRaises(ValueError):image_path(pid,0)
        for idx in [-1,20,True,1.2,'5']:
            with self.assertRaises(ValueError):image_path('hand2_left',idx)

if __name__=='__main__':unittest.main()
