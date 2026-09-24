"""Group-control acceptance with fake SDKs and clocks; never connects hardware."""
import copy
import io
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from group_timing import common_schedule,retime_sections
from group_coordinator import GroupCoordinator
from hardware_showcase import HardwareShowcase
from test_hardware_showcase import FakeHand,feedback,diagnostics
from hardware_trial import LABELS,make_trial
from group_hardware import metadata


class GroupTimingTests(unittest.TestCase):
    def test_shared_sections_do_not_accelerate_either_hand(self):
        from performance_program import segment_peak
        plans=[make_trial([q]*20,'pair_wave_'+side,.5,1) for q,side in ((.1,'left'),(.25,'right'))]
        rows=[dict(metadata(p),clock_id='same-kernel') for p in plans];shared=common_schedule(rows)
        for p,old in zip(plans,rows):
            pts=retime_sections(p['points'],old,shared)
            self.assertAlmostEqual(pts[-1]['t'],sum(shared.values()))
            self.assertEqual(pts[0]['q'],p['points'][0]['q'])
            for a,b,x,y in zip(pts,pts[1:],p['points'],p['points'][1:]):
                self.assertGreater(b['t'],a['t']);self.assertLessEqual(segment_peak(a,b),segment_peak(x,y)+1e-8)
            self.assertAlmostEqual(pts[1]['t'],shared['entry_s'])
            self.assertAlmostEqual(pts[-3]['t'],shared['entry_s']+shared['body_s'])

    def test_different_or_unknown_kernel_clock_is_not_synchronous(self):
        row=dict(entry_s=1,body_s=16,exit_s=1,hold_s=.2,clock_id='A')
        for key in ('B',None,''):
            with self.assertRaises(ValueError):common_schedule([row,dict(row,clock_id=key)])

    def test_paired_motion_uses_native_limits_on_both_sides(self):
        from bimanual_program import INTERNAL_IDS,program
        from device_profiles import native_ranges
        from motion_timing import segment_position
        for action in INTERNAL_IDS:
            ranges=native_ranges('hand2_'+action.rsplit('_',1)[1]);points=program(action)['points']
            self.assertEqual(points[0]['q'],points[-1]['q'])
            for a,b in zip(points,points[1:]):
                for t in (a['t'],(a['t']+b['t'])/2):
                    self.assertTrue(all(lo<=v<=hi for v,(lo,hi) in zip(segment_position(a,b,t),ranges)),action)


class GroupHardwareTests(unittest.TestCase):
    def driver(self,tmp):
        hand=FakeHand();hand.joints=lambda:[SimpleNamespace(index=i,label=s) for i,s in enumerate(LABELS)]
        d=HardwareShowcase(hand,'synthetic-left',lambda **k:SimpleNamespace(**k),Path(tmp)/'absent')
        d.check_probe_ready=lambda *a:True
        return hand,d

    @patch('group_hardware.clock_id',return_value='same-kernel')
    def test_arms_at_measured_pose_and_waits_for_common_start(self,_):
        with tempfile.TemporaryDirectory() as tmp:
            h,d=self.driver(tmp)
            d.start_trial(dict(action='pair_wave_left',amplitude=.25,cycles=1,speed=1,lease='a'*32,workspace_clear=True,group_sync={'token':'b'*32}),feedback(1.,.1),diagnostics(1.),1.)
            for now in (1.01,1.10,1.2):
                d.last_beat=now;d.tick(feedback(now,.1),diagnostics(now),now)
            self.assertEqual(d.phase,'group_wait');self.assertEqual(d.elapsed,0.)
            self.assertTrue(all(row[1]==[.1]*20 for row in h.calls if row[0]=='send'))
            d.command(dict(name='hardware_group_commit',lease='a'*32,token='b'*32,start_s=1.5,timing=metadata(d.run_profile)),feedback(1.2,.1),diagnostics(1.2),1.2)
            d.last_beat=1.6;d.tick(feedback(1.49,.1),diagnostics(1.49),1.49);self.assertEqual(d.elapsed,0.)
            d.tick(feedback(1.501,.1),diagnostics(1.501),1.501);self.assertAlmostEqual(d.elapsed,.001)
            d.tick(feedback(1.523,.1),diagnostics(1.523),1.523);self.assertAlmostEqual(d.elapsed,.023)
            d.stop('test');self.assertFalse(d.owned)

    @patch('group_hardware.clock_id',return_value='same-kernel')
    def test_missed_start_does_not_jump_ahead_into_choreography(self,_):
        with tempfile.TemporaryDirectory() as tmp:
            h,d=self.driver(tmp)
            d.start_trial(dict(action='pair_wave_left',amplitude=.25,cycles=1,speed=1,lease='a'*32,workspace_clear=True,group_sync={'token':'b'*32}),feedback(1.,.1),diagnostics(1.),1.)
            d.tick(feedback(1.01,.1),diagnostics(1.01),1.01)
            d.command(dict(name='hardware_group_commit',lease='a'*32,token='b'*32,start_s=1.5,timing=metadata(d.run_profile)),feedback(1.1,.1),diagnostics(1.1),1.1)
            count=len([r for r in h.calls if r[0]=='send']);d.last_beat=1.55
            d.tick(feedback(1.55,.1),diagnostics(1.55),1.55)
            self.assertFalse(d.active);self.assertIn('deadline',d.reason)
            self.assertEqual(count,len([r for r in h.calls if r[0]=='send']))

    @patch('group_hardware.clock_id',return_value='same-kernel')
    def test_two_different_preparation_delays_share_one_execution_clock(self,_):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            drivers=[]
            for side,initial,ready in (('left',.1,1.),('right',.2,1.25)):
                h,d=self.driver(tmp)
                with patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_'+side):
                    d.start_trial(dict(action='pair_wave_'+side,amplitude=.5,cycles=1,speed=1,lease='a'*32,workspace_clear=True,group_sync={'token':'b'*32}),feedback(ready,initial),diagnostics(ready),ready)
                d.tick(feedback(ready+.01,initial),diagnostics(ready+.01),ready+.01)
                drivers.append((initial,h,d))
            shared=common_schedule([dict(metadata(d.run_profile),clock_id='same-kernel') for _,_,d in drivers])
            for initial,h,d in drivers:
                d.command(dict(name='hardware_group_commit',lease='a'*32,token='b'*32,start_s=2.,timing=shared),feedback(1.4,initial),diagnostics(1.4),1.4)
            for k in range(201):
                now=2.+k*.01
                for initial,h,d in drivers:
                    row=feedback(now)
                    for j,v in zip(row['joints'],d.target):j['position_rad']=v
                    d.last_beat=now;d.tick(row,diagnostics(now),now)
                    self.assertTrue(d.active,d.reason)
                self.assertAlmostEqual(drivers[0][2].elapsed,drivers[1][2].elapsed,places=10)
                self.assertAlmostEqual(drivers[0][2].elapsed,now-2,places=10)
            a,b=drivers[0][2].point_times,drivers[1][2].point_times
            self.assertEqual(len(a),len(b));self.assertLess(max(abs(x-y) for x,y in zip(a,b)),1e-10)
            for _,h,d in drivers:d.stop('test')


class GroupCoordinatorTests(unittest.TestCase):
    def test_prepare_failure_aborts_every_possible_reservation(self):
        calls=[]
        def read(i):return dict(device_profile=dict(side='left' if i=='L' else 'right'))
        def post(i,c):
            calls.append((i,c['name']))
            if c['name']=='group_prepare' and i=='R':raise ValueError('injected refusal')
            return {}
        g=GroupCoordinator(read,post)
        try:
            g.start(dict(members=['L','R'],mode='preview',kind='pair',action='pair_wave',speed=1,cycles=1,amplitude=1))
            g.thread.join(2);self.assertFalse(g.thread.is_alive())
            self.assertEqual(g.snapshot()['phase'],'failed')
            self.assertEqual({i for i,c in calls if c=='group_abort'},{'L','R'})
            self.assertNotIn('group_commit',[c for i,c in calls])
        finally:g.pool.shutdown()

    def test_two_left_hands_cannot_be_implicitly_mirrored(self):
        g=GroupCoordinator(lambda i:dict(device_profile=dict(side='left')),lambda *a:self.fail('No prepare expected'))
        try:
            with self.assertRaises(ValueError):g.start(dict(members=['L','L2'],mode='preview',kind='pair',action='pair_wave',speed=1,cycles=1,amplitude=1))
        finally:g.pool.shutdown()

    def test_duplicate_device_and_cross_host_sync_rejected_before_arming(self):
        states={i:dict(device_id=i,connection='connected',stale=False,device_profile=dict(side=side,generation='hand2'),hardware=dict(group_sync_version=1,controller_clock_id=i)) for i,side in (('L','left'),('R','right'))}
        g=GroupCoordinator(lambda i:copy.deepcopy(states[i]),lambda *a:self.fail('No prepare expected'))
        try:
            command=dict(members=['L','R'],mode='hardware',kind='pair',action='pair_wave',speed=1,cycles=1,amplitude=1,workspace_clear=True)
            with self.assertRaisesRegex(ValueError,'Linux'):g.start(command)
            states['R']['hardware']['controller_clock_id']='L';states['R']['device_id']='L'
            with self.assertRaisesRegex(ValueError,'different'):g.start(command)
        finally:g.pool.shutdown()


class GroupParticipantTests(unittest.TestCase):
    def test_reservation_blocks_unrelated_motion_and_lease_expiry_cancels_preview(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports')
            c.action(dict(name='group_prepare',token='a'*32,mode='preview',kind='pair',action='pair_wave',speed=1,cycles=1,amplitude=.5))
            with self.assertRaises(ValueError):c.action(dict(name='program_start'))
            with self.assertRaises(ValueError):c.action(dict(name='device_profile_select',profile='hand2_right'))
            c.group.heartbeat=time.monotonic()-2;c.group.watch.join(1)
            self.assertFalse(c.group.active);self.assertFalse(c.player.active)

    def test_hardware_prepare_is_read_only(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(reports=Path(tmp)/'reports');c.stdin=io.StringIO()
            c.state.update(connection='connected',device_id='test',metrics=dict(age_ms=0.))
            c.last_update=time.monotonic();c.state['hardware'].update(group_sync_version=1,trial_ready=True)
            try:
                c.action(dict(name='group_prepare',token='a'*32,mode='hardware',kind='pair',action='pair_wave',speed=1,cycles=1,amplitude=.5,workspace_clear=True))
                self.assertEqual(c.stdin.getvalue(),'')
            finally:c.group.active=False


if __name__=='__main__':unittest.main()
