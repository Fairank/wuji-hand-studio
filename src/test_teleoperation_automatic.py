"""Automatic view selection is display state, never a device command."""
from contextlib import nullcontext
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import numpy as np  # Cache the real extension before synthetic renderer patches.


class AutomaticControllerTests(unittest.TestCase):
    def test_default_and_display_changes_never_connect_or_forward_to_devices(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=folder):
            c=Controller(factory=Mock(side_effect=AssertionError('No controller starts')),reports=Path(folder)/'reports')
            self.assertEqual(c.state['teleoperation_view_mode'],'automatic')
            c.send=Mock(side_effect=AssertionError('No hand command'))
            c.glove.action=Mock(side_effect=AssertionError('No glove command'))
            c.player.command=Mock(side_effect=AssertionError('No playback command'))
            with patch('cli_coordination.gate',side_effect=lambda *a,**k:nullcontext()), \
                    patch('cli_coordination.enforce'),patch('studio_component.enforce_session_owner'):
                for mode in ('target','feedback','constrained','automatic'):
                    result=c.action(dict(name='teleoperation_view_mode',mode=mode))
                    self.assertEqual(result,dict(teleoperation_view_mode=mode))
                    self.assertEqual(c.state['view_source'],'glove')
            c.factory.assert_not_called();c.send.assert_not_called();c.glove.action.assert_not_called();c.player.command.assert_not_called()


if __name__=='__main__':unittest.main()
