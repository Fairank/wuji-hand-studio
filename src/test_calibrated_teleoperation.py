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
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from calibrated_teleoperation import CalibratedTeleoperation
from calibration_portability import CONTEXT_FIELDS
from glove_bridge import GloveBridge
from glove_agent import worker_glove
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
            e.glove.update(connection='receiving',glove_serial=bundle['serial'],model_provenance=bundle)
            self.assertEqual(e.service.snapshot()['phase'],'preview')
            self.assertTrue(e.service.snapshot()['connected'])
            self.assertFalse(e.service.snapshot()['automatically_follows'])

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
            for changed in ({'serial':'OTHER'},{'model_sha256':'b'*64},{'owner_user_id':''},{'source_workspace':'foreign'}):
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
                glove=N(serial_number=context['serial'],hand_side=lambda:N(get=lambda:actual),
                    hand_model_path=lambda:N(get=lambda:''),hand_skeleton=lambda:N(subscribe=subscribe),
                    disconnect=lambda:calls.append(('disconnect',current[0]['user_id'])))
                manager=N(current_user=lambda:current[0],list_users=lambda:[prior,wearer],switch_user=switch,
                    scan=lambda:[N(sn=context['serial'],device_type='glove',address='fixture')],
                    connect=lambda **kwargs:(calls.append(('connect',kwargs)) or glove))
                sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='glove'),WujiGlove=type(glove),
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
