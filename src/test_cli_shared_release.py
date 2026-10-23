"""Windows sharing violations do not release a replacement maintenance owner."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cli_coordination import release,reservation


class SharedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'session.json';self.token='a'*32
        self.path.write_text(json.dumps(dict(kind='firmware',token=self.token)),encoding='utf-8')

    def test_transient_read_handle_retries_exact_owner_then_releases(self):
        original=Path.unlink;calls=[]
        def unlink(path,**kwargs):
            calls.append(path)
            if len(calls)==1:raise PermissionError('synthetic Windows sharing violation')
            return original(path,**kwargs)
        with patch.object(Path,'unlink',unlink),patch('cli_coordination.time.sleep'):
            release(self.token,self.root)
        self.assertFalse(self.path.exists());self.assertEqual(len(calls),2)

    def test_replacement_owner_is_never_deleted_during_retry(self):
        calls=[]
        def unlink(path,**kwargs):
            calls.append(path)
            self.path.write_text(json.dumps(dict(kind='firmware',token='b'*32)),encoding='utf-8')
            raise PermissionError('synthetic ownership change during reader contention')
        with patch.object(Path,'unlink',unlink),patch('cli_coordination.time.sleep'):
            release(self.token,self.root)
        self.assertEqual(len(calls),1);self.assertEqual(reservation(self.root)['token'],'b'*32)

    def test_persistent_denial_is_bounded_and_keeps_marker(self):
        with patch.object(Path,'unlink',side_effect=PermissionError('synthetic persistent denial')) as unlink,patch('cli_coordination.time.sleep'):
            with self.assertRaises(PermissionError):release(self.token,self.root)
        self.assertEqual(unlink.call_count,10);self.assertEqual(reservation(self.root)['token'],self.token)


if __name__=='__main__':unittest.main()
