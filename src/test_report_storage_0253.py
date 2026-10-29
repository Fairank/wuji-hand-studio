"""Report/storage stream recovery; no real process, network, SDK or device."""
import collections
import errno
import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
import zipfile

import console_server
import support_bundle
from test_update_path_errors_0251 import method
REPORT_GET=method('do_GET')


class InlineThread:
    def __init__(self,target,args=(),**kwargs):self.target,self.args=target,args
    def start(self):self.target(*self.args)


def controller(root):
    c=console_server.Controller.__new__(console_server.Controller)
    c.lock=threading.RLock();c.reports=Path(root)/'reports';c.generation=c.hardware_generation=1
    c.state=dict(connection='connected',message='',recording={'active':False},hardware={'active':None,'stop_confirmed':False},sessions=[],motion_history=[])
    c.session_devices={1:'SYNTHETIC'};c.session_epochs={1:1};c.feedback_sessions={1:dict(worker_closed=False,reader_finished=False,transport_closed=False)}
    c.termination=Mock();c.glove=Mock();c.log=Mock();c.clear_live=Mock();c.send=Mock();c.client=c.stdin=None
    return c


def report(identifier='a'*32,payload='original complete payload'):
    return dict(id=identifier,frames=1,reason='synthetic completed',peak_actual_delta_deg=0.,return_error_deg=0.,full_evidence=payload)


def get_report(c,identifier):
    target=type('Target',(),{})();target.path='/api/report?id='+identifier;target.allowed_host=lambda:True;target.reply=Mock()
    target.server=type('Server',(),{'controller':c})();REPORT_GET(target);return target.reply.call_args


class ControllerReportRecovery(unittest.TestCase):
    def test_both_report_types_continue_consuming_notice_and_closed_ack_after_storage_failure(self):
        for kind in ('report','motion_report'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);c=controller(root);original=report();consumed=[]
                expected=(c.reports if kind=='report' else root/'motion_reports')/(original['id']+'.json')
                events=[dict(type=kind,report=original),dict(type='notice',message='after report'),dict(type='closed',device_id='SYNTHETIC',hardware=dict(active=None,stop_confirmed=False))]
                class Output:
                    channel=type('Channel',(),{'closed':True})()
                    def __iter__(self):
                        for event in events:
                            if event['type']=='notice':
                                assert c.state['hardware']['active'] is None
                                assert c.termination.observe.call_count==0
                            consumed.append(event['type']);yield 'WUJI_JSON:'+json.dumps(event)+'\n'
                client=Mock();client.agent_command='never executed';client.exec_command.return_value=(io.StringIO(),Output(),io.StringIO());client.close.return_value=True;c.factory=Mock(return_value=client)
                replace=console_server.os.replace
                def fail(source,target):
                    if target==expected:raise OSError(errno.ENOSPC,'synthetic disk full')
                    return replace(source,target)
                with patch.object(console_server,'DATA',root),patch('console_server.os.replace',side_effect=fail),patch('console_server.threading.Thread',InlineThread):c.read_session({'name':'connect'},1)
                self.assertEqual(consumed,[kind,'notice','closed']);self.assertTrue(c.feedback_sessions[1]['worker_closed']);self.assertTrue(c.feedback_sessions[1]['reader_finished']);self.assertTrue(c.feedback_sessions[1]['transport_closed']);self.assertIsNone(c.state['hardware']['active']);self.assertFalse(c.state['hardware']['stop_confirmed'])
                row=c.state['sessions' if kind=='report' else 'motion_history'][0];self.assertFalse(row['workbench_storage']['saved']);self.assertTrue(row['workbench_storage']['memory_available']);self.assertEqual(json.loads(c.unsaved_report(original['id'])),original);self.assertFalse(expected.exists())
                client.close.assert_called_once()

    def test_exact_memory_export_is_read_only_and_original_json_has_no_storage_metadata(self):
        for kind in ('report','motion_report'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                c=controller(folder);original=report()
                with patch('console_server.os.replace',side_effect=PermissionError('synthetic denied')):c.event(dict(type=kind,report=original),1)
                with patch.object(Path,'open',side_effect=AssertionError('Cached GET must not access files')),patch('console_server.os.replace',side_effect=AssertionError('GET must not repair storage')):call=get_report(c,original['id'])
                self.assertEqual(call.args[0],200);self.assertEqual(json.loads(call.args[1]),original);self.assertNotIn('workbench_storage',json.loads(call.args[1]));self.assertIsNone(c.unsaved_report('b'*32))
                with patch.object(Path,'read_bytes',side_effect=FileNotFoundError()):self.assertEqual(get_report(c,'b'*32).args[0],404)

    def test_success_failure_retry_preserves_first_disk_report_then_commits_new_original(self):
        with tempfile.TemporaryDirectory() as folder:
            c=controller(folder);first=report(payload='first original');c.event(dict(type='report',report=first),1);path=c.reports/(first['id']+'.json');before=path.read_bytes()
            second=report(payload='second original')
            with patch('console_server.os.replace',side_effect=OSError(errno.ENOSPC,'synthetic disk full')):c.event(dict(type='report',report=second),1)
            self.assertEqual(path.read_bytes(),before);self.assertEqual(json.loads(get_report(c,second['id']).args[1]),second)
            retry=report(payload='retry original');c.event(dict(type='report',report=retry),1)
            self.assertIsNone(c.unsaved_report(retry['id']));self.assertEqual(json.loads(path.read_bytes()),retry);self.assertEqual(json.loads(get_report(c,retry['id']).args[1]),retry);self.assertNotIn('workbench_storage',c.state['sessions'][0]);self.assertFalse(list(c.reports.glob('*.pending')))

    def test_cache_limits_update_evicted_metadata_and_oversized_report_stays_unavailable(self):
        with tempfile.TemporaryDirectory() as folder,patch('console_server.REPORT_MEMORY_ITEMS',2),patch('console_server.REPORT_MEMORY_BYTES',2000),patch('console_server.REPORT_MEMORY_FILE_BYTES',700),patch('console_server.os.replace',side_effect=PermissionError('synthetic denied')):
            c=controller(folder)
            for index in range(3):c.event(dict(type='report',report=report(identifier=f'{index:032x}')),1)
            self.assertEqual(len(c._unsaved_reports),2);self.assertLessEqual(c._unsaved_report_bytes,2000);self.assertIsNone(c.unsaved_report('0'*32));self.assertFalse(next(row for row in c.state['sessions'] if row['id']=='0'*32)['workbench_storage']['memory_available'])
            large=report(identifier='f'*32,payload='x'*1000);c.event(dict(type='motion_report',report=large),1)
            self.assertIsNone(c.unsaved_report(large['id']));self.assertFalse(c.state['motion_history'][0]['workbench_storage']['memory_available']);self.assertTrue(any('cannot be exported' in call.args[0] for call in c.log.call_args_list))
            c.event(dict(type='closed',device_id='SYNTHETIC',hardware=dict(active=None,stop_confirmed=False)),1);self.assertTrue(c.feedback_sessions[1]['worker_closed']);self.assertIsNone(c.state['hardware']['active'])


class LocalSupportValidation(unittest.TestCase):
    def test_local_success_invalid_or_missing_archive_retry_retains_outputs_and_no_partial_attachment(self):
        zipped=io.BytesIO()
        with zipfile.ZipFile(zipped,'w') as archive:archive.writestr('original.txt','official original logs')
        for fault in ('invalid','missing'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as folder,patch.object(support_bundle,'DATA',Path(folder)),patch('official_cli.cli_path',return_value='/synthetic/wuji'):
                mode='success'
                def run(command,**kwargs):
                    output=Path(command[-1]);self.assertEqual(output.suffix,'.zip')
                    if mode!='missing':output.write_bytes(zipped.getvalue() if mode=='success' else b'incomplete ZIP')
                    return (0 if mode=='success' else 1),'LOCAL_ORIGINAL_STDOUT','LOCAL_ORIGINAL_STDERR'
                first=support_bundle.export_support(dict(mode='local'),1,'first',None,run,Mock());self.assertTrue(first['collection_complete']);first_archive=(Path(folder)/'support'/first['id']/'workbench-support.zip').read_bytes()
                mode=fault;failed=support_bundle.export_support(dict(mode='local'),1,'second',None,run,Mock());self.assertFalse(failed['official_archive_available']);self.assertFalse(failed['collection_complete']);self.assertEqual(failed['exit_code'],1);self.assertEqual(failed['stdout'],'LOCAL_ORIGINAL_STDOUT');self.assertEqual(failed['stderr'],'LOCAL_ORIGINAL_STDERR')
                base=Path(folder)/'support'/failed['id'];brief=(base/'fault-brief.txt').read_text(encoding='utf-8-sig');self.assertIn('LOCAL_ORIGINAL_STDOUT',brief);self.assertIn('LOCAL_ORIGINAL_STDERR',brief);self.assertFalse((base/'official-wuji-support.zip').exists())
                with zipfile.ZipFile(base/'workbench-support.zip') as archive:self.assertNotIn('official-wuji-support.zip',archive.namelist())
                mode='success';self.assertTrue(support_bundle.export_support(dict(mode='local'),1,'retry',None,run,Mock())['collection_complete']);self.assertEqual((Path(folder)/'support'/first['id']/'workbench-support.zip').read_bytes(),first_archive)


if __name__=='__main__':unittest.main()
