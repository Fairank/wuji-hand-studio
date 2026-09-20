import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from desktop_tools import dispatch, verify_update, idle


class DesktopToolsTests(unittest.TestCase):
    def test_reject_arbitrary_script_shell_and_pages(self):
        host=Mock()
        for payload in ({'operation':'exec','script':'anything'}, {'operation':'page','page':'http://example.com'},
                        {'operation':'resize','width':True,'height':700},
                        {'operation':'appearance','key':'reduceMotion','value':'false'},None):
            with self.assertRaises(ValueError):dispatch(host,payload)
        self.assertEqual(host.mock_calls,[])

    def test_busy_never_closes_or_updates(self):
        host=Mock();host.server.controller.snapshot.return_value=dict(connection='connected')
        host.server.controller.doctor.snapshot.return_value={}
        for op in ('close_idle','prepare_update'):
            with self.assertRaises(ValueError):dispatch(host,dict(operation=op))
        host.stop_and_close.assert_not_called()

    def test_idle_rejects_unknown_hardware_or_unfinished_work(self):
        state=dict(connection='disconnected',hardware=dict(active=False))
        self.assertTrue(idle(state,{}))
        for addition in (dict(hardware={}),dict(glove=dict(busy=True)),dict(recording=dict(active=True)),dict(parameter_sync=dict(busy=True))):
            self.assertFalse(idle(dict(state,**addition),{}))
        self.assertFalse(idle(state,dict(running=True)))

    def test_update_requires_edition_hash_version_and_local_path(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'updates').mkdir();data=b'MZ'+b'test installer';digest=hashlib.sha256(data).hexdigest()
            p=root/'updates/HandWorkbench-0.1.5-windows-x64-setup.exe';p.write_bytes(data)
            edition=dict(name='basic',version='0.1.4')
            self.assertEqual(verify_update(root,edition,'0.1.5',digest)['installer'],str(p))
            for version,sha,ed in [('0.1.5','0'*64,edition),('0.1.3',digest,edition),('../0.1.5',digest,edition),('0.1.5',digest,dict(name='workbench',version='0.1.6'))]:
                with self.assertRaises(ValueError):verify_update(root,ed,version,sha)
            p.write_bytes(b'not an exe')
            with self.assertRaises(ValueError):verify_update(root,edition,'0.1.5',hashlib.sha256(p.read_bytes()).hexdigest())


if __name__=='__main__':unittest.main()
