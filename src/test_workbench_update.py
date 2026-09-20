import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

spec=importlib.util.spec_from_file_location('workbenchctl',Path(__file__).resolve().parents[1]/'scripts/workbenchctl.py')
ctl=importlib.util.module_from_spec(spec);spec.loader.exec_module(ctl)


class UpdateTests(unittest.TestCase):
    def test_reset_while_service_exits_does_not_abort_verified_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';data.mkdir()
            source=root/'HandWorkbench-0.1.4-windows-x64-setup.exe';source.write_bytes(b'MZ installer test')
            client=Mock();client.get.side_effect=[dict(native=True,edition='workbench',data_dir=str(data)),ConnectionResetError('server closing')]
            client.call.return_value=dict(ok=True)
            with patch.object(ctl.sys,'platform','win32'),patch.object(ctl.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as execute:
                result=ctl.upgrade(client,source,ctl.checksum(source),True)
            self.assertTrue(result['ok']);self.assertFalse(result['app_restarted'])
            self.assertEqual([c.args[0] for c in client.call.call_args_list],['prepare_update','close_idle'])
            self.assertEqual(Path(execute.call_args.args[0][0]).parent,data/'updates')

    def test_bad_checksum_does_not_close_or_start_installer(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'HandWorkbench-0.1.4-windows-x64-setup.exe';source.write_bytes(b'MZ test')
            client=Mock();client.get.return_value=dict(native=True,edition='workbench',data_dir=tmp)
            with patch.object(ctl.sys,'platform','win32'),patch.object(ctl.subprocess,'run') as execute:
                with self.assertRaises(ValueError):ctl.upgrade(client,source,'0'*64,True)
            client.call.assert_not_called();execute.assert_not_called()
