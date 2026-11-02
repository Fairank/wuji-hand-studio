"""Only an explicit atomic sync saves/transfers drafts; no device boundary."""
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch
from console_server import Controller


class ParameterSyncAdmissionTests(unittest.TestCase):
    def test_disconnected_transaction_saves_and_syncs_once_without_connecting(self):
        with tempfile.TemporaryDirectory() as temporary,patch('bridge_config.load_config',return_value={'mode':'wsl','cli':'wuji'}):
            path=Path(temporary)/'motion_parameters.py'
            path.write_bytes(Path(__file__).with_name('motion_parameters.py').read_bytes())
            client=N(parameters_in_launch=True,close=Mock(return_value=True),exec_command=Mock())
            c=Controller(factory=Mock(return_value=client),reports=Path(temporary)/'reports',parameter_path=path)
            c.teleoperation_handover._runtime=Mock(return_value={'mode':'wsl'})
            original=c.parameters.snapshot();values=dict(original['values'],KP=4.)
            result=c.action(dict(name='parameters_sync_reconnect',request_id='a'*32,
                expected_owner=c.snapshot()['connection_switch']['expected_owner'],revision=original['revision'],values=values))
            self.assertEqual(result['parameters']['values'],values)
            self.assertEqual(result['parameters']['sync']['state'],'synced')
            self.assertEqual(result['parameters']['sync']['reconnect_state'],'not_requested')
            self.assertEqual(c.state['connection'],'disconnected');self.assertEqual(c.generation,0)
            c.factory.assert_called_once();client.exec_command.assert_not_called();client.close.assert_called_once()


if __name__=='__main__':unittest.main()
