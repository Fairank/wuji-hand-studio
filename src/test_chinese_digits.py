"""Semantic number regressions and native-geometry checks; no device connection."""
import unittest
import numpy as np
import mujoco as mj
from chinese_digits import digit,THUMB_TUCK
from gesture_library import poses,letter,route
from performance_program import DANCES,program,real_points
from device_profiles import load_native_model
from motion_timing import segment_position

def non_adjacent_penetration(model,data):
    def part(g):
        name=model.body(int(model.geom_bodyid[g])).name
        return next((f for f in ('thumb','index','middle','ring','pinky') if f in name),'palm')
    return max((-float(c.dist) for c in data.contact if part(c.geom1)!=part(c.geom2)),default=0.)

class ChineseDigitsTests(unittest.TestCase):
    def test_number_convention_and_independent_letters(self):
        self.assertEqual(digit(4)[:4],list(THUMB_TUCK))
        for n,straight in ((1,{1}),(2,{1,2}),(3,{1,2,3}),(4,{1,2,3,4}),(5,{1,2,3,4}),(6,{4}),(8,{1})):
            for f in range(1,5):
                bending=sum(digit(n)[f*4+j] for j in (0,2,3))
                self.assertLess(bending,.2) if f in straight else self.assertGreater(bending,2.)
        self.assertLess(digit(6)[0],-.4);self.assertLess(digit(8)[0],-.4)
        self.assertGreater(digit(9)[6],1.);self.assertLess(digit(9)[4],.2)
        self.assertEqual(letter('F'),poses()['pair_index'])
        self.assertNotEqual(letter('F'),digit(9));self.assertNotEqual(letter('O'),digit(0))
        for invalid in (-1,10,True,2.0,'7'):
            with self.assertRaises(ValueError):digit(invalid)

    def test_digits_and_open_transitions_in_both_native_hands(self):
        for side in ('left','right'):
            m=load_native_model('hand2_'+side);d=mj.MjData(m)
            d.qpos[:]=poses()['open'];mj.mj_forward(m,d)
            open_span=abs(d.site_xpos[1,0]-d.site_xpos[4,0])
            for n in range(10):
                target=np.array(digit(n));self.assertTrue(np.all(target>=m.actuator_ctrlrange[:,0]));self.assertTrue(np.all(target<=m.actuator_ctrlrange[:,1]))
                for x in np.linspace(0,1,101):
                    d.qpos[:]=(1-x)*np.array(poses()['open'])+x*target;mj.mj_forward(m,d)
                    self.assertLess(non_adjacent_penetration(m,d),.0005,(side,n,x))
                if n==4:self.assertGreater(abs(d.site_xpos[1,0]-d.site_xpos[4,0]),open_span+.035)
                if n==7:
                    for f in (1,2):self.assertLess(np.linalg.norm(d.site_xpos[0]-d.site_xpos[f]),.011)

    def test_every_dance_has_visible_s2_and_no_deep_intersection_at_100hz(self):
        for side in ('left','right'):
            m=load_native_model('hand2_'+side);d=mj.MjData(m)
            for action in DANCES:
                points=program(action)['points'];ranges=np.ptp(np.array([p['q'] for p in points]),axis=0)
                self.assertTrue(np.all(ranges[1::4]>.10),action)
                self.assertGreater(ranges[5],.30);self.assertGreater(ranges[17],.33)
                self.assertTrue(np.all(ranges[6::4]>.70),action)
                for point in points:
                    d.qpos[:]=point['q'];mj.mj_forward(m,d)
                    self.assertLess(non_adjacent_penetration(m,d),.0005,(side,action,point['t']))

    def test_zero_to_seven_phrase_has_clear_intermediate_pose(self):
        p=program('text_sequence','07')
        self.assertGreaterEqual(sum('Digit transition' in x['label'] for x in p['points']),2)
        m=load_native_model('hand2_left');d=mj.MjData(m)
        for a,b in zip(p['points'],p['points'][1:]):
            for t in np.linspace(a['t'],b['t'],101):
                d.qpos[:]=segment_position(a,b,t);mj.mj_forward(m,d)
                self.assertLess(non_adjacent_penetration(m,d),.0005)

if __name__=='__main__':unittest.main()
