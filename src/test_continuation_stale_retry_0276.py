"""Frame freshness must not manufacture a new calibration transfer task."""
import copy
import threading
from types import SimpleNamespace as N
import unittest

from connection_switch import ConnectionSwitch
from device_profiles import profile
import test_calibrated_teleoperation as fixtures


class ContinuationStaleRetryTests(unittest.TestCase):
    environment=fixtures.ContinuationTests.environment
    command=fixtures.ContinuationTests.command
    finish=fixtures.ContinuationTests.finish

    def receiving(self,e,*,fresh=False):
        e.service.execute(self.command(e));self.finish(e)
        proof=e.start.call_args.args[0]
        e.glove.update(connection='receiving',glove_serial=proof['serial'],profile='hand2_right',
            user={'user_id':proof['source_user_id']},model_provenance=proof,
            stream=dict(fresh=fresh,solver=dict(engine='sdk',applied=True)),hardware={},feedback={})
        return proof

    def switch(self,e):
        c=N(lock=threading.RLock(),calibration_handover=N(workflow_lock=threading.RLock()),session_epochs={1:1})
        switch=ConnectionSwitch(c)
        state=dict(connection='disconnected',device_id=None,device_profile=profile('hand2_right'),hardware={'active':False},
            session_generation=1,unconfirmed_devices=[],program={'active':False},group={'active':False},
            calibration={},calibration_connection={},teleoperation_connection={},maintenance={},official_studio={},
            glove=copy.deepcopy(e.glove),official_viz={},calibrated_teleoperation=e.service.snapshot())
        return switch,state

    def test_receiving_stale_keeps_explicit_switch_admission_without_claiming_preview(self):
        with self.environment() as e:
            self.receiving(e,fresh=True)
            self.assertTrue(e.service.snapshot()['connected'])
            e.glove['stream']['fresh']=False
            current=e.service.snapshot()
            self.assertFalse(current['busy'])
            self.assertFalse(current['connected']);self.assertEqual(current['phase'],'connecting')
            self.assertFalse(current['can_continue'])
            switch,state=self.switch(e)
            self.assertIsNone(switch._blocked(state))
            for _ in range(3):e.service.snapshot()
            e.start.assert_called_once();e.stop.assert_not_called();e.export.assert_called_once()

    def test_receiving_without_first_valid_frame_is_not_a_running_transfer_after_worker_exit(self):
        with self.environment() as e:
            self.receiving(e,fresh=False)
            for stream in ({},{'fresh':False},{'fresh':True,'solver':{'engine':'sdk','applied':False}},
                           {'fresh':True,'solver':{'engine':'official_open','applied':True}}):
                e.glove['stream']=stream
                current=e.service.snapshot()
                self.assertFalse(current['busy']);self.assertFalse(current['connected'])
                self.assertFalse(current['can_continue']);self.assertEqual(current['phase'],'connecting')
            e.stop.assert_not_called();e.start.assert_called_once()

    def test_actual_connecting_ready_and_cancel_cleanup_still_block_replacement(self):
        with self.environment() as e:
            self.receiving(e,fresh=False)
            for connection in ('connecting','ready'):
                e.glove['connection']=connection
                self.assertTrue(e.service.snapshot()['busy'])
                switch,state=self.switch(e)
                self.assertIsNotNone(switch._blocked(state))
            e.glove['connection']='receiving';e.service._cancel.set()
            current=e.service.snapshot()
            self.assertTrue(current['busy']);self.assertFalse(current['connected'])
            self.assertEqual(current['phase'],'cancelling')
            e.stop.assert_not_called()

    def test_inflight_worker_and_unknown_paired_hand_remain_blocked(self):
        with self.environment() as e:
            self.receiving(e,fresh=False)
            # An accepted transfer can reach receiving before its own worker
            # has returned; preserve that original work flag independently.
            with e.service.lock:e.service.state['busy']=True
            self.assertTrue(e.service.snapshot()['busy'])
            with e.service.lock:e.service.state['busy']=False
            e.glove.update(feedback={'device_id':'HAND-fixture'},hardware={'active':None})
            switch,state=self.switch(e)
            self.assertIsNotNone(switch._blocked(state))
            e.start.assert_called_once();e.stop.assert_not_called()

    def test_new_fresh_frames_restore_only_preview_without_another_start_or_follow(self):
        with self.environment() as e:
            self.receiving(e,fresh=False)
            self.assertFalse(e.service.snapshot()['connected'])
            e.glove['stream']['fresh']=True
            current=e.service.snapshot()
            self.assertFalse(current['busy']);self.assertTrue(current['connected'])
            self.assertEqual(current['phase'],'preview');self.assertFalse(current['can_continue'])
            self.assertFalse(current['automatically_prepares_hand']);self.assertFalse(current['automatically_follows'])
            e.start.assert_called_once();e.stop.assert_not_called();e.export.assert_called_once()


if __name__=='__main__':unittest.main()
