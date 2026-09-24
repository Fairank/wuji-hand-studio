import io
import threading
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
        command=['calibrate','wuji','--jsonl','calib','hand-model','--sn','WG','--handedness','left','--timeout-s','900']
        with patch('sdk_session.ProfileLease',Lease),patch('device_ownership.DeviceOwnership',Lease),patch.object(calibration_runner.subprocess,'Popen',return_value=Process()),patch.object(calibration_runner.sys,'stdin',io.StringIO('cancel\n')),patch.object(calibration_runner.signal,'signal'):
            self.assertEqual(calibration_runner.run(command),9)
        self.assertEqual(calls,['acquire','acquire','signal','wait','release','release'])

    def test_unrecognized_command_never_spawns(self):
        with patch.object(calibration_runner.subprocess,'Popen') as spawn:
            for command in [[],['shell','sh'],['calibrate','wuji','--json','devices'],['profile','wuji','--json','firmware','upgrade']]:
                with self.subTest(command=command),self.assertRaises(ValueError):calibration_runner.run(command)
            spawn.assert_not_called()


if __name__=='__main__':unittest.main()
