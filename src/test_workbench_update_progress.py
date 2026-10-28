"""Real streaming/staging and service state; only transport/installer are doubles."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event

import workbench_update as update
from workbench_release import WorkbenchRelease,WorkbenchReleaseError,LATEST_URL
from test_workbench_release import Network,release
from test_workbench_update_service import ServiceTests

class ProgressTests(unittest.TestCase):
    def test_newer_release_does_not_strand_prepared_older_installer(self):
        with ServiceTests().environment() as env:
            ServiceTests().ready(env)
            env.store.check.return_value=dict(available=True,latest_version='0.2.31')
            env.service.start('check');env.service.thread.run_to_end()
            state=env.service.snapshot()
            self.assertEqual(state['phase'],'available')
            self.assertEqual(state['latest_version'],'0.2.31')
            self.assertEqual(state['prepared']['version'],'0.2.30')
            env.service.start('prepare');env.service.thread.run_to_end()
            env.store.prepare.assert_called_once()

    def test_stream_progress_tracks_actual_bytes_and_then_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            payload=b'MZ'+b'x'*150000;metadata=release(payload);network=Network()
            network.responses[metadata['assets'][0]['browser_download_url']]=payload
            events=[];store=WorkbenchRelease(folder,'0.2.29',runner=network)
            result=store.prepare(metadata,progress=lambda e:events.append(dict(e)))
            self.assertTrue(result['prepared'])
            counts=[e['received_bytes'] for e in events if e['stage']=='downloading']
            self.assertEqual(counts,[0,65536,131072,len(payload)])
            self.assertEqual(events[-1],dict(stage='verifying',received_bytes=len(payload),total_bytes=len(payload)))
            events.clear();store.prepare(metadata,progress=events.append)
            self.assertEqual(len(network.calls),1)
            self.assertEqual([e['stage'] for e in events],['verifying'])

    def test_bad_hash_never_becomes_ready_and_retry_clears_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            payload=b'MZ'+b'x'*90000;metadata=release(payload);network=Network()
            url=metadata['assets'][0]['browser_download_url'];network.responses[url]=b'z'*len(payload)
            store=WorkbenchRelease(folder,'0.2.29',runner=network);events=[]
            with self.assertRaisesRegex(WorkbenchReleaseError,'SHA-256'):store.prepare(metadata,progress=events.append)
            self.assertEqual(list(store.versions.iterdir()),[])
            network.responses[url]=payload
            self.assertTrue(store.prepare(metadata,progress=events.append)['prepared'])

    def test_cancel_after_actual_chunk_removes_partial_and_no_prepared_result(self):
        with tempfile.TemporaryDirectory() as folder:
            payload=b'MZ'+b'x'*150000;metadata=release(payload);network=Network();stop=Event()
            network.responses[metadata['assets'][0]['browser_download_url']]=payload
            def progress(event):
                if event['received_bytes']>0:stop.set()
            store=WorkbenchRelease(folder,'0.2.29',runner=network)
            with self.assertRaisesRegex(WorkbenchReleaseError,'cancelled'):store.prepare(metadata,cancel_event=stop,progress=progress)
            self.assertEqual(list(store.versions.iterdir()),[])

    def test_service_exposes_live_progress_and_resets_on_retry(self):
        with ServiceTests().environment() as env:
            env.service.latest=env.latest;observed=[]
            def prepare(*args,progress,**kwargs):
                progress(dict(stage='downloading',received_bytes=42,total_bytes=100))
                observed.append(env.service.snapshot())
                raise OSError('Synthetic transport interrupted')
            env.store.prepare.side_effect=prepare
            env.service.start('prepare');env.service.thread.run_to_end()
            self.assertEqual(observed[0]['progress']['received_bytes'],42)
            self.assertTrue(observed[0]['busy'])
            self.assertEqual(env.service.snapshot()['phase'],'error')
            env.store.prepare.side_effect=None
            self.assertIsNone(env.service.start('prepare')['progress'])
            env.service.thread.run_to_end()
            self.assertEqual(env.service.snapshot()['phase'],'ready')

    def test_restart_only_reports_exact_verified_launched_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);updates=root/'updates';updates.mkdir()
            pointer=dict(app_id=update.APP_ID,repository=update.REPOSITORY,token='a'*32,version='0.2.45',sha256='b'*64)
            (updates/'last-application-update.json').write_text(json.dumps(pointer),'utf-8')
            result=updates/('apply-'+pointer['token']+'.result.json')
            good=dict(token=pointer['token'],version=pointer['version'],sha256=pointer['sha256'],phase='installed',launch_started=True,version_verified=True)
            for change in (dict(token='c'*32),dict(version='0.2.46'),dict(sha256='d'*64),dict(phase='error'),dict(launch_started=False),dict(version_verified=False)):
                result.write_text(json.dumps(dict(good,**change)),'utf-8')
                self.assertEqual(update.WorkbenchUpdate(root,dict(version='0.2.45')).state['phase'],'idle')
            result.write_text(json.dumps(good),'utf-8')
            self.assertEqual(update.WorkbenchUpdate(root,dict(version='0.2.45')).state['phase'],'completed')
            self.assertEqual(update.WorkbenchUpdate(root,dict(version='0.2.44')).state['phase'],'idle')

    def test_result_arriving_after_constructor_is_observed_without_overwriting_check(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);updates=root/'updates';updates.mkdir()
            pointer=dict(app_id=update.APP_ID,repository=update.REPOSITORY,token='a'*32,version='0.2.45',sha256='b'*64)
            (updates/'last-application-update.json').write_text(json.dumps(pointer),'utf-8')
            service=update.WorkbenchUpdate(root,dict(version='0.2.45'))
            service.state.update(phase='checking',busy=True)
            (updates/('apply-'+pointer['token']+'.result.json')).write_text(json.dumps(dict(pointer,phase='installed',launch_started=True,version_verified=True)),'utf-8')
            service._completion_checked_at=0
            state=service.snapshot()
            self.assertEqual(state['phase'],'checking');self.assertTrue(state['busy'])
            self.assertEqual(state['completed_version'],'0.2.45')
