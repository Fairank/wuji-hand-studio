import ast
from pathlib import Path
from types import SimpleNamespace as Obj
import unittest
from capture import serialize_frame,require_left

class CaptureTests(unittest.TestCase):
    def frame(self):
        return Obj(header=Obj(seq=7,timestamp_us=1000,frame_id='l_wrist'),num_joints=2,
            joints=[Obj(nid=52,position=.2,velocity=.1,effort=.03),Obj(nid=17,position=.4,velocity=0,effort=-.04)])
    def test_preserves_nids_order_and_units(self):
        value=serialize_frame(self.frame(),1.)
        self.assertEqual([j['nid'] for j in value['joints']],[52,17])
        self.assertEqual(value['joints'][1]['effort_A'],-.04)
        self.assertNotIn('force_N',value['joints'][1])
    def test_duplicate_nid_rejected(self):
        f=self.frame();f.joints[1].nid=52
        with self.assertRaises(ValueError):serialize_frame(f,1.)
    def test_nan_rejected(self):
        f=self.frame();f.joints[0].effort=float('nan')
        with self.assertRaises(ValueError):serialize_frame(f,1.)
    def test_count_mismatch_rejected(self):
        f=self.frame();f.num_joints=20
        with self.assertRaises(ValueError):serialize_frame(f,1.)
    def test_right_hand_refused(self):
        with self.assertRaises(RuntimeError):require_left(Obj(handedness=lambda:Obj(get=lambda:'right')))
        self.assertEqual(require_left(Obj(handedness=lambda:Obj(get=lambda:'left'))),'left')
    def test_no_command_or_configuration_api(self):
        tree=ast.parse(Path(__file__).with_name('capture.py').read_text(encoding='utf-8'))
        forbidden={'joint_command','publish','send','set','set_rate','enable','disable','reboot','clear_fault','set_origin','clear_origin'}
        calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertFalse(calls&forbidden)

if __name__=='__main__':unittest.main()
