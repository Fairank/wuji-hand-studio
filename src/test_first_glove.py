import queue
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from first_generation import worker_first
from device_profiles import native_ranges


class HandOneGloveTests(unittest.TestCase):
    def exercise(self, stale=False):
        calls=[];clock=[1.];read=[False];requests_count=[0];source_count=[0]
        desired=[(lo+hi)/2 for lo,hi in native_ranges('hand1_left')]
        def now():clock[0]+=.01;return clock[0]
        def receive():
            read[0]=not read[0]
            return SimpleNamespace(position=[0.]*20) if read[0] else None
        lowpass=SimpleNamespace(__enter__=lambda:calls.append('controller-open'),__exit__=lambda *args:calls.append('controller-close'),set_target_position=lambda q:calls.append(('target',q[:])))
        hand=SimpleNamespace(serial_number='HAND1',handedness_name=lambda:'left',
                             joint_states=lambda:SimpleNamespace(subscribe=lambda:SimpleNamespace(recv=receive,close=lambda:None)),
                             set_all_effort_limit=lambda v:calls.append('effort'),enable=lambda:calls.append('enable'),disable=lambda:calls.append('disable'),
                             disconnect=lambda:calls.append('disconnect-hand'),realtime_controller=lambda _:lowpass)
        manager=SimpleNamespace(disconnect_all=lambda:calls.append('BAD-disconnect-all'))
        sdk=SimpleNamespace(SdkManager=SimpleNamespace(instance=lambda:manager),DeviceType=object(),JointCommand=lambda *args:args,LowPass=lambda **kw:kw)
        def command():
            requests_count[0]+=1
            if requests_count[0]==1:return dict(name='hardware_start',workspace_clear=True,lease='a'*32)
            if requests_count[0]>=4:return dict(name='disconnect')
            raise queue.Empty
        def target(_):
            source_count[0]+=1
            if stale and source_count[0]>1:raise ValueError('expired-glove')
            return desired
        events=queue.Queue()
        with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch.dict('os.environ',{'WUJI_HAND_PROFILE':'hand1_left'}),patch('first_generation.time.monotonic',side_effect=now),patch('first_generation.time.sleep'):
            worker_first('',SimpleNamespace(get_nowait=command),events,prepared_hand=hand,source=SimpleNamespace(target=target))
        return calls,list(events.queue)

    def test_follow_uses_lowpass_seed_then_bounded_targets_and_releases_only_hand(self):
        calls,events=self.exercise()
        targets=[x[1] for x in calls if isinstance(x,tuple)]
        self.assertEqual(targets[0],[0.]*20)
        self.assertGreater(len(targets),1)
        self.assertLess(max(abs(x) for x in targets[1]),max((lo+hi)/2 for lo,hi in native_ranges('hand1_left')))
        self.assertIn('disable',calls);self.assertNotIn('BAD-disconnect-all',calls)
        self.assertTrue(events[-1]['hardware']['stop_confirmed'])

    def test_source_loss_stops_without_publishing_new_target(self):
        calls,events=self.exercise(stale=True)
        self.assertEqual(len([x for x in calls if isinstance(x,tuple)]),1)
        self.assertIn('disable',calls)
        self.assertTrue(any('expired-glove' in str(e) for e in events))


if __name__=='__main__':unittest.main()
