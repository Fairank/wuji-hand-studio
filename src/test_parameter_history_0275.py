"""Corrupt sync history cannot block startup or replace user parameter files."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from parameter_store import ParameterStore


class ParameterHistoryStartupTests(unittest.TestCase):
    def check_record(self,record,*,valid=False,raw=None):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'motion_parameters.py'
            source=(Path(__file__).parent/'motion_parameters.py').read_bytes();path.write_bytes(source)
            history=root/'parameter_history';history.mkdir();saved=history/'web-last-sync.json'
            raw=json.dumps(record).encode('utf8') if raw is None else raw;saved.write_bytes(raw)
            store=ParameterStore(path)
            self.assertFalse(store.sync_status['busy'])
            self.assertEqual(store.sync_status['state'],'synced' if valid else 'not_synced')
            self.assertEqual(store.sync_status['revision'],'a'*64 if valid else None)
            self.assertIsInstance(store.sync_status['message'],str)
            self.assertEqual(path.read_bytes(),source)
            self.assertEqual(saved.read_bytes(),raw)
            snapshot=store.snapshot()
            self.assertIsNone(snapshot['error'])
            self.assertIsInstance(snapshot['revision'],str)

    def test_non_object_history_keeps_default_and_preserves_files(self):
        for value in (None,False,7,'old status',[],[['state','synced']]):
            with self.subTest(value=value):self.check_record(value)

    def test_invalid_object_history_cannot_restore_an_incomplete_sync(self):
        for value in ({},{'state':'syncing','message':'stuck','revision':None},
                {'state':'synced','message':'done','revision':None},
                {'state':'synced','message':{},'revision':'a'*64},
                {'state':{},'message':'done','revision':'a'*64},
                {'state':'synced','message':'done','revision':'wrong'}):
            with self.subTest(value=value):self.check_record(value)

    def test_valid_history_restores_only_completed_sync_and_never_busy(self):
        self.check_record({'busy':True,'state':'synced','message':'existing saved status','revision':'a'*64},valid=True)

    def test_oversized_history_is_retained_but_not_loaded(self):
        self.check_record({'state':'synced','message':'done','revision':'a'*64,'padding':'x'*70000})

    def test_excessive_json_nesting_is_retained_without_blocking_startup(self):
        self.check_record(None,raw=b'['*1200+b'null'+b']'*1200)

    def test_history_growth_before_open_cannot_bypass_read_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'motion_parameters.py'
            source=(Path(__file__).parent/'motion_parameters.py').read_bytes();path.write_bytes(source)
            history=root/'parameter_history';history.mkdir();saved=history/'web-last-sync.json'
            saved.write_text('{}',encoding='utf8')
            larger=json.dumps({'state':'synced','message':'grew','revision':'b'*64,'padding':'x'*70000}).encode('utf8')
            original=Path.open;changed=[]
            def grow(target,mode='r',*args,**kwargs):
                if target==saved and mode in ('r','rb') and not changed:
                    changed.append(True);saved.write_bytes(larger)
                return original(target,mode,*args,**kwargs)
            with patch.object(Path,'open',grow):store=ParameterStore(path)
            self.assertTrue(changed)
            self.assertEqual(store.sync_status['state'],'not_synced')
            self.assertEqual(path.read_bytes(),source)
            self.assertEqual(saved.read_bytes(),larger)


if __name__=='__main__':unittest.main()
