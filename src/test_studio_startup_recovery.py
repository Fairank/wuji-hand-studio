"""Studio launch failure recovery with exact leases and fully fake OS processes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from studio_component import StudioComponent,lease_state


class ImmediateThread:
    def __init__(self,target=None,args=(),**_):self.target=target;self.args=args
    def start(self):self.target(*self.args)
    def is_alive(self):return False
    def join(self,timeout=None):pass


class FailedThread(ImmediateThread):
    def start(self):raise RuntimeError('synthetic startup unavailable')


def failure(kind):
    return dict(side_effect=RuntimeError('synthetic constructor unavailable')) if kind=='construct' else dict(side_effect=FailedThread)


class StudioStartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.folder=Path(temp.name);self.component=StudioComponent(self.folder,idle_check=Mock())
        self.component.state['current']='2026.9.22'

    def retry_open(self):
        component=self.component;process=Mock();process.returncode=0;process.poll.return_value=0;process.wait.return_value=0
        def records():
            yield 'STUDIO_JSON:'+json.dumps(dict(type='ready',port=19090,password='synthetic-secret',network_isolated=True,binary_sha256='a'*64))+'\n'
            # Verify duplicate open uses this already active owner and does not
            # create another worker/process while the first stream is present.
            repeated=component.open(offline=True)
            self.assertTrue(repeated['studio']['active']);self.assertIn('viewer_url',repeated)
            yield 'STUDIO_JSON:{"type":"closed"}\n'
        process.stdout=records();sock=Mock();sock.__enter__=Mock(return_value=sock);sock.__exit__=Mock(return_value=False)
        sock.getsockname.return_value=('127.0.0.1',19090)
        with patch('studio_component.threading.Thread',ImmediateThread),patch.object(component,'_linux',return_value={'active':False}), \
                patch.object(component,'_command',return_value=['synthetic-never-executed']), \
                patch('studio_component.socket.socket',return_value=sock),patch('studio_component.subprocess.Popen',return_value=process) as spawn:
            result=component.open(offline=True)
        spawn.assert_called_once();self.assertFalse(result['studio']['busy']);self.assertFalse(result['studio']['active'])
        self.assertFalse(result['studio']['reserved']);self.assertIsNone(component.process);self.assertIsNone(component.token)

    def test_background_constructor_and_start_failures_release_only_new_reservation_then_retry(self):
        for reserve in (False,True):
            for kind in ('construct','start'):
                with self.subTest(reserve=reserve,kind=kind):
                    work=Mock(side_effect=lambda:self.component._update(phase='installed'))
                    with patch('studio_component.threading.Thread',**failure(kind)),self.assertRaisesRegex(RuntimeError,'unavailable'):
                        self.component._background('switching',work,reserve=reserve)
                    self.assertFalse(self.component.snapshot()['busy']);self.assertIsNone(self.component.worker)
                    self.assertFalse(lease_state(self.folder)['reserved']);self.assertIsNone(self.component.token)
                    work.assert_not_called();self.component.idle_check.assert_not_called()
                    with patch('studio_component.threading.Thread',ImmediateThread):self.component._background('switching',work,reserve=reserve)
                    work.assert_called_once();self.assertFalse(self.component.snapshot()['busy'])
                    self.component.idle_check.reset_mock()

    def test_open_constructor_and_start_failure_do_not_call_linux_and_allow_full_fake_retry(self):
        self.retry_open()
        for kind in ('construct','start'):
            with self.subTest(kind=kind),patch.object(self.component,'_linux') as linux:
                with patch('studio_component.threading.Thread',**failure(kind)),self.assertRaisesRegex(RuntimeError,'unavailable'):
                    self.component.open(offline=True)
                linux.assert_not_called();self.assertIsNone(self.component.process);self.assertIsNone(self.component.worker)
                self.assertFalse(self.component.snapshot()['busy']);self.assertFalse(lease_state(self.folder)['reserved'])
            self.retry_open()

    def test_release_failure_keeps_exact_token_and_blocks_new_reserved_operation(self):
        for entry in ('background','open'):
            with self.subTest(entry=entry),patch.object(self.component,'_linux') as linux:
                original_unlink=Path.unlink
                def denied(path,*args,**kwargs):
                    if path==self.folder/'lease.json':raise PermissionError('synthetic denied cleanup')
                    return original_unlink(path,*args,**kwargs)
                with patch.object(Path,'unlink',denied),patch('studio_component.threading.Thread',FailedThread):
                    with self.assertRaisesRegex(RuntimeError,'unavailable'):
                        if entry=='open':self.component.open(offline=True)
                        else:self.component._background('switching',Mock(),reserve=True)
                result=self.component.snapshot();token=self.component.token
                self.assertFalse(result['busy']);self.assertTrue(result['cleanup_required']);self.assertEqual(result['phase'],'cleanup_required')
                self.assertEqual(lease_state(self.folder)['token'],token);linux.assert_not_called()
                with self.assertRaisesRegex(ValueError,'cleanup is unconfirmed'):self.component.open(offline=True)
                self.assertTrue(self.component._release());self.assertFalse(lease_state(self.folder)['reserved'])

    def test_unexpected_release_exception_also_keeps_cleanup_required_and_original_marker(self):
        with patch.object(self.component,'_release',side_effect=RuntimeError('synthetic unexpected cleanup')), \
                patch('studio_component.threading.Thread',FailedThread),self.assertRaisesRegex(RuntimeError,'startup unavailable'):
            self.component.open(offline=True)
        result=self.component.snapshot();self.assertFalse(result['busy']);self.assertTrue(result['cleanup_required'])
        self.assertEqual(result['phase'],'cleanup_required');self.assertEqual(lease_state(self.folder)['token'],self.component.token)
        self.component._release()

    def test_foreign_replacement_marker_is_never_removed_by_start_failure_cleanup(self):
        replacement='b'*32
        def replaced(**_):
            row=lease_state(self.folder);row.pop('reserved');row['token']=replacement
            (self.folder/'lease.json').write_text(json.dumps(row),encoding='utf-8')
            raise RuntimeError('synthetic constructor unavailable')
        with patch('studio_component.threading.Thread',side_effect=replaced),self.assertRaises(RuntimeError):self.component.open(offline=True)
        self.assertEqual(lease_state(self.folder)['token'],replacement);self.assertNotEqual(self.component.token,replacement)
        self.assertTrue(self.component.snapshot()['cleanup_required']);self.assertFalse(self.component.snapshot()['busy'])

    def test_metadata_start_failure_does_not_release_an_earlier_unconfirmed_lease(self):
        self.component._reserve('needs_check');token=self.component.token
        self.component.state.update(cleanup_required=True,phase='cleanup_required')
        with patch('studio_component.threading.Thread',FailedThread),patch.object(self.component,'_release',wraps=self.component._release) as release:
            with self.assertRaises(RuntimeError):self.component._background('checking',Mock(),reserve=False)
        release.assert_not_called();self.assertEqual(self.component.token,token)
        self.assertEqual(lease_state(self.folder)['token'],token);self.assertTrue(self.component.snapshot()['cleanup_required'])
        self.component._release()

    def test_preflight_failure_with_cleanup_failure_does_not_publish_installed_phase(self):
        self.component.idle_check.side_effect=ValueError('synthetic busy owner')
        with patch.object(self.component,'_release',return_value=False),self.assertRaisesRegex(ValueError,'busy owner'):
            self.component.open(offline=True)
        self.assertTrue(self.component.snapshot()['cleanup_required']);self.assertEqual(self.component.snapshot()['phase'],'cleanup_required')
        self.assertEqual(lease_state(self.folder)['token'],self.component.token);self.component._release()

    def test_existing_live_viewer_and_newer_local_token_are_never_cleared(self):
        original=object();self.component.process=original
        self.component.state.update(active=True,phase='running');self.component.secret={'port':19090,'password':'synthetic-secret'}
        before=self.component.snapshot()
        with patch('studio_component.threading.Thread') as spawn,patch.object(self.component,'_release') as release:
            self.assertIn('viewer_url',self.component.open())
            with self.assertRaisesRegex(ValueError,'Finish the current'):self.component._background('checking',Mock())
        spawn.assert_not_called();release.assert_not_called();self.assertIs(self.component.process,original);self.assertEqual(self.component.snapshot(),before)
        self.component.token='b'*32;worker=object();self.component.worker=worker
        self.component._startup_failed(RuntimeError('stale failure'),'a'*32)
        self.assertIs(self.component.worker,worker);self.assertEqual(self.component.token,'b'*32);self.assertIs(self.component.process,original)

    def test_constructor_or_start_failure_then_foreign_marker_stop_never_calls_linux(self):
        for kind in ('construct','start'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                component=StudioComponent(folder);component.state['current']='2026.9.22'
                def replace():
                    row=lease_state(folder);row.pop('reserved');row['token']='b'*32
                    (Path(folder)/'lease.json').write_text(json.dumps(row),encoding='utf-8')
                    raise RuntimeError('synthetic startup unavailable')
                class ReplacedThread(ImmediateThread):
                    def start(self):replace()
                options=dict(side_effect=lambda **_:replace()) if kind=='construct' else dict(side_effect=ReplacedThread)
                with patch('studio_component.threading.Thread',**options),self.assertRaises(RuntimeError):component.open(offline=True)
                old=component.token
                with patch.object(component,'_linux') as linux,self.assertRaisesRegex(ValueError,'owner changed'):component.stop()
                linux.assert_not_called();self.assertEqual(component.token,old)
                self.assertEqual(lease_state(folder)['token'],'b'*32);self.assertTrue(component.snapshot()['cleanup_required'])

    def test_exact_original_cleanup_stop_still_confirms_and_clears_its_flag(self):
        with patch.object(self.component,'_release',return_value=False),patch('studio_component.threading.Thread',FailedThread),self.assertRaises(RuntimeError):
            self.component.open(offline=True)
        self.assertTrue(self.component.snapshot()['cleanup_required'])
        with patch.object(self.component,'_linux',return_value={'active':False}) as linux:
            result=self.component.stop()
        self.assertEqual([call.args[0] for call in linux.call_args_list],['stop','probe'])
        self.assertTrue(result['component_closed']);self.assertFalse(result['studio']['cleanup_required'])
        self.assertFalse(lease_state(self.folder)['reserved']);self.assertIsNone(self.component.token)

    def test_owner_replaced_during_stop_or_probe_is_preserved_before_next_action(self):
        for replaced_at in ('stop','probe'):
            with self.subTest(replaced_at=replaced_at),tempfile.TemporaryDirectory() as folder:
                component=StudioComponent(folder);component._reserve('needs_check')
                def linux(name,**_):
                    if name==replaced_at:
                        row=lease_state(folder);row.pop('reserved');row['token']='b'*32
                        (Path(folder)/'lease.json').write_text(json.dumps(row),encoding='utf-8')
                    return {'active':False}
                with patch.object(component,'_linux',side_effect=linux) as called,self.assertRaisesRegex(ValueError,'owner changed'):component.stop()
                self.assertEqual([call.args[0] for call in called.call_args_list],['stop'] if replaced_at=='stop' else ['stop','probe'])
                self.assertEqual(lease_state(folder)['token'],'b'*32);self.assertTrue(component.snapshot()['cleanup_required'])


if __name__=='__main__':unittest.main()
