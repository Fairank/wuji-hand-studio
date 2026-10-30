"""Continuation transactions and original glove transport, all synthetic."""
from contextlib import contextmanager,nullcontext
import copy
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch
import numpy as np  # Keep the real extension cached outside synthetic SDK patches.

from calibrated_teleoperation import CalibratedTeleoperation
from calibration_portability import CONTEXT_FIELDS
from glove_bridge import GloveBridge
from glove_protocol import CancellationNotDispatched
from glove_agent import worker_glove
from test_runtime_model_binding_0268 import Resource, WujiException
from retarget_settings import defaults
from test_calibration_portability import MODEL,bundle_context,completed


class ContinuationTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'right_hand.urdf';model.write_bytes(MODEL)
            state=completed(model);glove=dict(session_id=17,connection='connecting',busy=True)
            start=Mock(return_value=17);stop=Mock();idle=Mock();owner=Mock()
            export=Mock(side_effect=lambda proof,**kw:{**proof,**bundle_context(root/'transfer')})
            service=CalibratedTeleoperation(lambda:copy.deepcopy(state),start,stop,lambda:copy.deepcopy(glove),
                idle,export,owner_workspace='test-workspace',gate_factory=nullcontext,owner_check=owner)
            yield N(root=root,model=model,state=state,glove=glove,start=start,stop=stop,idle=idle,
                    owner=owner,export=export,service=service)

    def command(self,e):
        state=e.service.snapshot()
        return dict(run_id=state['run_id'],expected_revision=state['expected_revision'])

    def finish(self,e):
        e.service.thread.join(3);self.assertFalse(e.service.thread.is_alive())

    def test_one_explicit_request_reuses_verified_run_without_second_source_selection(self):
        with self.environment() as e:
            command=self.command(e);self.assertTrue(e.service.snapshot()['can_continue'])
            e.service.execute(command);self.finish(e)
            e.start.assert_called_once();e.stop.assert_not_called()
            bundle=e.start.call_args.args[0]
            self.assertEqual(bundle['serial'],'WG-SYNTHETIC');self.assertEqual(bundle['source_profile'],'Alice')
            state=e.service.snapshot();self.assertFalse(state['can_continue']);self.assertFalse(state['connected'])
            e.glove.update(connection='receiving',glove_serial=bundle['serial'],model_provenance=bundle,
                stream={'fresh':True,'solver':{'engine':'sdk','applied':True}})
            self.assertEqual(e.service.snapshot()['phase'],'preview')
            self.assertTrue(e.service.snapshot()['connected'])
            self.assertFalse(e.service.snapshot()['automatically_follows'])

    def test_cancelled_receiving_bridge_stays_cancelling_until_exact_session_ends(self):
        with self.environment() as e:
            e.service.execute(self.command(e));self.finish(e)
            bundle=e.start.call_args.args[0]
            e.glove.update(connection='receiving',glove_serial=bundle['serial'],model_provenance=bundle)
            before=e.service.snapshot()
            result=e.service.cancel()
            self.assertEqual(result['phase'],'cancelling');self.assertTrue(result['busy'])
            self.assertFalse(result['connected']);self.assertFalse(result['can_continue'])
            e.stop.assert_called_once_with(17)
            e.glove.update(connection='disconnected',busy=False)
            ended=e.service.snapshot()
            self.assertEqual(ended['phase'],'cancelled');self.assertFalse(ended['busy']);self.assertFalse(ended['connected'])

    def test_failed_exact_stop_remains_visible_instead_of_becoming_receiving_success(self):
        with self.environment() as e:
            e.service.execute(self.command(e));self.finish(e)
            bundle=e.start.call_args.args[0]
            e.glove.update(connection='receiving',glove_serial=bundle['serial'],model_provenance=bundle)
            before=e.service.snapshot();e.stop.side_effect=ValueError('synthetic stop failed')
            with self.assertRaisesRegex(ValueError,'stop failed'):
                e.service.cancel()
            result=e.service.snapshot()
            self.assertEqual(result['phase'],'error');self.assertTrue(result['busy']);self.assertFalse(result['connected'])
            self.assertIn('stop failed',result['cleanup_error']);self.assertFalse(result['can_continue'])

    def test_new_idle_offer_uses_new_model_hash_and_discards_old_attempt_identity(self):
        with self.environment() as e:
            e.service.execute(self.command(e));self.finish(e);e.glove.update(connection='disconnected',busy=False)
            new=e.root/'new.urdf';new.write_bytes(MODEL+b'\n')
            e.state['run'].update(id='e'*32,serial='GLOVE-B',user='Bob')
            e.state['run'].update(model_sha256=__import__('hashlib').sha256(new.read_bytes()).hexdigest(),model_bytes=new.stat().st_size)
            e.state['run']['result']['result'].update(calibrated_urdf=str(new))
            identity=dict(user_id='owner-bob',display_name='Bob')
            e.state['run']['sdk_user']=copy.deepcopy(identity)
            e.state['run']['result']['user']=copy.deepcopy(identity)
            e.state['run']['result']['result']['sdk_user']=copy.deepcopy(identity)
            result=e.service.snapshot()
            self.assertEqual(result['run_id'],'e'*32);self.assertEqual(result['serial'],'GLOVE-B');self.assertEqual(result['profile'],'Bob')
            self.assertEqual(result['model_sha256'],__import__('hashlib').sha256(new.read_bytes()).hexdigest())
            self.assertIsNone(result['attempt_id']);self.assertIsNone(result['sdk_session']);self.assertTrue(result['can_continue'])

    def test_scoped_late_cancel_after_preview_or_follow_never_stops_active_session(self):
        for following in (False,True):
            with self.subTest(following=following),self.environment() as e:
                e.service.execute(self.command(e));self.finish(e)
                bundle=e.start.call_args.args[0]
                before=e.service.snapshot()
                e.glove.update(connection='receiving',glove_serial=bundle['serial'],model_provenance=bundle,
                    hardware={'active':following},feedback={'device_id':'HAND'} if following else {})
                with self.assertRaisesRegex(ValueError,'pause and disconnect'):
                    e.service.cancel({key:before[key] for key in ('run_id','attempt_id')})
                e.stop.assert_not_called();self.assertFalse(e.service._cancel.is_set())

    def test_scoped_connecting_cancel_is_pending_only_and_does_not_hold_class_lock_for_callback(self):
        with self.environment() as e:
            e.service.execute(self.command(e));self.finish(e);before=e.service.snapshot()
            read=[]
            def glove():
                thread=threading.Thread(target=lambda:(e.service.lock.acquire(),read.append(True),e.service.lock.release()))
                thread.start();thread.join(1);self.assertFalse(thread.is_alive())
                return copy.deepcopy(e.glove)
            e.service.get_glove=glove
            result=e.service.cancel({key:before[key] for key in ('run_id','attempt_id')})
            self.assertTrue(read);self.assertFalse(result['connected']);self.assertTrue(result['busy'])
            e.stop.assert_called_once_with(17,pending_only=True)

    def test_scoped_cancel_rechecks_attempt_after_bridge_read_without_touching_replacement(self):
        with self.environment() as e:
            e.service.execute(self.command(e));self.finish(e);before=e.service.snapshot()
            def replace():
                with e.service.lock:e.service.state['attempt_id']='f'*32
                return copy.deepcopy(e.glove)
            e.service.get_glove=replace
            with self.assertRaisesRegex(ValueError,'attempt changed'):
                e.service.cancel({key:before[key] for key in ('run_id','attempt_id')})
            e.stop.assert_not_called();self.assertFalse(e.service._cancel.is_set())

    def test_destination_rejecting_completed_preview_or_follow_does_not_publish_cancellation(self):
        for following in (False,True):
            with self.subTest(following=following),self.environment() as e:
                e.service.execute(self.command(e));self.finish(e)
                bridge=GloveBridge(Mock());bridge.session=17;bridge.thread=N(is_alive=lambda:True)
                bridge.stdin=io.StringIO();bridge.state.update(connection='connecting')
                e.service.get_glove=bridge.snapshot
                before=e.service.snapshot();stored=copy.deepcopy(e.service.state)
                bundle=e.start.call_args.args[0]
                def completed_before_destination(session,**kwargs):
                    with bridge.lock:
                        bridge.state.update(connection='receiving',glove_serial=bundle['serial'],model_provenance=bundle,
                            hardware={'active':following},feedback={'device_id':'HAND'} if following else {},
                            stream={'fresh':True,'age_ms':0,'timeout_ms':250,'solver':{'engine':'sdk','applied':True}})
                        bridge.last_update=time.monotonic()
                    return bridge.disconnect_session(session,**kwargs)
                e.stop.side_effect=completed_before_destination
                with self.assertRaises(CancellationNotDispatched):
                    e.service.cancel({key:before[key] for key in ('run_id','attempt_id')})
                self.assertFalse(e.service._cancel.is_set());self.assertEqual(e.service.state,stored)
                self.assertFalse(bridge.stopping);self.assertEqual(bridge.stdin.getvalue(),'')
                result=e.service.snapshot()
                self.assertEqual(result['phase'],'preview');self.assertFalse(result['busy']);self.assertTrue(result['connected'])
                self.assertIsNone(result['error']);self.assertIsNone(result['cleanup_error'])
                self.assertEqual(bridge.state['hardware']['active'],following)
                e.stop.assert_called_once_with(17,pending_only=True)

    def test_destination_rejecting_replaced_session_does_not_publish_cancellation(self):
        with self.environment() as e:
            e.service.execute(self.command(e));self.finish(e)
            bridge=GloveBridge(Mock());bridge.session=17;bridge.thread=N(is_alive=lambda:True)
            bridge.stdin=io.StringIO();bridge.state.update(connection='connecting')
            e.service.get_glove=bridge.snapshot
            before=e.service.snapshot();stored=copy.deepcopy(e.service.state)
            def replace_before_destination(session,**kwargs):
                with bridge.lock:bridge.session=18
                return bridge.disconnect_session(session,**kwargs)
            e.stop.side_effect=replace_before_destination
            with self.assertRaises(CancellationNotDispatched):
                e.service.cancel({key:before[key] for key in ('run_id','attempt_id')})
            self.assertFalse(e.service._cancel.is_set());self.assertEqual(e.service.state,stored)
            self.assertFalse(bridge.stopping);self.assertEqual(bridge.stdin.getvalue(),'')
            self.assertEqual(e.service.snapshot()['phase'],'session_changed')

    def test_rejected_pending_cancel_never_exposes_flag_to_publishing_worker(self):
        with self.environment() as e:
            published=threading.Event();resume=threading.Event();worker_flags=[]
            original=e.service._set
            def pause_after_publication(**values):
                original(**values)
                if 'sdk_session' in values:
                    published.set();self.assertTrue(resume.wait(2));worker_flags.append(e.service._cancel.is_set())
            e.service._set=pause_after_publication
            e.service.execute(self.command(e));self.assertTrue(published.wait(2))
            before=e.service.snapshot();bundle=e.start.call_args.args[0]
            def reject_after_worker_can_observe(session,**kwargs):
                self.assertEqual((session,kwargs),(17,dict(pending_only=True)))
                e.glove.update(connection='receiving',glove_serial=bundle['serial'],model_provenance=bundle,
                    stream={'fresh':True,'solver':{'engine':'sdk','applied':True}})
                resume.set();self.finish(e)
                raise CancellationNotDispatched('synthetic completed before destination check')
            e.stop.side_effect=reject_after_worker_can_observe
            try:
                with self.assertRaises(CancellationNotDispatched):
                    e.service.cancel({key:before[key] for key in ('run_id','attempt_id')})
            finally:
                resume.set();self.finish(e)
            self.assertEqual(worker_flags,[False]);self.assertFalse(e.service._cancel.is_set())
            e.stop.assert_called_once_with(17,pending_only=True)
            result=e.service.snapshot();self.assertEqual(result['phase'],'preview')
            self.assertTrue(result['connected']);self.assertFalse(result['busy']);self.assertIsNone(result['cleanup_error'])

    def test_pending_cancel_stream_failure_stays_cancelled_and_cleanup_error_until_session_ends(self):
        with self.environment() as e:
            e.service.execute(self.command(e));self.finish(e)
            class FailedStream:
                def write(self,_):raise OSError('synthetic disconnect write failed')
                def flush(self):raise AssertionError('write did not succeed')
            bridge=GloveBridge(Mock());bridge.session=17;bridge.thread=N(is_alive=lambda:True)
            bridge.stdin=FailedStream();bridge.state.update(connection='connecting')
            e.service.get_glove=bridge.snapshot;e.stop.side_effect=bridge.disconnect_session
            before=e.service.snapshot()
            with self.assertRaisesRegex(OSError,'disconnect write failed'):
                e.service.cancel({key:before[key] for key in ('run_id','attempt_id')})
            self.assertTrue(bridge.stopping);self.assertTrue(e.service._cancel.is_set())
            result=e.service.snapshot()
            self.assertEqual(result['phase'],'error');self.assertTrue(result['busy']);self.assertFalse(result['connected'])
            self.assertIn('disconnect write failed',result['cleanup_error']);self.assertFalse(result['can_continue'])

    def test_private_task_dispatch_releases_workflow_lock_before_async_start(self):
        with self.environment() as e:
            with e.service.workflow_lock:
                e.service.execute(self.command(e))
                e.start.assert_not_called()
            self.finish(e);e.start.assert_called_once()
            self.assertIsNone(e.service.snapshot()['error'])

    def test_old_run_changed_revision_and_client_selected_identity_are_rejected(self):
        with self.environment() as e:
            command=self.command(e)
            for changed in ({**command,'run_id':'b'*32},{**command,'expected_revision':'c'*64},
                            {**command,'serial':'OTHER'},{**command,'bundle_path':'arbitrary'}):
                with self.subTest(changed=changed),self.assertRaises(ValueError):e.service.execute(changed)
            e.start.assert_not_called();e.export.assert_not_called()

    def test_changed_model_after_offered_proof_never_starts_controller(self):
        with self.environment() as e:
            command=self.command(e);e.model.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):e.service.execute(command)
            self.assertFalse(e.service.snapshot()['can_continue'])
            e.export.assert_not_called();e.start.assert_not_called()

    def test_any_busy_workspace_or_owner_mismatch_prevents_export(self):
        for which in ('idle','owner'):
            with self.subTest(which=which),self.environment() as e:
                getattr(e,which).side_effect=ValueError('foreign workspace busy')
                e.service.execute(self.command(e));self.finish(e)
                e.export.assert_not_called();e.start.assert_not_called()

    def test_cancellation_during_export_never_publishes_new_sdk_session(self):
        with self.environment() as e:
            entered=threading.Event();release=threading.Event()
            def export(proof,**kw):
                entered.set();self.assertTrue(release.wait(2));kw['cancel_check']();return proof
            e.export.side_effect=export;e.service.execute(self.command(e));self.assertTrue(entered.wait(2))
            e.service.cancel();release.set();self.finish(e)
            e.start.assert_not_called();e.stop.assert_not_called();self.assertEqual(e.service.snapshot()['phase'],'cancelled')

    def test_cancellation_during_session_publication_stops_only_new_exact_session(self):
        with self.environment() as e:
            def start(_):e.service.cancel();return 17
            e.start.side_effect=start;e.service.execute(self.command(e));self.finish(e)
            e.stop.assert_called_once_with(17)

    def test_cancel_during_final_owner_check_does_not_launch_controller(self):
        with self.environment() as e:
            entered=threading.Event();release=threading.Event()
            def owner(_):
                if e.owner.call_count==2:
                    entered.set();self.assertTrue(release.wait(2))
            e.owner.side_effect=owner
            e.service.execute(self.command(e));self.assertTrue(entered.wait(2))
            e.service.cancel();release.set();self.finish(e)
            e.export.assert_called_once();e.start.assert_not_called();e.stop.assert_not_called()
            self.assertEqual(e.service.snapshot()['phase'],'cancelled')

    def test_active_attempt_identity_survives_changed_calibration_snapshot(self):
        with self.environment() as e:
            entered=threading.Event();release=threading.Event()
            def export(proof,**kw):
                entered.set();self.assertTrue(release.wait(2));return proof
            e.export.side_effect=export
            reply=e.service.execute(self.command(e));self.assertTrue(entered.wait(2))
            try:
                e.state['run']['id']='b'*32
                state=e.service.snapshot()
                self.assertEqual(state['run_id'],'a'*32)
                self.assertEqual(state['attempt_id'],reply['attempt_id'])
                self.assertFalse(state['can_continue'])
            finally:
                e.service.cancel();release.set();self.finish(e)
            e.start.assert_not_called()

    def test_old_attempt_cancel_cannot_cancel_retry_of_the_same_saved_run(self):
        with self.environment() as e:
            sessions=iter((17,18))
            def start(_):
                session=next(sessions);e.glove.update(session_id=session,connection='connecting',busy=True)
                return session
            e.start.side_effect=start
            command=self.command(e);first=e.service.execute(command);self.finish(e)
            expected=dict(run_id=first['run_id'],attempt_id=first['attempt_id'])
            e.service.cancel(expected);e.glove.update(connection='disconnected',busy=False)
            second=e.service.execute(command);self.finish(e)
            self.assertNotEqual(second['attempt_id'],first['attempt_id'])
            self.assertFalse(e.service._cancel.is_set())
            with self.assertRaisesRegex(ValueError,'changed'):
                e.service.cancel(expected)
            self.assertFalse(e.service._cancel.is_set());e.stop.assert_called_once_with(17,pending_only=True)
            with self.assertRaisesRegex(ValueError,'changed'):
                e.service.cancel(dict(run_id='b'*32,attempt_id=second['attempt_id']))
            e.service.cancel(dict(run_id=second['run_id'],attempt_id=second['attempt_id']))
            self.assertEqual(e.stop.call_args.args,(18,))

    def test_idle_new_proof_does_not_inherit_old_attempt_error_or_cancel_identity(self):
        with self.environment() as e:
            e.owner.side_effect=ValueError('old attempt failed')
            e.service.execute(self.command(e));self.finish(e)
            self.assertEqual(e.service.snapshot()['phase'],'error')
            e.state['run']['id']='b'*32
            state=e.service.snapshot()
            self.assertEqual(state['run_id'],'b'*32);self.assertTrue(state['can_continue'])
            self.assertEqual(state['phase'],'idle');self.assertIsNone(state['error'])
            self.assertIsNone(state['attempt_id'])

    def test_late_stop_return_does_not_overwrite_retry_phase(self):
        with self.environment() as e:
            first=e.service.execute(self.command(e));self.finish(e)
            stopping=threading.Event();stopped=threading.Event();exporting=threading.Event();exported=threading.Event()
            def stop(_,**kwargs):
                self.assertEqual(kwargs,dict(pending_only=True))
                e.glove.update(connection='disconnected',busy=False)
                stopping.set();self.assertTrue(stopped.wait(2))
            e.stop.side_effect=stop
            caller=threading.Thread(target=e.service.cancel,args=(dict(run_id=first['run_id'],attempt_id=first['attempt_id']),))
            caller.start();self.assertTrue(stopping.wait(2))
            def export(proof,**kw):exporting.set();self.assertTrue(exported.wait(2));return proof
            e.export.side_effect=export
            try:
                retry=e.service.execute(self.command(e));self.assertTrue(exporting.wait(2))
                stopped.set();caller.join(2);self.assertFalse(caller.is_alive())
                self.assertEqual(e.service.state['attempt_id'],retry['attempt_id'])
                self.assertEqual(e.service.state['phase'],'exporting')
                self.assertFalse(e.service._cancel.is_set())
            finally:
                stopped.set();exported.set();caller.join(2);self.finish(e)

    def test_source_run_change_during_export_is_detected_without_device_command(self):
        with self.environment() as e:
            def export(proof,**kw):e.state['run']['id']='b'*32;return proof
            e.export.side_effect=export;e.service.execute(self.command(e));self.finish(e)
            e.start.assert_not_called();self.assertEqual(e.service.state['phase'],'error')

    def test_replaced_controller_session_cannot_be_reported_as_connected(self):
        with self.environment() as e:
            e.service.execute(self.command(e));self.finish(e)
            e.glove.update(session_id=18,connection='receiving',glove_serial='WG-SYNTHETIC')
            state=e.service.snapshot();self.assertEqual(state['phase'],'session_changed');self.assertFalse(state['connected'])


class BridgeContinuationTests(unittest.TestCase):
    def test_private_bootstrap_is_never_a_public_browser_glove_action(self):
        with tempfile.TemporaryDirectory() as folder:
            context=bundle_context(Path(folder));bridge=GloveBridge(Mock())
            with self.assertRaisesRegex(ValueError,'Use scan'):
                bridge.action(dict(name='glove_session',continuation=context))
            bridge.factory.assert_not_called()

    def test_owned_readiness_builds_only_exact_glove_open_after_binding_callback(self):
        with tempfile.TemporaryDirectory() as folder:
            context=bundle_context(Path(folder));bridge=GloveBridge(Mock());bridge.stdin=io.StringIO()
            callback=Mock(return_value=dict(timeout_ms=250,retarget=defaults(),solver={'engine':'sdk'}))
            bridge._on_model_ready=callback
            receipt={k:context[k] for k in ('source_run_id','source_workspace','source_profile','serial','side','model_sha256')}
            receipt.update(owner_user_id='owner-1',imported=True)
            bridge._calibrated_ready(dict(receipt=receipt),context)
            command=json.loads(bridge.stdin.getvalue())
            self.assertEqual(command['name'],'glove_open');self.assertEqual(command['serial'],context['serial'])
            self.assertEqual(command['user_id'],'owner-1');callback.assert_called_once_with(receipt)
            self.assertNotIn('glove_prepare',bridge.stdin.getvalue());self.assertNotIn('glove_follow',bridge.stdin.getvalue())
            bridge.stdin=io.StringIO()
            for changed in ({'serial':'OTHER'},{'model_sha256':'b'*64},{'owner_user_id':''},
                            {'owner_user_id':'same-name-different-owner'},{'source_workspace':'foreign'}):
                with self.subTest(changed=changed),self.assertRaises(ValueError):bridge._calibrated_ready(dict(receipt={**receipt,**changed}),context)
            self.assertEqual(bridge.stdin.getvalue(),'')

    def test_cancelling_before_ready_does_not_apply_binding_or_open_glove(self):
        with tempfile.TemporaryDirectory() as folder:
            context=bundle_context(Path(folder));bridge=GloveBridge(Mock());bridge.stdin=io.StringIO();bridge.stopping=True
            bridge._on_model_ready=Mock()
            receipt={k:context[k] for k in ('source_run_id','source_workspace','source_profile','serial','side','model_sha256')}
            receipt.update(owner_user_id='owner-1',imported=True)
            bridge._calibrated_ready(dict(receipt=receipt),context)
            bridge._on_model_ready.assert_not_called();self.assertEqual(bridge.stdin.getvalue(),'')

    def test_stage_reuses_only_the_same_controller_file_transport_and_hash(self):
        from local_controller import LocalFiles
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);context=bundle_context(root/'source');remote=root/'controller';remote.mkdir()
            (remote/'calibration-transfer').mkdir(mode=0o700)
            client=N(agent_directory=str(remote),open_sftp=lambda:LocalFiles(remote))
            bridge=GloveBridge(Mock());staged=bridge._stage_calibration(client,context)
            self.assertEqual(Path(staged['bundle_path']).read_bytes(),Path(context['bundle_path']).read_bytes())
            self.assertEqual(bridge._stage_calibration(client,context),staged)
            Path(staged['bundle_path']).write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'checksum'):bridge._stage_calibration(client,context)

    def test_continuation_failure_remains_visible_after_exact_worker_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge=GloveBridge(Mock());bridge._continuation=bundle_context(Path(folder))
            bridge.receive(dict(type='glove_error',message='synthetic model conflict'))
            bridge.receive(dict(type='glove_closed',hardware={}))
            self.assertEqual(bridge.snapshot()['connection'],'error')
            self.assertEqual(bridge.snapshot()['message'],'synthetic model conflict')


class WorkerContinuationTests(unittest.TestCase):
    def test_cancel_before_or_during_import_never_starts_fresh_device_scan(self):
        for during in (False,True):
            with self.subTest(during=during),tempfile.TemporaryDirectory() as folder:
                context=bundle_context(Path(folder));requests=queue.Queue();events=queue.Queue()
                manager=N(current_user=lambda:dict(user_id=''),list_users=lambda:[],scan=Mock(),connect=Mock())
                sdk=N(SdkManager=N(instance=lambda:manager))
                if not during:requests.put(dict(name='glove_disconnect'))
                def prepare(*args):
                    requests.put(dict(name='glove_disconnect'))
                    return dict(context,owner_user_id='owner-1',model_path='unused',imported=True)
                with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'), \
                        patch('calibration_portability.prepare_controller_model',side_effect=prepare) as imported, \
                        patch('sdk_session.ProfileLease',return_value=nullcontext()):
                    worker_glove(requests,events,context)
                self.assertEqual(imported.call_count,int(during))
                manager.scan.assert_not_called();manager.connect.assert_not_called()
                output=list(events.queue)
                self.assertFalse(any(e['type']=='glove_error' for e in output))
                self.assertTrue(any(e['type']=='glove_closed' for e in output))

    def test_mismatched_bootstrap_side_stops_before_import_scan_or_connect(self):
        with tempfile.TemporaryDirectory() as folder:
            context=bundle_context(Path(folder))
            manager=N(current_user=lambda:dict(user_id=''),list_users=lambda:[],scan=Mock(),connect=Mock())
            sdk=N(SdkManager=N(instance=lambda:manager));events=queue.Queue()
            with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_left'), \
                    patch('calibration_portability.prepare_controller_model') as prepare:
                worker_glove(queue.Queue(),events,context)
            prepare.assert_not_called();manager.scan.assert_not_called();manager.connect.assert_not_called()
            self.assertTrue(any(e['type']=='glove_error' for e in list(events.queue)))

    def test_existing_worker_imports_then_verifies_user_side_and_model_before_subscription(self):
        for actual in ('right','left'):
            with self.subTest(actual=actual),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);context=bundle_context(root/'source');model=root/'right_hand.urdf';model.write_bytes(MODEL)
                prior=dict(user_id='',display_name='Default',is_default=True)
                wearer=dict(user_id='owner-1',display_name='Alice',is_default=False);current=[prior];calls=[]
                def switch(user):current[0]=wearer if user=='owner-1' else prior;calls.append(('user',user))
                def subscribe():calls.append(('subscribe',current[0]['user_id']));return N(recv=lambda:None,close=lambda:None)
                runtime_model=Resource()
                glove=N(serial_number=context['serial'],hand_side=lambda:N(get=lambda:actual),
                    hand_model_path=lambda:runtime_model,hand_skeleton=lambda:N(subscribe=subscribe),
                    disconnect=lambda:calls.append(('disconnect',current[0]['user_id'])))
                manager=N(current_user=lambda:current[0],list_users=lambda:[prior,wearer],switch_user=switch,
                    scan=lambda:[N(sn=context['serial'],device_type='glove',address='fixture')],
                    connect=lambda **kwargs:(calls.append(('connect',kwargs)) or glove))
                sdk=N(WujiException=WujiException,SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='glove'),WujiGlove=type(glove),
                    HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
                    RetargetSession=N(for_hand=lambda *args,**kw:N(step=lambda _: [0.]*20)))
                requests=queue.Queue()
                class Events(queue.Queue):
                    def put(self,event):
                        super().put(event)
                        if event['type']=='calibrated_ready':
                            requests.put(dict(name='glove_open',serial=context['serial'],user_id='owner-1',timeout_ms=250))
                        if event['type']=='glove_state' and event['state']['connection']=='receiving':
                            requests.put(dict(name='disconnect'))
                events=Events()
                receipt=dict(context,owner_user_id='owner-1',model_path=str(model),imported=True)
                def prepare(*args):calls.append(('model-import',));return receipt
                with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict(os.environ,WUJI_HAND_PROFILE='hand2_right'), \
                        patch('calibration_portability.prepare_controller_model',side_effect=prepare), \
                        patch('zenoh_route.managed_wsl',return_value=False),patch('sdk_session.ProfileLease',return_value=nullcontext()):
                    worker_glove(requests,events,context)
                output=[]
                while not events.empty():output.append(events.get())
                self.assertTrue(any(e['type']=='calibrated_ready' for e in output))
                self.assertEqual(calls[0],('model-import',))
                self.assertFalse(any('hardware' in str(call) for call in calls))
                if actual=='right':
                    self.assertIn(('subscribe','owner-1'),calls)
                    self.assertTrue(any(e['type']=='glove_state' and e['state']['connection']=='receiving' for e in output))
                else:
                    self.assertNotIn(('subscribe','owner-1'),calls)
                    self.assertTrue(any(e['type']=='glove_error' for e in output))
                self.assertEqual(current[0],prior)


if __name__=='__main__':unittest.main()
