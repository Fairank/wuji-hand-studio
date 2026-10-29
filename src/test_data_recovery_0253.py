"""Exact-source failure/retry tests with file, stream, thread and SDK stubs only."""
import errno
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch
import zipfile

import console_agent
import doctor
import first_generation
import support_bundle

CONFIG=dict(mode='local',python='/synthetic/python',agent_directory='/synthetic',cli='wuji')


class InlineThread:
    def __init__(self,target,args=(),daemon=True):self.target,self.args=target,args
    def start(self):self.target(*self.args)


class PartialWrite:
    def __init__(self,stream):self.stream=stream
    def __getattr__(self,name):return getattr(self.stream,name)
    def __enter__(self):return self
    def __exit__(self,*args):return self.stream.__exit__(*args)
    def write(self,text):
        self.stream.write(text[:12]);raise OSError(errno.ENOSPC,'synthetic partial write/full disk')


class DoctorTransactions(unittest.TestCase):
    def test_both_launches_restore_retry_and_saved_evidence_after_constructor_or_start_failure(self):
        for operation in ('version','support'):
            for point in ('constructor','start'):
                with self.subTest(operation=operation,point=point),tempfile.TemporaryDirectory() as folder,patch.object(doctor,'DATA',Path(folder)),patch('doctor.load_config',return_value=CONFIG),patch('doctor.doctor_args',return_value=['synthetic-wuji']),patch('doctor.local_run',return_value=(0,'normal original output','')),patch('calibration_cli.controller_args',side_effect=lambda c,a:a),patch('support_bundle.export_support',return_value=dict(id='a'*32,exit_code=0)):
                    service=doctor.Doctor();service.state.update(report=dict(stdout='previous original output'),support=dict(id='b'*32))
                    fake=Mock();fake.start.side_effect=RuntimeError('synthetic start failure')
                    with patch('doctor.threading.Thread',side_effect=RuntimeError('synthetic constructor failure') if point=='constructor' else None,return_value=fake):
                        with self.assertRaises(RuntimeError):service.start_support() if operation=='support' else service.start('version')
                    state=service.snapshot();self.assertFalse(state['running']);self.assertEqual(state['status'],'error');self.assertEqual(state['report']['stdout'],'previous original output');self.assertEqual(state['support']['id'],'b'*32)
                    with patch('doctor.threading.Thread',InlineThread):service.start_support() if operation=='support' else service.start('version')
                    self.assertFalse(service.snapshot()['running']);self.assertEqual(service.snapshot()['status'],'completed')

    def test_atomic_report_partial_write_or_replace_failure_preserves_saved_report_and_current_memory(self):
        for point in ('write','replace'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as folder,patch.object(doctor,'DATA',Path(folder)),patch('calibration_cli.controller_args',side_effect=lambda c,a:a):
                service=doctor.Doctor();path=Path(folder)/'doctor-latest.json'
                with patch('doctor.local_run',return_value=(0,'first complete output','')):service.work(CONFIG,['synthetic'],'version')
                before=path.read_bytes();factory=tempfile.NamedTemporaryFile
                failure=(patch('doctor.os.replace',side_effect=PermissionError('synthetic replace denied')) if point=='replace' else patch('doctor.tempfile.NamedTemporaryFile',side_effect=lambda *a,**kw:PartialWrite(factory(*a,**kw))))
                with failure,patch('doctor.local_run',return_value=(1,'second complete output','second stderr')):service.work(CONFIG,['synthetic'],'version')
                self.assertEqual(path.read_bytes(),before);self.assertEqual(doctor.Doctor().snapshot()['report']['stdout'],'first complete output')
                state=service.snapshot();self.assertEqual(state['report']['stdout'],'second complete output');self.assertEqual(state['report']['stderr'],'second stderr');self.assertIn('could not be saved',state['error']);self.assertFalse(list(Path(folder).glob('*.pending')))
                with patch('doctor.local_run',return_value=(0,'retry complete output','')):service.work(CONFIG,['synthetic'],'version')
                self.assertEqual(json.loads(path.read_text())['stdout'],'retry complete output');self.assertIsNone(service.snapshot()['error'])

    def reader_failure(self,point,unknown=False):
        process=Mock(stdout=io.BufferedReader(io.BytesIO(b'original stdout')),stderr=io.BufferedReader(io.BytesIO(b'original stderr')))
        process.poll.return_value=None;process.wait.return_value=0
        if unknown:process.wait.side_effect=TimeoutError('synthetic process exit remains unknown')
        readers=[];constructs=0
        class Reader:
            def __init__(self,target,args,daemon):
                nonlocal constructs
                constructs+=1
                if point=='constructor'+str(constructs):raise RuntimeError('synthetic reader construction failed')
                self.target,self.args,self.ident=target,args,None;self.joins=0;readers.append(self)
            def start(self):
                if point=='start'+str(readers.index(self)+1):raise RuntimeError('synthetic reader start failed')
                self.ident=1;self.target(*self.args)
            def join(self,timeout):
                if self.ident is None:raise AssertionError('Unstarted reader was joined')
                self.joins+=1
        with patch('doctor.subprocess.Popen',return_value=process),patch('doctor.threading.Thread',Reader):
            with self.assertRaises(TimeoutError if unknown else RuntimeError):doctor.local_run(['synthetic-cli'])
        process.kill.assert_called_once();process.wait.assert_called_once();self.assertTrue(process.stdout.closed);self.assertTrue(process.stderr.closed)
        self.assertTrue(all(reader.joins==0 for reader in readers if reader.ident is None))

    def test_reader_construction_or_start_failure_closes_fake_process_and_only_joins_started_threads(self):
        for point in ('constructor1','constructor2','start1','start2'):
            with self.subTest(point=point):self.reader_failure(point)

    def test_unknown_fake_process_exit_is_propagated_without_claiming_success(self):self.reader_failure('start2',unknown=True)


class SupportCollectionTransactions(unittest.TestCase):
    def test_ssh_success_failure_retry_preserves_raw_evidence_and_never_publishes_partial_archive(self):
        zipped=io.BytesIO()
        with zipfile.ZipFile(zipped,'w') as archive:archive.writestr('original.txt','original official log')
        content=zipped.getvalue()
        for failure in ('partial','missing','invalid','connect'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as folder,patch.object(support_bundle,'DATA',Path(folder)),patch('official_cli.cli_path',return_value='/synthetic/wuji'):
                mode='success'
                class Files:
                    def stat(self,path):
                        if mode=='missing':raise FileNotFoundError('synthetic remote archive missing')
                        return N(st_size=len(content))
                    def get(self,source,destination):
                        Path(destination).write_bytes(content if mode=='success' else b'partial/corrupt archive')
                        if mode=='partial':raise PermissionError('synthetic collection denied')
                    def close(self):pass
                client=N(open_sftp=lambda:Files(),close=lambda:None)
                def connect(config):
                    if mode=='connect':raise OSError('synthetic transport unavailable')
                    return client
                with patch('bridge_config.ssh_client',side_effect=connect):
                    runner=Mock(return_value=(0,'ORIGINAL_STDOUT','ORIGINAL_STDERR'));config=dict(CONFIG,mode='ssh')
                    first=support_bundle.export_support(config,1,'first',None,Mock(),runner);self.assertTrue(first['collection_complete'])
                    mode=failure;failed=support_bundle.export_support(config,1,'second',None,Mock(),runner);self.assertFalse(failed['official_archive_available']);self.assertFalse(failed['collection_complete']);self.assertIn('collection_error',failed)
                    base=Path(folder)/'support'/failed['id'];self.assertIn('ORIGINAL_STDOUT',(base/'fault-brief.txt').read_text(encoding='utf-8-sig'));self.assertIn('ORIGINAL_STDERR',(base/'fault-brief.txt').read_text(encoding='utf-8-sig'));self.assertFalse((base/'official-wuji-support.zip').exists());self.assertFalse((base/'official-wuji-support.zip.partial').exists())
                    with zipfile.ZipFile(base/'workbench-support.zip') as archive:self.assertNotIn('official-wuji-support.zip',archive.namelist())
                    mode='success';retry=support_bundle.export_support(config,1,'retry',None,Mock(),runner);self.assertTrue(retry['collection_complete']);self.assertNotEqual(first['id'],retry['id']);self.assertTrue((Path(folder)/'support'/first['id']/'workbench-support.zip').is_file())

    def test_wsl_copy_failure_preserves_original_output_and_retry(self):
        zipped=io.BytesIO()
        with zipfile.ZipFile(zipped,'w') as archive:archive.writestr('original.txt','original official log')
        content=zipped.getvalue();original_file=Path.is_file;original_stat=Path.stat
        def source(path):return str(path).startswith('\\\\wsl.localhost\\SyntheticRuntime\\tmp\\')
        def is_file(path):return True if source(path) else original_file(path)
        def stat(path,*a,**kw):return N(st_size=len(content)) if source(path) else original_stat(path,*a,**kw)
        mode='success'
        def copy(path,destination):
            Path(destination).write_bytes(content if mode=='success' else b'partial')
            if mode!='success':raise OSError(errno.ENOSPC,'synthetic copy disk full')
        with tempfile.TemporaryDirectory() as folder,patch.object(support_bundle,'DATA',Path(folder)),patch('official_cli.cli_path',return_value='/synthetic/wuji'),patch('managed_runtime.check_ready'),patch('managed_runtime.args',side_effect=lambda a:a),patch('managed_runtime.NAME','SyntheticRuntime'),patch.object(Path,'is_file',is_file),patch.object(Path,'stat',stat),patch('support_bundle.shutil.copyfile',side_effect=copy):
            runner=Mock(return_value=(0,'WSL_ORIGINAL_STDOUT','WSL_ORIGINAL_STDERR'));config=dict(CONFIG,mode='wsl')
            self.assertTrue(support_bundle.export_support(config,1,'first',None,runner,Mock())['collection_complete']);mode='fail'
            failed=support_bundle.export_support(config,1,'failed',None,runner,Mock());self.assertFalse(failed['official_archive_available']);self.assertIn('WSL_ORIGINAL_STDOUT',(Path(folder)/'support'/failed['id']/'fault-brief.txt').read_text(encoding='utf-8-sig'))
            mode='success';self.assertTrue(support_bundle.export_support(config,1,'retry',None,runner,Mock())['collection_complete'])


def feedback(seq,now):return dict(seq=seq,host_s=now,device_timestamp_us=int(now*1e6),joints=[])


class RecorderRecovery(unittest.TestCase):
    def test_partial_summary_write_preserves_pending_and_retry_uses_original_reason_time_and_raw_frames(self):
        original=Path.open
        with tempfile.TemporaryDirectory() as folder,patch('device_profiles.controller_profile',return_value=dict(generation='hand2',side='left')):
            rec=console_agent.Recorder(folder);rec.start('baseline',10,1.);rec.add(feedback(1,1.01))
            def partial(path,*a,**kw):
                stream=original(path,*a,**kw);return PartialWrite(stream) if path.name=='summary.pending.json' else stream
            with patch.object(Path,'open',partial),self.assertRaises(OSError):rec.finish('user_stop',1.5)
            self.assertTrue(rec.active);self.assertTrue(rec.state()['summary_pending']);self.assertFalse((rec.path/'summary.json').exists());self.assertTrue((rec.path/'summary.pending.json').exists())
            rec.add(feedback(2,1.6));self.assertEqual(rec.frames,1)
            with self.assertRaises(ValueError):rec.start('thumb',10,2.)
            result=rec.finish('disconnected',5.);self.assertEqual(result['reason'],'user_stop');self.assertEqual(result['seconds'],.5);self.assertEqual(result['frames'],1);self.assertFalse(rec.active);self.assertIsNone(rec.pending_report);self.assertFalse((rec.path/'summary.pending.json').exists());self.assertEqual(len((rec.path/'feedback.jsonl').read_text().splitlines()),1);self.assertIsNone(rec.finish('again',6.))

    def test_summary_replace_failure_retains_valid_pending_file_and_explicit_retry(self):
        with tempfile.TemporaryDirectory() as folder,patch('device_profiles.controller_profile',return_value=dict(generation='hand2',side='left')):
            rec=console_agent.Recorder(folder);rec.start('baseline',10,1.);rec.add(feedback(1,1.01))
            with patch('console_agent.os.replace',side_effect=PermissionError('synthetic summary replace denied')),self.assertRaises(OSError):rec.finish('user_stop',1.5)
            pending=json.loads((rec.path/'summary.pending.json').read_text());self.assertEqual(pending['reason'],'user_stop');self.assertTrue(rec.active)
            self.assertEqual(rec.finish('different reason',9.)['reason'],'user_stop')

    def test_raw_close_flush_failure_stays_unconfirmed_in_retry_report_and_state(self):
        with tempfile.TemporaryDirectory() as folder,patch('device_profiles.controller_profile',return_value=dict(generation='hand2',side='left')):
            rec=console_agent.Recorder(folder);rec.start('baseline',10,1.);rec.add(feedback(1,1.01));stream=rec.file
            class Close:
                calls=0
                def close(self):
                    self.calls+=1;stream.close()
                    if self.calls==1:raise OSError(errno.ENOSPC,'synthetic raw flush unconfirmed')
            rec.file=Close()
            with self.assertRaises(OSError):rec.finish('user_stop',1.5)
            self.assertEqual(rec.state()['raw_feedback_integrity'],'unconfirmed');report=rec.finish('retry',8.)
            self.assertFalse(report['raw_feedback_flush_confirmed']);self.assertEqual(report['raw_feedback_integrity'],'unconfirmed');self.assertIn('raw flush',report['raw_feedback_error']);self.assertEqual(rec.state()['raw_feedback_integrity'],'unconfirmed')

    def worker_case(self,continuous=False,auto=False):
        requests,events=queue.Queue(),queue.Queue();calls=[];records=[];attempts=0;real_open=Path.open
        class Subscription:
            counter=0
            def recv(self):
                self.counter+=1
                if self.counter==2:requests.put(dict(name='record',label='index',seconds=10))
                if not auto and self.counter in (18,22):requests.put(dict(name='stop'))
                if self.counter==(10000 if auto else 28):requests.put(dict(name='disconnect'))
                return N(header=N(seq=self.counter,timestamp_us=self.counter*1000,frame_id='left'),num_joints=1,joints=[N(nid=5,position=.2,velocity=.1,effort=.02)])
            def close(self):calls.append('subscription_close')
        class Hand:
            serial_number='synthetic-left'
            def handedness(self):return N(get=lambda:'left')
            def joint_states(self):return N(subscribe=Subscription)
            def joint_diagnostics(self):return N(subscribe=lambda:N(recv=lambda:None,close=lambda:None))
            def disconnect(self):calls.append('disconnect')
        sdk=N(SdkManager=N(instance=lambda:N(connect=lambda **kw:Hand())),Handedness=N(Left='left'),WujiHand2=N())
        with tempfile.TemporaryDirectory() as folder:
            original=console_agent.Recorder
            def create():
                rec=original(folder);records.append(rec)
                if auto:
                    start=rec.start
                    def expired(*args):start(*args);rec.started-=10
                    rec.start=expired
                return rec
            def fail(path,*a,**kw):
                nonlocal attempts
                stream=real_open(path,*a,**kw)
                if path.name=='summary.pending.json':
                    attempts+=1
                    if continuous or attempts==1:return PartialWrite(stream)
                return stream
            with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch('console_agent.Recorder',side_effect=create),patch.object(Path,'open',fail):console_agent.worker('',requests,events)
            emitted=[]
            while not events.empty():emitted.append(events.get())
            self.assertEqual(calls[-2:],['subscription_close','disconnect']);self.assertTrue(any(e['type']=='notice' for e in emitted));self.assertTrue(any(e['type']=='closed' for e in emitted));self.assertFalse(any(e['type']=='error' for e in emitted))
            reports=[e['report'] for e in emitted if e['type']=='report']
            if continuous:self.assertEqual(reports,[]);self.assertIsNotNone(records[0].pending_report);self.assertEqual(attempts,2 if auto else 3)
            else:self.assertEqual(len(reports),1);self.assertEqual(reports[0]['reason'],'user_stop');self.assertEqual(attempts,2)

    def test_hand2_stop_failure_explicit_retry_reports_once_and_disconnects(self):self.worker_case()
    def test_hand2_continuous_failure_does_not_publish_saved_report_or_skip_disconnect(self):self.worker_case(True)
    def test_automatic_finish_failure_waits_for_explicit_operation_before_retrying_storage(self):self.worker_case(True,True)

    def test_hand1_finally_keeps_pending_and_closes_transport_when_summary_storage_fails(self):
        calls=[];requests,events=queue.Queue(),queue.Queue();requests.put(dict(name='disconnect'));real_open=Path.open
        with tempfile.TemporaryDirectory() as folder,patch('device_profiles.controller_profile',return_value=dict(generation='hand1',side='left')):
            rec=console_agent.Recorder(folder);rec.start('baseline',10,1.);rec.add(feedback(1,1.01))
            hand=N(serial_number='synthetic-hand1',joint_states=lambda:N(subscribe=lambda:N(recv=lambda:None,close=lambda:calls.append('subscription_close'))),disconnect=lambda:calls.append('disconnect'))
            sdk=N(SdkManager=N(instance=lambda:N()),DeviceType=N(WujiHand='hand1'),JointCommand=lambda *a:None,LowPass=lambda *a:None)
            def fail(path,*a,**kw):
                stream=real_open(path,*a,**kw);return PartialWrite(stream) if path.name=='summary.pending.json' else stream
            with patch.dict(sys.modules,{'wuji_sdk':sdk}),patch('console_agent.Recorder',return_value=rec),patch('first_generation.controller_profile',return_value=dict(id='hand1_left',generation='hand1',side='left')),patch('first_generation.require_identity'),patch('device_versions.read_handle_version',return_value={}),patch.object(Path,'open',fail):first_generation.worker_first('',requests,events,prepared_hand=hand)
            emitted=[]
            while not events.empty():emitted.append(events.get())
            self.assertEqual(calls,['subscription_close','disconnect']);self.assertIsNotNone(rec.pending_report);self.assertTrue(any(e['type']=='notice' for e in emitted));self.assertTrue(any(e['type']=='closed' for e in emitted));self.assertFalse(any(e['type']=='report' for e in emitted))


if __name__=='__main__':unittest.main()
