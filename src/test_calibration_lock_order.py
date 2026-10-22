"""A state reader must not deadlock native handover's metadata callback."""
from contextlib import nullcontext
import os
import threading
import time
import unittest
from unittest.mock import Mock,patch

from calibration_cli import CalibrationCLI


class CalibrationLockOrder(unittest.TestCase):
    def test_metadata_callback_does_not_hold_run_lock_needed_by_state_reader(self):
        service=CalibrationCLI();observed=[]
        def metadata():
            def state_reader():
                acquired=service.lock.acquire(timeout=.3)
                observed.append(acquired)
                if acquired:service.lock.release()
            reader=threading.Thread(target=state_reader);reader.start();reader.join(1)
            self.assertFalse(reader.is_alive())
        service.metadata_check=metadata
        service.preflight_guided=Mock()
        profile=dict(name='Demo',left_hand=dict(calibrated=False))
        connected=dict(source='native_viewer',serial='WG_SYNTH',side='left',viewer_token='a'*32,
                       owner_pid=os.getpid(),owner_start='live',checked_at=time.time())
        with patch('calibration_cli.load_config',return_value=dict(mode='wsl',cli='wuji')),\
                patch('official_device_discovery.use_native',return_value=True),\
                patch('cli_coordination.identity',return_value='live'),\
                patch('cli_coordination.gate',side_effect=lambda *a,**k:nullcontext()),\
                patch('calibration_cli.cli_json',return_value=profile),\
                patch('native_calibration.NativeCalibration') as native:
            service.start('left','WG_SYNTH',profile='Demo',connected=connected)
            native.return_value.start_guided.assert_called_once()
        self.assertEqual(observed,[True])
