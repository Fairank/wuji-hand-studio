import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import desktop

class PortTests(unittest.TestCase):
    def test_explicit_port_unchanged(self):
        with patch.dict(os.environ,{'WUJI_STUDIO_PORT':'8888'}),patch('desktop.ready') as ready:
            self.assertEqual(desktop.choose_port(),desktop.PORT);ready.assert_not_called()

    def test_occupied_old_workbench_is_not_stopped(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{},clear=True),patch('desktop.DATA',Path(d)),patch('desktop.ready',return_value=False),patch('desktop.PORT',8781),patch('desktop.URL',''),patch('runtime_paths.PORT',8781),patch('desktop.socket.socket') as socket:
            probe=socket.return_value.__enter__.return_value;probe.bind.side_effect=[OSError('occupied'),None]
            self.assertEqual(desktop.choose_port(),8782)
            self.assertEqual(os.environ['WUJI_STUDIO_PORT'],'8782')
            self.assertEqual(json.loads((Path(d)/'desktop-port.json').read_text())['port'],8782)

    def test_reopen_uses_saved_matching_app(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{},clear=True),patch('desktop.DATA',Path(d)),patch('desktop.ready',return_value=True) as ready,patch('desktop.PORT',8781),patch('desktop.URL',''),patch('runtime_paths.PORT',8781),patch('desktop.socket.socket') as socket:
            (Path(d)/'desktop-port.json').write_text('{"port":8790}')
            self.assertEqual(desktop.choose_port(),8790);ready.assert_called_once_with(8790);socket.assert_not_called()

if __name__=='__main__':unittest.main()
