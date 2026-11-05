"""Exercise the actual worker loops with in-memory SDK/queue substitutes."""
import os
import queue
from types import SimpleNamespace as N
import unittest
from unittest.mock import MagicMock,Mock,patch

from console_agent import worker
from first_generation import worker_first

LEASE='a'*32


class Subscription:
    def __init__(self,first=False):self.first=first;self.count=0
    def recv(self):
        self.count+=1
        if self.count%2==0:return None
        if self.first:return N(position=[0.]*20)
        return N(header=N(seq=self.count,timestamp_us=self.count*1000,frame_id='left'),num_joints=1,
                 joints=[N(nid=1,position=0.,velocity=0.,effort=0.)])
    def close(self):pass


def collected(events):
    rows=[]
    while not events.empty():rows.append(events.get_nowait())
    return rows


class StartAcknowledgmentTests(unittest.TestCase):
    def test_hand2_returns_ack_after_start_rejection_and_in_terminal_status(self):
        requests,events=queue.Queue(),queue.Queue()
        requests.put(dict(name='hardware_probe',lease=LEASE,index=6,direction=1,workspace_clear=True))
        requests.put(dict(name='disconnect'))
        hand=Mock();hand.serial_number='SYNTHETIC-HAND2';hand.handedness.return_value.get.return_value='left'
        hand.joint_states.return_value.subscribe.return_value=Subscription()
        hand.joint_diagnostics.return_value.subscribe.return_value=N(recv=lambda:None,close=lambda:None)
        motion=Mock(active=False,trial=None,pending_report=None,reason='synthetic rejection')
        motion.command.side_effect=ValueError('synthetic start rejected')
        motion.status.return_value=dict(active=False,stop_confirmed=True)
        sdk=N(SdkManager=Mock(),Handedness=N(Left='left',Right='right'),WujiHand2=N())
        with patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_left'),patch.dict('sys.modules',wuji_sdk=sdk), \
                patch('device_versions.read_handle_version',return_value=None),patch('hardware_trial.make_trial'):
            worker('',requests,events,motion_factory=lambda *args:motion,prepared=(hand,Mock()))
        rows=collected(events)
        statuses=[r['hardware'] for r in rows if r['type'] in ('state','closed')]
        self.assertGreaterEqual(len(statuses),2)
        self.assertTrue(all(r['handled_lease']==LEASE for r in statuses))
        self.assertTrue(any(r['type']=='notice' and 'rejected' in r['message'] for r in rows))
        hand.enable.assert_not_called()

    def test_hand2_unexpected_start_failure_retains_ack_through_final_cleanup(self):
        requests,events=queue.Queue(),queue.Queue()
        requests.put(dict(name='hardware_probe',lease=LEASE,index=6,direction=1,workspace_clear=True))
        hand=Mock();hand.serial_number='SYNTHETIC-HAND2';hand.handedness.return_value.get.return_value='left'
        hand.joint_states.return_value.subscribe.return_value=Subscription()
        hand.joint_diagnostics.return_value.subscribe.return_value=N(recv=lambda:None,close=lambda:None)
        motion=Mock(active=False,trial=None,pending_report=None,reason='synthetic failure')
        motion.command.side_effect=OSError('synthetic SDK failure')
        motion.status.return_value=dict(active=None,stop_confirmed=False)
        sdk=N(SdkManager=Mock(),Handedness=N(Left='left',Right='right'),WujiHand2=N())
        with patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_left'),patch.dict('sys.modules',wuji_sdk=sdk), \
                patch('device_versions.read_handle_version',return_value=None),patch('hardware_trial.make_trial'):
            worker('',requests,events,motion_factory=lambda *args:motion,prepared=(hand,Mock()))
        final=[r for r in collected(events) if r['type']=='closed'][-1]['hardware']
        self.assertEqual(final['handled_lease'],LEASE)
        self.assertIsNone(final['active']);self.assertIs(final['stop_confirmed'],False)

    def test_hand1_acceptance_and_rejection_acknowledge_only_the_handled_start(self):
        for rejected in (False,True):
            with self.subTest(rejected=rejected):
                requests,events=queue.Queue(),queue.Queue()
                requests.put(dict(name='hardware_probe' if rejected else 'hardware_trial',lease=LEASE,
                                  action='open',amplitude=.25,speed=.5,cycles=1,workspace_clear=True))
                requests.put(dict(name='hardware_stop'));requests.put(dict(name='disconnect'))
                hand=Mock();hand.serial_number='SYNTHETIC-HAND1';hand.handedness_name.return_value='Left'
                hand.realtime_controller.return_value=MagicMock()
                hand.joint_states.return_value.subscribe.return_value=Subscription(first=True)
                sdk=N(SdkManager=Mock(),DeviceType=N(),JointCommand=lambda *args:args,LowPass=lambda **kwargs:N())
                with patch.dict(os.environ,WUJI_HAND_PROFILE='hand1_left'),patch.dict('sys.modules',wuji_sdk=sdk), \
                        patch('device_versions.read_handle_version',return_value=None):
                    worker_first('',requests,events,prepared_hand=hand)
                rows=collected(events);states=[r['hardware'] for r in rows if r['type']=='state']
                self.assertTrue(states);self.assertEqual(states[0]['handled_lease'],LEASE)
                self.assertIs(states[0]['active'],not rejected)
                final=[r for r in rows if r['type']=='closed'][-1]['hardware']
                self.assertEqual(final['handled_lease'],LEASE)
                self.assertIs(final['active'],False);self.assertIs(final['stop_confirmed'],True)
                if rejected:hand.enable.assert_not_called()
                else:hand.enable.assert_called_once()


if __name__=='__main__':unittest.main()
