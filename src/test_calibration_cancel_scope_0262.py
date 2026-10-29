"""Actual Controller/REST/Native cancellation ownership; no vendor processes."""
from contextlib import contextmanager
import copy,io,json,threading,time
from pathlib import Path
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch
import test_calibration_backup_ledger_0262 as ledger
import test_calibration_guided as fixture
from console_server import Handler,ORIGIN
import calibration_handover as handover
import native_calibration as native_module
import cli_coordination

REAL_THREAD=threading.Thread
A='a'*32;B='b'*32

class CancelScopeTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with ledger.LedgerTests().environment() as e,patch('calibration_cli.load_config',return_value={'mode':'local'}),patch('official_device_discovery.use_native',return_value=True),patch('studio_component.enforce_session_owner'):
            original=e.command
            def command(*args):
                if args==('user','list'):return {'users':[copy.deepcopy(e.profile)]}
                if args==('user','show',fixture.USER):return copy.deepcopy(e.profile)
                return original(*args)
            e.query.side_effect=command;e.jobs=[]
            def spawn(*a,**kw):
                process=fixture.FakeProcess([]);job=N(process=process,close=Mock(return_value=True),terminate_and_wait=Mock(return_value=True))
                e.jobs.append(job);return job
            e.spawn.side_effect=spawn
            c=e.controller;c.connection_switch.release_for_calibration=Mock();c.connection_switch.request_cancel=Mock()
            c.calibration.device_provider=lambda fresh:[]
            c.calibration_handover.idle_check=lambda:None;c.calibration_handover.request_cancel=Mock()
            c.calibration_devices=Mock(return_value=[{'sn':fixture.SN,'model':'Wuji Glove 1','handedness':'left'}])
            yield e

    def post(self,e,command):
        raw=json.dumps(command).encode();c=e.controller
        h=N(path='/api/action',server=N(controller=c),rfile=io.BytesIO(raw),wfile=io.BytesIO(),
            headers={'Origin':ORIGIN,'X-Console-Token':c.csrf,'Content-Length':str(len(raw)),'Content-Type':'application/json'},allowed_host=lambda:True,_task_view_headers=lambda:None)
        h.reply=Mock();Handler.do_POST(h);return h.reply.call_args.args

    def begin(self,e,request_id):
        return self.post(e,dict(name='calibration_begin',request_id=request_id,serial=fixture.SN,side='left',profile=fixture.USER,replace=True,create_profile=False,interface='guided'))

    def no_global_cancel(self,e):
        e.controller.connection_switch.request_cancel.assert_not_called();e.controller.calibration_handover.request_cancel.assert_not_called()

    def test_cancel_before_begin_blocks_only_that_request_and_old_request_never_cancels_new_run(self):
        with self.environment() as e:
            self.assertEqual(self.post(e,dict(name='calibration_cancel',request_id=A))[0],200)
            self.assertEqual(self.begin(e,A)[0],400);e.spawn.assert_not_called();e.controller.calibration_devices.assert_not_called()
            self.assertEqual(self.begin(e,B)[0],200);run=copy.deepcopy(e.controller.calibration.run);job=e.jobs[-1]
            state=copy.deepcopy(e.controller.calibration_handover.state)
            self.assertEqual(self.post(e,dict(name='calibration_cancel',request_id=A))[0],200)
            self.assertEqual(self.begin(e,A)[0],400)
            self.assertEqual(e.controller.calibration.run,run);self.assertEqual(e.controller.calibration_handover.state,state)
            self.assertEqual(job.process.stdin.getvalue(),'');job.close.assert_not_called();self.no_global_cancel(e)
            self.assertEqual(self.post(e,dict(name='calibration_cancel',request_id=B))[0],200)
            self.assertEqual(json.loads(job.process.stdin.getvalue()),{'type':'cancel'})

    def test_wrong_run_and_bad_shapes_are_zero_target_effects_correct_run_writes_once(self):
        with self.environment() as e:
            self.assertEqual(self.begin(e,B)[0],200);run=e.controller.calibration.run;run_id=run['id'];job=e.jobs[-1]
            for command in [dict(name='calibration_cancel',expected_run=A),dict(name='calibration_cancel',expected_run=None),dict(name='calibration_cancel',request_id='bad'),dict(name='calibration_cancel',request_id=B,expected_run=run_id)]:
                self.assertEqual(self.post(e,command)[0],400)
                self.assertEqual(job.process.stdin.getvalue(),'');job.close.assert_not_called()
            self.assertEqual(self.post(e,dict(name='calibration_cancel',expected_run=run_id))[0],200)
            self.assertEqual(json.loads(job.process.stdin.getvalue()),{'type':'cancel'});self.no_global_cancel(e)

    def test_native_final_run_check_rejects_replacement_after_CLI_check(self):
        with self.environment() as e:
            self.assertEqual(self.begin(e,B)[0],200);c=e.controller;run_id=c.calibration.run['id'];native=c.calibration.native
            original=native.cancel
            def replace(**kwargs):
                with c.calibration.lock:c.calibration.run=dict(c.calibration.run,id=A)
                return original(**kwargs)
            with patch.object(native,'cancel',side_effect=replace):self.assertEqual(self.post(e,dict(name='calibration_cancel',expected_run=run_id))[0],400)
            self.assertEqual(c.calibration.run['id'],A);self.assertEqual(e.jobs[-1].process.stdin.getvalue(),'');e.jobs[-1].close.assert_not_called();self.no_global_cancel(e)

    def test_pending_discovery_cancel_never_waits_for_gate_or_cancels_other_workflow(self):
        with self.environment() as e:
            entered=threading.Event();leave=threading.Event();rows=e.controller.calibration_devices.return_value;reply=[]
            def slow(**kwargs):entered.set();leave.wait(3);return rows
            e.controller.calibration_devices.side_effect=slow
            worker=REAL_THREAD(target=lambda:reply.append(self.begin(e,A)));worker.start()
            try:
                self.assertTrue(entered.wait(2));start=time.monotonic()
                status,body=self.post(e,dict(name='calibration_cancel',request_id=A))
                self.assertEqual(status,200);self.assertLess(time.monotonic()-start,.5)
                self.assertEqual(body['cancelled_request_id'],A);e.spawn.assert_not_called();self.no_global_cancel(e)
            finally:leave.set();worker.join(4)
            self.assertFalse(worker.is_alive());self.assertEqual(reply[0][0],400);e.spawn.assert_not_called()
            e.controller.calibration_devices.side_effect=None;self.assertEqual(self.begin(e,B)[0],200)

    def test_cancel_at_publication_targets_exact_new_run(self):
        with self.environment() as e:
            original=e.controller.calibration.start
            def published(*a,**kw):
                result=original(*a,**kw)
                self.assertEqual(self.post(e,dict(name='calibration_cancel',request_id=B))[0],200)
                return result
            with patch.object(e.controller.calibration,'start',side_effect=published):self.assertEqual(self.begin(e,B)[0],200)
            job=e.jobs[-1];self.assertEqual(json.loads(job.process.stdin.getvalue()),{'type':'cancel'})
            run=e.controller.calibration.run;self.assertEqual(e.controller.calibration_handover.state['run_id'],run['id']);self.no_global_cancel(e)

    def test_restore_cancel_is_run_scoped_and_old_request_never_targets_it(self):
        with self.environment() as e:
            self.assertEqual(self.post(e,dict(name='calibration_backup_restore',backup_id=e.saved['id'],expected_sha256=e.saved['sha256']))[0],200)
            c=e.controller;run_id=c.calibration.run['id'];job=e.jobs[-1]
            self.assertEqual(c.calibration.run['source'],'official_user_import_tty')
            self.assertEqual(self.post(e,dict(name='calibration_cancel',request_id=A))[0],200)
            self.assertEqual(self.post(e,dict(name='calibration_cancel',expected_run=B))[0],400)
            self.assertEqual(job.process.stdin.getvalue(),'');job.close.assert_not_called();self.no_global_cancel(e)
            self.assertEqual(self.post(e,dict(name='calibration_cancel',expected_run=run_id))[0],200)
            self.assertEqual(json.loads(job.process.stdin.getvalue()),{'type':'cancel'})

    def test_scoped_cleanup_of_never_spawned_partial_marker_never_clears_another_run(self):
        with self.environment() as e:
            c=e.controller
            with patch.object(native_module,'atomic',side_effect=OSError('synthetic marker write denied')),patch.object(native_module,'release',side_effect=PermissionError('synthetic retained reservation')):
                with self.assertRaises(OSError):c.calibration.restore_backup(e.saved['id'],e.saved['sha256'])
            run_id=c.calibration.run['id'];marker=cli_coordination.reservation(e.folder)
            self.assertNotIn('run_id',marker);e.spawn.assert_not_called()
            self.assertEqual(self.post(e,dict(name='calibration_cancel',expected_run=A))[0],400)
            self.assertEqual(cli_coordination.reservation(e.folder),marker)
            self.assertEqual(self.post(e,dict(name='calibration_cancel',expected_run=run_id))[0],200)
            self.assertFalse(cli_coordination.reservation(e.folder));self.assertTrue(c.calibration.run['cleanup_confirmed']);e.spawn.assert_not_called()

    def test_tombstones_are_bounded_and_do_not_reject_new_intent_at_short_system_uptime(self):
        with self.environment() as e:
            service=e.controller.calibration_handover
            with patch.object(handover.time,'monotonic',return_value=100):
                for i in range(300):service.cancel_request(format(i,'032x'))
                self.assertLessEqual(len(service._cancelled_requests),256)
                self.assertEqual(self.begin(e,B)[0],200)

    def test_consumed_begin_nonce_is_not_replayed_after_run_finished(self):
        with self.environment() as e:
            self.assertEqual(self.begin(e,B)[0],200);c=e.controller;job=e.jobs[-1];old_id=c.calibration.run['id']
            job.process.stdout=io.StringIO(json.dumps({'type':'ready','serial':fixture.SN,'side':'left'})+'\n'+json.dumps({'type':'exit','exitstatus':9})+'\n')
            c.calibration.native._collect();self.assertFalse(c.calibration.run['running'])
            before=copy.deepcopy(c.calibration_handover.state);count=len(e.jobs)
            self.assertEqual(self.begin(e,B)[0],400);self.assertEqual(len(e.jobs),count);self.assertEqual(c.calibration_handover.state,before)
            self.assertEqual(self.begin(e,'c'*32)[0],200);current=c.calibration.run['id'];self.assertNotEqual(old_id,current)
            self.assertEqual(self.post(e,dict(name='calibration_cancel',request_id=B))[0],200)
            self.assertEqual(e.jobs[-1].process.stdin.getvalue(),'');self.assertEqual(c.calibration.run['id'],current)

    def test_old_published_request_ack_retires_without_touching_new_restore_even_at_final_check(self):
        for race in (False,True):
            with self.subTest(race=race),self.environment() as e:
                self.assertEqual(self.begin(e,B)[0],200);c=e.controller;old_native=c.calibration.native;old_id=c.calibration.run['id']
                def replace_with_restore():
                    e.jobs[0].process.stdout=io.StringIO(json.dumps({'type':'ready','serial':fixture.SN,'side':'left'})+'\n'+json.dumps({'type':'exit','exitstatus':9})+'\n')
                    old_native._collect()
                    self.assertEqual(self.post(e,dict(name='calibration_backup_restore',backup_id=e.saved['id'],expected_sha256=e.saved['sha256']))[0],200)
                if race:
                    original=old_native.cancel
                    def changed(**kwargs):replace_with_restore();return original(**kwargs)
                    with patch.object(old_native,'cancel',side_effect=changed):status,body=self.post(e,dict(name='calibration_cancel',request_id=B))
                else:
                    replace_with_restore();status,body=self.post(e,dict(name='calibration_cancel',request_id=B))
                self.assertEqual(status,200);self.assertEqual(body['cancelled_request_id'],B)
                self.assertNotEqual(c.calibration.run['id'],old_id);self.assertEqual(c.calibration.run['source'],'official_user_import_tty')
                self.assertEqual(e.jobs[-1].process.stdin.getvalue(),'');e.jobs[-1].close.assert_not_called();self.no_global_cancel(e)

if __name__=='__main__':unittest.main()
