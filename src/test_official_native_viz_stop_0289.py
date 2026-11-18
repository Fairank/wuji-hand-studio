"""Exact native Viewer cancellation reuses the existing private-console worker."""
import io,json,os,sys
from pathlib import Path
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch
import cli_coordination as coordination
import official_visualization as viz
import test_official_native_viz as fixtures


class NativeViewerStopTests(unittest.TestCase):
    def test_stop_sends_existing_worker_cancel_before_wait_and_never_forces_clean_exit(self):
        case=fixtures.NativeVisualizationTests()
        with case.fixture() as (value,job,_,_,_,_,_):
            case.marker(value);value.native_job=job;value.process=job.process
            value.state.update(backend='windows_native',active=True,phase='running')
            alive=[True]
            def join(timeout=None):
                self.assertTrue(job.process.stdin.getvalue(),'Stop must request the existing worker cancellation first')
                self.assertEqual(json.loads(job.process.stdin.getvalue()),{'type':'cancel'})
                job.terminate_and_wait.assert_not_called()
                alive[0]=False;job.process.returncode=0
                value.process=None;value.native_job=None;value.state.update(active=False,busy=False,phase='idle',native_release='graceful')
                coordination.release(value.token,value.folder);value.token=None
            value.thread=N(is_alive=lambda:alive[0],join=join)
            value.stop()
            job.terminate_and_wait.assert_not_called();self.assertEqual(value.snapshot()['native_release'],'graceful')

    def test_worker_exit_receipt_required_and_protocol_failure_never_claims_graceful(self):
        case=fixtures.NativeVisualizationTests()
        for records in ([{'type':'exit','exitstatus':0}],[],[{'type':'error','message':'Ctrl+C exit unconfirmed'}],
                        [{'type':'exit','exitstatus':0},{'type':'output','text':'late'}]):
            with self.subTest(records=records),case.fixture() as (value,job,factory,_,_,_,_):
                case.marker(value);value.native_stop.set();value.state.update(phase='stopping')
                # Let this synthetic worker be registered, then request Stop.
                value.native_stop.clear()
                job.active_processes=Mock(return_value=0)
                job.process.stdout=iter(json.dumps(row)+'\n' for row in records)
                value._run_native(fixtures.SERIAL,'light')
                expected='graceful' if len(records)==1 and records[0]['type']=='exit' else 'unconfirmed'
                self.assertEqual(value.snapshot().get('native_release'),expected)
                if expected!='graceful':self.assertEqual(value.snapshot()['phase'],'error')
                argv=factory.call_args.args[0]
                self.assertIn('--native-viz-worker',argv)

    def test_worker_entry_reuses_exact_owned_job_and_original_jsonl_command(self):
        worker=getattr(viz,'native_worker_main',None)
        self.assertTrue(callable(worker),'Viewer needs the existing PrivateConsole worker entry')
        token='a'*32;serial=fixtures.SERIAL
        marker=dict(kind='viz',token=token,backend='windows_native',job_name='Local\\HandWorkbench-OfficialViz-'+token,
                    serial=serial,pid=os.getpid(),start='SYNTHETIC-START',cli_version=fixtures.COMPONENT['version'])
        with (patch('native_calibration._attach_pipes'),patch('windows_process_job.current_in_named_job',return_value=True),
              patch.object(viz,'gate',return_value=__import__('contextlib').nullcontext()),
              patch.object(viz,'reservation',return_value=marker),patch.object(viz,'identity',return_value='SYNTHETIC-START'),
              patch('native_cli.prepare',return_value=fixtures.COMPONENT),patch('native_jsonl_worker.run',return_value=0) as run):
            self.assertEqual(worker([token,serial]),0)
            run.assert_called_once_with([fixtures.COMPONENT['binary'],'viz','--sn',serial,'--no-zero','--no-open','--jsonl'])

    def test_worker_rejects_changed_owner_or_missing_containment_before_cli_launch(self):
        worker=getattr(viz,'native_worker_main',None);self.assertTrue(callable(worker))
        for contained in (False,True):
            with self.subTest(contained=contained),patch('native_calibration._attach_pipes'),\
                    patch('windows_process_job.current_in_named_job',return_value=contained),\
                    patch.object(viz,'gate',return_value=__import__('contextlib').nullcontext()),\
                    patch.object(viz,'reservation',return_value={}),patch('native_cli.prepare') as prepare,\
                    patch('native_jsonl_worker.run') as run,patch('builtins.print'):
                self.assertEqual(worker(['a'*32,fixtures.SERIAL]),1)
                prepare.assert_not_called();run.assert_not_called()

    def test_late_old_worker_exit_never_clears_replacement_owner_or_handles(self):
        case=fixtures.NativeVisualizationTests()
        with case.fixture() as (value,job,_,_,_,_,_):
            case.marker(value);replacement=fixtures.FakeProcess();replacement_job=fixtures.FakeJob(replacement)
            def lines():
                value.token='b'*32;value.process=replacement;value.native_job=replacement_job
                marker=coordination.reservation(value.folder);marker['token']=value.token
                marker['job_name']='Local\\HandWorkbench-OfficialViz-'+value.token
                coordination.atomic(value.folder/'session.json',marker)
                value.state.update(phase='running',active=True)
                yield json.dumps(dict(type='exit',exitstatus=0))+'\n'
            job.process.stdout=lines()
            value._run_native(fixtures.SERIAL,'light')
            self.assertEqual(value.token,'b'*32);self.assertIs(value.process,replacement)
            self.assertIs(value.native_job,replacement_job);self.assertTrue(value.state['active'])
            replacement_job.close.assert_not_called()


if __name__=='__main__':unittest.main()
