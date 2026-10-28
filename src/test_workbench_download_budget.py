"""Slow but progressing updates, stalls and retries; no network or installation."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import workbench_release as module
from test_workbench_release import Network,Response,release,LATEST_URL,VERSION


class DownloadBudgetTests(unittest.TestCase):
    def setUp(self):
        qa_root=Path(os.environ.get('WUJI_RELEASE_QA_ROOT',str(Path(__file__).resolve().parents[3]/'outputs/update-download-qa')))
        qa_root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=qa_root);self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)/'isolated';self.data.mkdir()
        self.payload=b'MZ'+b'PUBLIC SYNTHETIC DATA'*14000
        self.release=release(self.payload);self.url=self.release['assets'][0]['browser_download_url']
        self.network=Network();self.network.responses[LATEST_URL]=json.dumps(self.release).encode()
        self.network.responses[self.url]=self.payload;self.clock=0.

    def advance(self):self.clock+=60.
    def store(self,**kwargs):return module.WorkbenchRelease(self.data,'0.2.29',runner=self.network,**kwargs)
    def no_stage(self):self.assertEqual(list((self.data/'updates/versions').glob('.prepare-*')),[])

    def test_default_progressing_transfer_can_exceed_old_three_minute_limit(self):
        self.network.responses[self.url]=lambda url:Response(self.payload,url,after_read=self.advance)
        store=self.store();events=[]
        with patch.object(module.time,'monotonic',side_effect=lambda:self.clock):
            result=store.prepare(self.release,progress=events.append)
        self.assertGreater(self.clock,180)
        self.assertEqual(Path(result['installer']).read_bytes(),self.payload)
        self.assertEqual(result['sha256'],hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(events[-1],dict(stage='verifying',received_bytes=len(self.payload),total_bytes=len(self.payload)))
        self.assertEqual(store.metadata_timeout,30)
        self.assertTrue(all(0<call[2]<=15 for call in self.network.calls))
        self.no_stage()

    def test_explicit_short_total_limit_still_stops_and_does_not_commit(self):
        self.network.responses[self.url]=lambda url:Response(self.payload,url,after_read=self.advance)
        with patch.object(module.time,'monotonic',side_effect=lambda:self.clock):
            with self.assertRaisesRegex(module.WorkbenchReleaseError,'timed out'):self.store(timeout=180).prepare(self.release)
        self.assertFalse((self.data/'updates/versions'/VERSION).exists());self.no_stage()

    def test_metadata_remains_short_bounded_for_both_check_and_prepare(self):
        def slow_metadata(url):
            def delay():self.clock+=31
            return Response(json.dumps(self.release).encode(),url,after_read=delay)
        self.network.responses[LATEST_URL]=slow_metadata
        for operation in ('check','prepare'):
            self.clock=0
            with self.subTest(operation=operation),patch.object(module.time,'monotonic',side_effect=lambda:self.clock):
                with self.assertRaisesRegex(module.WorkbenchReleaseError,'timed out'):getattr(self.store(),operation)()
            self.assertFalse((self.data/'updates').exists())

    def test_midstream_socket_stall_keeps_progress_and_retry_verifies_whole_file(self):
        class Interrupted(Response):
            def read(inner,amount=-1):
                if inner.tell():raise TimeoutError('synthetic stalled socket')
                return super().read(amount)
        self.network.responses[self.url]=lambda url:Interrupted(self.payload,url)
        events=[];store=self.store()
        with self.assertRaisesRegex(module.WorkbenchReleaseError,'download stalled'):
            store.prepare(self.release,progress=events.append)
        self.assertEqual(events[-1]['received_bytes'],65536)
        self.assertFalse((self.data/'updates/versions'/VERSION).exists());self.no_stage()
        self.network.responses[self.url]=self.payload
        result=store.prepare(self.release)
        self.assertEqual(Path(result['installer']).read_bytes(),self.payload);self.no_stage()

    def test_cancelled_slow_transfer_cannot_commit_and_explicit_retry_succeeds(self):
        cancelled=threading.Event()
        def received():self.advance();cancelled.set()
        self.network.responses[self.url]=lambda url:Response(self.payload,url,after_read=received)
        store=self.store()
        with patch.object(module.time,'monotonic',side_effect=lambda:self.clock):
            with self.assertRaisesRegex(module.WorkbenchReleaseError,'cancelled'):store.prepare(self.release,cancel_event=cancelled)
        self.assertFalse((self.data/'updates/versions'/VERSION).exists());self.no_stage()
        cancelled.clear();self.network.responses[self.url]=self.payload
        result=store.prepare(self.release,cancel_event=cancelled)
        self.assertEqual(result['sha256'],hashlib.sha256(self.payload).hexdigest())

    def test_invalid_operation_limits_are_rejected_without_creating_files(self):
        for kwargs in ({'timeout':3601},{'metadata_timeout':601},{'metadata_timeout':True},{'metadata_timeout':float('nan')},{'metadata_timeout':0}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.store(**kwargs)
        self.assertEqual(list(self.data.iterdir()),[])

    def test_same_tag_new_official_asset_retires_verified_packet_then_downloads_replacement(self):
        store=self.store();old=store.prepare(self.release);old_file=Path(old['installer']);old_bytes=old_file.read_bytes()
        replacement=b'MZ REPLACED OFFICIAL SYNTHETIC PACKET'*240
        new_release=release(replacement);self.network.responses[self.url]=replacement
        result=store.prepare(new_release)
        self.assertEqual(Path(result['installer']).read_bytes(),replacement)
        retired=list((self.data/'updates/retired').glob('*/package'))
        self.assertEqual(len(retired),1)
        self.assertEqual((retired[0]/old_file.name).read_bytes(),old_bytes)
        history=json.loads((retired[0].parent/'retired.json').read_text())
        self.assertEqual(history['previous_sha256'],old['sha256'])
        self.assertEqual(history['replacement_sha256'],result['sha256'])
        self.no_stage()

    def test_packet_with_unrecognized_extra_file_is_preserved_and_never_moved(self):
        store=self.store();old=store.prepare(self.release);old_file=Path(old['installer'])
        extra=old_file.parent/'user-file.txt';extra.write_bytes(b'PRESERVE')
        replacement=b'MZ NEW PACKET';new_release=release(replacement)
        self.network.responses[self.url]=replacement
        with self.assertRaisesRegex(module.WorkbenchReleaseError,'Unexpected files'):store.prepare(new_release)
        self.assertEqual(old_file.read_bytes(),self.payload);self.assertEqual(extra.read_bytes(),b'PRESERVE')
        self.assertFalse((self.data/'updates/retired').exists())

if __name__=='__main__':unittest.main()
