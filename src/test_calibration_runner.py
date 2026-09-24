import io
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import calibration_runner


class CalibrationRunnerTests(unittest.TestCase):
    def test_cancel_line_reaches_official_process_before_locks_release(self):
        calls=[];cancelled=threading.Event()
        class Lease:
            def __init__(self,*args,**kwargs):pass
            def __enter__(self):calls.append('acquire');return self
            def __exit__(self,*args):calls.append('release')
        class Process:
            def poll(self):return None
            def send_signal(self,sig):calls.append('signal');cancelled.set()
            def wait(self):
                self_test.assertTrue(cancelled.wait(1));calls.append('wait');return 9
        self_test=self
        command=['calibrate','Demo','keep','wuji','--jsonl','calib','hand-model','--sn','WG','--handedness','left','--timeout-s','900']
        def current(*args,**kwargs):
            self.assertEqual(calls,['acquire','acquire'])
            return SimpleNamespace(returncode=0,stdout='{"name":"Demo","left_hand":{"calibrated":false}}')
        with patch('sdk_session.ProfileLease',Lease),patch('device_ownership.DeviceOwnership',Lease),patch.object(calibration_runner.subprocess,'run',side_effect=current),patch.object(calibration_runner.subprocess,'Popen',return_value=Process()),patch.object(calibration_runner.sys,'stdin',io.StringIO('cancel\n')),patch.object(calibration_runner.signal,'signal'):
            self.assertEqual(calibration_runner.run(command),9)
        self.assertEqual(calls,['acquire','acquire','signal','wait','release','release'])

    def test_user_changed_between_host_preflight_and_locked_capture(self):
        from contextlib import nullcontext
        current=SimpleNamespace(returncode=0,stdout='{"name":"Another","left_hand":{"calibrated":false}}')
        command=['calibrate','Demo','keep','wuji','--jsonl','calib','hand-model','--sn','WG','--handedness','left','--timeout-s','900']
        with patch('sdk_session.ProfileLease',return_value=nullcontext()),patch('device_ownership.DeviceOwnership',return_value=nullcontext()),patch.object(calibration_runner.subprocess,'run',return_value=current),patch.object(calibration_runner.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'user changed'):calibration_runner.run(command)
            spawn.assert_not_called()

    def test_model_created_during_preflight_requires_fresh_consent(self):
        from contextlib import nullcontext
        current=SimpleNamespace(returncode=0,stdout='{"name":"Demo","left_hand":{"calibrated":true}}')
        command=['calibrate','Demo','keep','wuji','--jsonl','calib','hand-model','--sn','WG','--handedness','left','--timeout-s','900']
        with patch('sdk_session.ProfileLease',return_value=nullcontext()),patch('device_ownership.DeviceOwnership',return_value=nullcontext()),patch.object(calibration_runner.subprocess,'run',return_value=current),patch.object(calibration_runner.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'explicit replacement'):calibration_runner.run(command)
            spawn.assert_not_called()

    def test_explicit_replacement_runs_after_same_user_verification(self):
        from contextlib import nullcontext
        from unittest.mock import Mock
        current=SimpleNamespace(returncode=0,stdout='{"name":"Demo","left_hand":{"calibrated":true}}')
        command=['calibrate','Demo','replace','wuji','--jsonl','calib','hand-model','--sn','WG','--handedness','left','--timeout-s','900']
        process=Mock();process.poll.return_value=0;process.wait.return_value=0
        with patch('sdk_session.ProfileLease',return_value=nullcontext()),patch('device_ownership.DeviceOwnership',return_value=nullcontext()),patch.object(calibration_runner.subprocess,'run',return_value=current),patch.object(calibration_runner.subprocess,'Popen',return_value=process) as spawn,patch.object(calibration_runner.sys,'stdin',io.StringIO('')),patch.object(calibration_runner.signal,'signal'):
            self.assertEqual(calibration_runner.run(command),0)
            self.assertEqual(spawn.call_args.args[0],command[3:])

    def test_unrecognized_command_never_spawns(self):
        with patch.object(calibration_runner.subprocess,'Popen') as spawn:
            for command in [[],['shell','sh'],['calibrate','wuji','--json','devices'],['profile','wuji','--json','firmware','upgrade']]:
                with self.subTest(command=command),self.assertRaises(ValueError):calibration_runner.run(command)
            spawn.assert_not_called()


if __name__=='__main__':unittest.main()
