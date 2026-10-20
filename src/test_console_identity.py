from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from console_server import Controller
from device_profiles import profile


class ConsoleIdentityTests(unittest.TestCase):
    def test_selected_model_is_not_a_verified_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            console=Controller(reports=Path(tmp)/'reports')
            self.assertIsNone(console.snapshot()['identified_device'])

    def test_sdk_identity_is_bound_to_serial_and_cleared_after_disconnect(self):
        with tempfile.TemporaryDirectory() as tmp,patch('device_profiles.save_profile',return_value=profile('hand2_right')):
            console=Controller(reports=Path(tmp)/'reports')
            console.event(dict(type='identity',profile='hand2_right',device_id='synthetic-device'),console.generation)
            self.assertEqual(console.snapshot()['identified_device'],dict(serial='synthetic-device',profile='hand2_right',source='sdk_identity'))
            console.clear_live()
            self.assertIsNone(console.snapshot()['identified_device'])


if __name__=='__main__':unittest.main()
