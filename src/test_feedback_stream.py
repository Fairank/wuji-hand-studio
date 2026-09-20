from collections import deque
from types import SimpleNamespace
import unittest
from feedback_stream import drain_available
from motion_history import summarize


class ReceiveTests(unittest.TestCase):
    def test_burst_is_fully_drained_and_last_frame_is_latest(self):
        q=deque(range(30));sub=SimpleNamespace(recv=lambda:q.popleft() if q else None)
        frames,empty=drain_available(sub,clock=lambda:1.)
        self.assertTrue(empty);self.assertEqual([x for x,_ in frames],list(range(30)))
        self.assertEqual(frames[-1][0],29)

    def test_endless_queue_cannot_block_stop_or_be_claimed_fresh(self):
        frames,empty=drain_available(SimpleNamespace(recv=lambda:1),clock=lambda:1.,limit=8)
        self.assertFalse(empty);self.assertEqual(len(frames),8)

    def test_time_budget_bounds_receive(self):
        tick=iter([0.,.005,.010])
        frames,empty=drain_available(SimpleNamespace(recv=lambda:1),clock=lambda:next(tick))
        self.assertFalse(empty);self.assertEqual(len(frames),2)

    def test_legacy_aborted_trial_does_not_claim_return_accuracy(self):
        report=dict(kind='low_current_showcase_trial',completed=False,return_error_deg=14.)
        r=summarize(report)
        self.assertFalse(r['return_evaluated']);self.assertIsNone(r['return_error_deg'])
        self.assertEqual(r['displacement_at_stop_deg'],14.)
        self.assertEqual(report['return_error_deg'],14.)

    def test_completed_return_and_small_failed_displacement_are_distinct(self):
        for completed in (True,False):
            r=summarize(dict(kind='low_current_showcase_trial',completed=completed,return_error_deg=.1))
            self.assertEqual(r['return_evaluated'],completed)
            self.assertEqual(r['return_error_deg'],.1 if completed else None)


if __name__=='__main__':unittest.main()
