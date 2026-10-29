"""Rejected native calibration output remains available without success authority."""
from contextlib import contextmanager
import copy
import io
import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import native_calibration


SN='WG_SYNTHETIC_RESULT_0257'
USER='Synthetic'
SDK_USER=dict(user_id='owner-result-0257',display_name=USER)
RUN='a'*32


def result(**fields):
    value=dict(schema_version=2,calibration='hand_model',operation='calibrate',event='result',status='ok',user=copy.deepcopy(SDK_USER),
        result=dict(sdk_user=copy.deepcopy(SDK_USER),handedness='left',calibrated_urdf='/synthetic/left_hand.urdf',sn=SN))
    value['result'].update(fields)
    return value


def output(text):return json.dumps(dict(type='output',text=text))+'\n'


def ready():return json.dumps(dict(type='ready',source='official_jsonl_native',serial=SN,side='left'))+'\n'


def exited(code=0):return json.dumps(dict(type='exit',exitstatus=code))+'\n'


@contextmanager
def isolated_gate(*args,**kwargs):yield


def forbidden(*args,**kwargs):raise AssertionError('Test must not run a network or child process')


class NativeCalibrationEvidenceTests(unittest.TestCase):
    def collect(self,records,*,clean=True,read_error=None,model_error=None):
        class Reader(io.StringIO):
            def __iter__(self):
                yield from self.getvalue().splitlines(keepends=True)
                if read_error is not None:raise read_error
        process=SimpleNamespace(stdin=io.StringIO(),stdout=Reader(''.join(records)),stderr=io.StringIO(),wait=lambda timeout:0)
        root=Path(os.environ.get('WUJI_CONTROL_QA_ROOT',str(Path(__file__).resolve().parents[2]/'update-flow-0257/calibration-audit/test-temp')))
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='calibration-result-0257-',dir=root) as temporary:
            folder=Path(temporary)
            owner=SimpleNamespace(lock=threading.RLock(),process=process,reports=folder,
                run=dict(running=True,status='starting',id=RUN,serial=SN,user=USER,expected_side='left',
                         source='official_jsonl_native',sdk_user=copy.deepcopy(SDK_USER),result=None,event=None,error=None,event_count=0))
            with patch.object(socket,'socket',forbidden),patch.object(socket,'create_connection',forbidden),patch.object(subprocess,'Popen',forbidden),patch.object(native_calibration,'root_folder',return_value=folder),patch.object(native_calibration,'gate',isolated_gate),patch.object(native_calibration,'release',return_value=True) as release:
                with patch('calibration_portability.file_digest',return_value=('d'*64,32),side_effect=model_error) as digest:
                    service=native_calibration.NativeCalibration(owner)
                    service.token='b'*32
                    service.job=SimpleNamespace(close=lambda timeout:clean)
                    service._collect()
                snapshot=copy.deepcopy(owner.run)
                raw=(folder/(RUN+'-terminal.ansi')).read_text('utf8')
                saved=json.loads((folder/(RUN+'.json')).read_text('utf8'))
                return dict(run=snapshot,saved=saved,transcript=raw,release_count=release.call_count,
                            retained_process=owner.process is process,size=service.size,frames=list(service.frames),digest_calls=digest.call_count)

    def test_matching_result_still_completes_only_after_clean_zero_exit(self):
        raw=json.dumps(result())+'\n'
        actual=self.collect([ready(),output(raw),exited()])
        self.assertEqual(actual['run']['status'],'completed')
        self.assertEqual(actual['run']['exit_code'],0)
        self.assertTrue(actual['run']['cleanup_confirmed'])
        self.assertEqual(actual['run']['result'],result())
        self.assertEqual(actual['transcript'],raw)
        self.assertEqual(actual['release_count'],1)
        self.assertEqual((actual['run']['model_sha256'],actual['run']['model_bytes']),('d'*64,32))

    def test_missing_unreadable_or_redirected_model_keeps_official_evidence_without_completed(self):
        raw=json.dumps(result())+'\n'
        for error in (ValueError('model missing'),OSError('model unreadable'),ValueError('model redirected')):
            with self.subTest(error=error):
                actual=self.collect([ready(),output(raw),exited()],model_error=error)
                self.assertEqual(actual['run']['status'],'error')
                self.assertEqual(actual['run']['exit_code'],0)
                self.assertEqual(actual['run']['result'],result())
                self.assertIn(raw,actual['transcript'])
                self.assertIn('Saved hand model could not be verified',actual['run']['error'])
                self.assertNotIn('model_sha256',actual['run'])

    def test_mismatched_user_hand_and_model_remain_errors_with_original_evidence(self):
        for changes in (dict(sdk_user='Other'),dict(handedness='right'),dict(calibrated_urdf=' '),
                        dict(calibrated_urdf={'path':'/synthetic/left_hand.urdf'})):
            with self.subTest(changes=changes):
                raw=json.dumps(result(**changes))+'\n'
                actual=self.collect([ready(),output(raw),exited()])
                self.assertEqual(actual['run']['status'],'error')
                self.assertIsNone(actual['run']['result'])
                self.assertTrue(actual['run']['error'])
                self.assertIn(raw,actual['transcript'])
                self.assertEqual(actual['saved']['result'],None)

    def test_valid_progress_before_rejected_result_in_same_chunk_remains_raw_only(self):
        progress=dict(schema_version=2,calibration='hand_model',operation='calibrate',event='progress',feedback=dict(step_name='pinch_index'))
        raw=json.dumps(progress)+'\n'+json.dumps(result(sdk_user='Other'))+'\n'
        actual=self.collect([ready(),output(raw),exited()])
        self.assertEqual(actual['run']['status'],'error')
        self.assertIsNone(actual['run']['result'])
        self.assertIn(raw,actual['transcript'])

    def test_bad_jsonl_without_result_keeps_diagnostics_but_never_completes(self):
        raw='not JSONL {\n'
        actual=self.collect([ready(),output(raw),exited()])
        self.assertEqual(actual['run']['status'],'error')
        self.assertIsNone(actual['run']['result'])
        self.assertIn(raw,actual['transcript'])

    def test_malformed_transport_record_is_labeled_and_kept_bounded(self):
        raw='{"type":"output", invalid-json}\n'
        actual=self.collect([ready(),raw])
        self.assertEqual(actual['run']['status'],'error')
        self.assertIn('Rejected terminal transport record',actual['transcript'])
        self.assertIn(raw,actual['transcript'])
        self.assertIsNone(actual['run']['result'])

    def test_oversized_transport_keeps_only_bounded_prefix_and_no_success(self):
        raw='x'*(129*1024)+'\n'
        actual=self.collect([ready(),raw])
        self.assertEqual(actual['run']['status'],'error')
        self.assertIn('record exceeds',actual['run']['error'])
        self.assertIn(raw[:65536],actual['transcript'])
        self.assertLess(len(actual['transcript']),67000)

    def test_reader_error_retains_previous_raw_output_and_explicit_error(self):
        raw='original diagnostic before pipe read failed\n'
        actual=self.collect([ready(),output(raw)],read_error=OSError('synthetic pipe read failed'))
        self.assertEqual(actual['run']['status'],'error')
        self.assertIn(raw,actual['transcript'])
        self.assertIn('[Terminal transport error] synthetic pipe read failed',actual['transcript'])
        self.assertIsNone(actual['run']['result'])

    def test_unclean_job_after_rejected_result_retains_unknown_owner_and_process(self):
        raw=json.dumps(result(sdk_user='Other'))+'\n'
        actual=self.collect([ready(),output(raw),exited()],clean=False)
        self.assertEqual(actual['run']['status'],'error')
        self.assertTrue(actual['run']['running'])
        self.assertFalse(actual['run']['cleanup_confirmed'])
        self.assertTrue(actual['retained_process'])
        self.assertEqual(actual['release_count'],0)
        self.assertEqual(actual['digest_calls'],0)
        self.assertIn(raw,actual['transcript'])

    def test_evidence_buffer_remains_bounded_after_many_raw_records(self):
        chunks=[output('diagnostic-'+str(i)+'x'*60000+'\n') for i in range(39)]
        actual=self.collect([ready(),*chunks,output(json.dumps(result(sdk_user='Other'))+'\n'),exited()])
        self.assertEqual(actual['run']['status'],'error')
        self.assertLessEqual(actual['size'],2*1024*1024)
        self.assertEqual(len(actual['transcript']),actual['size'])
        self.assertIn('"sdk_user": "Other"',actual['transcript'])


class NativeCalibrationCleanupCompletionTests(unittest.TestCase):
    def test_actual_saved_model_is_hashed_before_completed_and_changes_block_continuation(self):
        from calibration_portability import file_digest,calibration_proof
        import test_calibration_guided as fixture
        with fixture.native_environment() as env:
            model=env.folder/'left_hand.urdf';data=b'<robot name="synthetic-0257" />';model.write_bytes(data)
            event=fixture.record('result');event['result']['calibrated_urdf']=str(model)
            records=[fixture.ready(),fixture.output(event),dict(type='exit',exitstatus=0)]
            env.process.stdout=io.StringIO(''.join(json.dumps(row)+'\n' for row in records))
            env.service.start_guided(fixture.SN,'left',fixture.USER)
            with patch('calibration_portability.file_digest',wraps=file_digest):env.service._collect()
            self.assertEqual(env.owner.run['status'],'completed')
            self.assertEqual((env.owner.run['model_sha256'],env.owner.run['model_bytes']),(hashlib.sha256(data).hexdigest(),len(data)))
            with patch('calibration_portability.file_digest',wraps=file_digest):
                proof=calibration_proof(dict(cleanup_required=False,run=copy.deepcopy(env.owner.run)))
                self.assertEqual(proof['model_sha256'],hashlib.sha256(data).hexdigest())
                model.write_bytes(data+b'\n')
                with self.assertRaisesRegex(ValueError,'differs from the verified'):
                    calibration_proof(dict(cleanup_required=False,run=copy.deepcopy(env.owner.run)))

    def ended_with_release_error(self,env):
        import test_calibration_guided as fixture
        env.owner.reports=env.folder/'reports'
        env.service.start_guided(fixture.SN,'left',fixture.USER)
        token=env.service.token
        with patch.object(native_calibration,'release',side_effect=PermissionError('synthetic marker retained')):
            env.service._collect()
        self.assertFalse(env.owner.run['running'])
        self.assertEqual(env.owner.run['status'],'error')
        self.assertFalse(env.owner.run['cleanup_confirmed'])
        self.assertIn('reservation was not released',env.owner.run['cleanup_error'])
        self.assertEqual(env.service.token,token)
        self.assertTrue(env.service.finished.is_set())
        self.assertTrue(env.process.stdin.closed);self.assertTrue(env.process.stdout.closed)
        self.assertTrue((env.owner.reports/(env.owner.run['id']+'.json')).is_file())
        return token

    def test_failed_marker_release_is_reported_and_exact_explicit_retry_acknowledges_cleanup_only(self):
        import cli_coordination
        from calibration_cli import CalibrationCLI
        import test_calibration_guided as fixture
        events=[fixture.ready(),fixture.output(fixture.record('result')),dict(type='exit',exitstatus=0)]
        with fixture.native_environment(events) as env:
            token=self.ended_with_release_error(env)
            status=CalibrationCLI(device_provider=lambda refresh:[]);status.run=env.owner.run;status.native=env.service
            status.cache=dict(available=False,calibration_supported=False,mode=None,users=[],current=None,devices=[],error=None,metadata_checked_at=None)
            status.cached_at=time.monotonic()
            self.assertTrue(status.snapshot(refresh=False)['cleanup_required'])
            result=env.service.cancel()
            self.assertTrue(result['cleanup_confirmed']);self.assertIsNone(result['cleanup_error'])
            self.assertEqual(result['status'],'error')
            self.assertEqual(cli_coordination.reservation(env.folder),{})
            self.assertIsNone(env.service.token)
            self.assertFalse(status.snapshot(refresh=False)['cleanup_required'])

    def test_retry_never_clears_replacement_marker(self):
        import cli_coordination
        import test_calibration_guided as fixture
        events=[fixture.ready(),fixture.output(fixture.record('result')),dict(type='exit',exitstatus=0)]
        with fixture.native_environment(events) as env:
            token=self.ended_with_release_error(env)
            foreign=dict(kind='calibration',token='e'*32,serial='WG_OTHER')
            cli_coordination.atomic(env.folder/'session.json',foreign)
            with patch.object(native_calibration,'release') as release,self.assertRaisesRegex(ValueError,'owner changed'):
                env.service.cancel()
            release.assert_not_called()
            self.assertEqual(cli_coordination.reservation(env.folder),foreign)
            self.assertEqual(env.service.token,token)
            self.assertFalse(env.owner.run['cleanup_confirmed'])

    def test_gate_exception_still_finishes_stream_and_report_cleanup(self):
        import test_calibration_guided as fixture
        events=[fixture.ready(),fixture.output(fixture.record('result')),dict(type='exit',exitstatus=0)]
        with fixture.native_environment(events) as env:
            env.owner.reports=env.folder/'reports'
            env.service.start_guided(fixture.SN,'left',fixture.USER)
            with patch.object(native_calibration,'gate',side_effect=OSError('synthetic gate unavailable')):
                env.service._collect()
            self.assertTrue(env.service.finished.is_set());self.assertTrue(env.process.stdin.closed)
            self.assertFalse(env.owner.run['running']);self.assertFalse(env.owner.run['cleanup_confirmed'])
            self.assertIsNotNone(env.service.token)
            self.assertTrue((env.owner.reports/(env.owner.run['id']+'.json')).is_file())


if __name__=='__main__':unittest.main()
