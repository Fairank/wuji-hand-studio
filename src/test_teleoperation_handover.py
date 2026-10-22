"""Viewer to SDK-session handover, with fake processes and zero device calls."""
from contextlib import contextmanager
import copy
import io
import os
import queue
import sys
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

import cli_coordination as coordination
import test_calibration_handover as fixtures
from glove_bridge import GloveBridge
from glove_agent import prepare_permission,paired_hand_worker
from retarget_workspace import RetargetWorkspace
from retarget_settings import defaults as output_defaults
from teleoperation_handover import TeleoperationHandover


class TeleoperationTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        # Reuse exact Viewer/Job fixtures, not their unittest suite.
        with fixtures.HandoverTests().environment() as e:
            e.controller.state={'device_profile':{'id':'hand2_left','generation':'hand2','side':'left'}}
            glove=GloveBridge(Mock(side_effect=AssertionError('No real controller may start')))
            e.controller.glove=glove
            service=TeleoperationHandover(e.controller);e.controller.teleoperation_handover=service
            original=e.controller.snapshot
            e.controller.snapshot=lambda:{**original(),'teleoperation_connection':copy.deepcopy(service.state)}
            e.server.configure_official_cli(startup=False)
            def action(command):
                self.assertEqual(command,{'name':'glove_scan'})
                self.assertFalse(coordination.reservation(e.folder))
                self.assertIsNone(e.viewer.native_job);self.assertIsNone(e.viewer.transport)
                with glove.lock:
                    glove.session+=1;glove.thread=N(is_alive=lambda:True)
                    glove.state.update(connection='ready',users=[{'user_id':'linux-user'}])
            e.controller.action=Mock(side_effect=action)
            with patch('bridge_config.load_config',return_value={'mode':'wsl'}), \
                    patch('managed_runtime.status',return_value={'busy':False,'ready':True}):
                yield N(**vars(e),glove=glove,teleoperation=service)

    def command(self,**extra):
        return dict(name='teleoperation_handover',serial=fixtures.SN,viewer_token=fixtures.TOKEN,**extra)

    def test_explicit_handover_only_starts_original_scan_without_glove_or_hand_open(self):
        with self.environment() as e:
            result=e.teleoperation.start(self.command())
            e.controller.action.assert_called_once_with({'name':'glove_scan'})
            self.assertEqual(result['glove']['connection'],'ready')
            self.assertEqual(e.teleoperation.snapshot()['phase'],'select_user')
            self.assertEqual(result['teleoperation_connection']['runtime']['sdk_profiles'],'controller_local')
            self.assertFalse(result['teleoperation_connection']['runtime']['windows_profiles_migrated'])
            self.assertTrue(result['teleoperation_connection']['viewer_released'])
            e.job.terminate_and_wait.assert_called_once();e.relay.close.assert_called_once()
            e.glove.factory.assert_not_called()

    def test_unknown_side_or_wrong_target_blocks_before_releasing_viewer(self):
        for side in (None,'right'):
            with self.subTest(side=side),self.environment() as e:
                e.viewer.state['reported_device']['handedness']=side
                with self.assertRaisesRegex(ValueError,'Glove side'):e.teleoperation.start(self.command())
                e.job.terminate_and_wait.assert_not_called();e.controller.action.assert_not_called()

    def test_missing_or_busy_runtime_blocks_before_releasing_viewer(self):
        for state in ({'busy':True,'ready':True},{'busy':False,'ready':False}):
            with self.subTest(state=state),self.environment() as e,patch('managed_runtime.status',return_value=state):
                with self.assertRaises(ValueError):e.teleoperation.start(self.command())
                e.job.terminate_and_wait.assert_not_called();e.controller.action.assert_not_called()

    def test_foreign_or_changed_viewer_identity_is_never_stopped(self):
        for changes in ({'pid':123456},{'token':'b'*32},{'serial':'WG_OTHER'},{'job_name':'Local\\Other'}):
            with self.subTest(changes=changes),self.environment() as e:
                coordination.atomic(e.folder/'session.json',{**e.marker,**changes})
                with self.assertRaises(ValueError):e.teleoperation.start(self.command())
                e.job.terminate_and_wait.assert_not_called();e.controller.action.assert_not_called()

    def test_local_busy_and_foreign_workspace_or_unknown_cleanup_remain_blocked(self):
        for change in ({'connection':'connected'},{'glove':{'busy':True}},
                       {'hardware':{'active':None}},{'calibration':{'cleanup_required':True}},
                       {'teleoperation_connection':{'busy':True}}):
            with self.subTest(change=change),self.environment() as e:
                remote={**copy.deepcopy(e.base),**change}
                e.server._fleet=N(lock=threading.RLock(),children={'other':{}},
                    get=lambda row,path:remote if path=='/api/state' else {})
                with self.assertRaises(ValueError):e.teleoperation.start(self.command())
                e.job.terminate_and_wait.assert_not_called();e.controller.action.assert_not_called()

    def test_file_gate_is_released_during_viewer_join_and_other_starts_share_lock(self):
        with self.environment() as e:
            original=e.viewer.stop_owned;acquired=threading.Event();local_blocked=threading.Event()
            def stop(*args):
                def other():
                    with coordination.gate(e.folder):acquired.set()
                    if not e.controller.calibration_handover.workflow_lock.acquire(blocking=False):local_blocked.set()
                thread=threading.Thread(target=other);thread.start();thread.join(1)
                self.assertFalse(thread.is_alive());self.assertTrue(acquired.is_set());self.assertTrue(local_blocked.is_set())
                return original(*args)
            e.viewer.stop_owned=stop;e.teleoperation.start(self.command())

    def test_failed_job_or_relay_cleanup_never_starts_sdk_session(self):
        for component in ('job','relay'):
            with self.subTest(component=component),self.environment() as e:
                getattr(e,component).close.return_value=False
                with self.assertRaises(ValueError):e.teleoperation.start(self.command())
                e.controller.action.assert_not_called()

    def test_other_session_acquired_during_stop_is_not_stopped_or_replaced(self):
        with self.environment() as e:
            original=e.viewer.stop_owned;foreign={**e.marker,'token':'b'*32,'pid':123456,'kind':'calibration'}
            def stop(*args):
                value=original(*args);coordination.atomic(e.folder/'session.json',foreign);return value
            e.viewer.stop_owned=stop
            with self.assertRaisesRegex(ValueError,'Another session'):e.teleoperation.start(self.command())
            self.assertEqual(coordination.reservation(e.folder),foreign);e.controller.action.assert_not_called()

    def test_cancellation_during_viewer_stop_prevents_controller_start(self):
        with self.environment() as e:
            original=e.viewer.stop_owned
            def stop(*args):
                thread=threading.Thread(target=e.teleoperation.request_cancel);thread.start();thread.join(1)
                return original(*args)
            e.viewer.stop_owned=stop
            with self.assertRaisesRegex(ValueError,'cancelled'):e.teleoperation.start(self.command())
            e.controller.action.assert_not_called();self.assertTrue(e.teleoperation.state['viewer_released'])

    def test_cancellation_during_scan_publication_closes_only_exact_new_session(self):
        with self.environment() as e:
            original=e.controller.action.side_effect
            def start(command):
                thread=threading.Thread(target=e.teleoperation.request_cancel);thread.start();thread.join(1)
                return original(command)
            e.controller.action.side_effect=start;e.glove.stdin=io.StringIO()
            e.teleoperation.start(self.command())
            self.assertEqual(e.glove.stdin.getvalue(),'{"name": "glove_disconnect"}\n')
            self.assertEqual(e.teleoperation.snapshot()['phase'],'cancelling')
            session=e.teleoperation.state['sdk_session'];e.glove.session=session+1
            with self.assertRaisesRegex(ValueError,'session changed'):e.teleoperation.cancel()
            self.assertEqual(e.glove.stdin.getvalue().count('glove_disconnect'),1)

    def test_failed_controller_start_does_not_restart_viewer_or_guess_user(self):
        with self.environment() as e:
            e.controller.action.side_effect=ValueError('controller unavailable');e.viewer.start=Mock()
            with self.assertRaisesRegex(ValueError,'controller unavailable'):e.teleoperation.start(self.command())
            self.assertTrue(e.teleoperation.state['viewer_released']);e.viewer.start.assert_not_called()
            self.assertIsNone(e.teleoperation.state['sdk_session'])

    def test_glove_only_mapping_needs_no_target_hand_serial(self):
        with tempfile.TemporaryDirectory(dir=fixtures.TEST_TEMP) as folder:
            workspace=RetargetWorkspace(folder);profile={'generation':'hand2','side':'right'}
            result=workspace.open_glove({'serial':fixtures.SN,'user_id':'linux-user'},profile,{})
            self.assertEqual(result['binding']['hand_serial'],'')
            self.assertEqual(result['binding']['glove_serial'],fixtures.SN)
            self.assertEqual(result['binding']['sdk_user'],'linux-user')

    def test_invalid_extra_control_flags_never_release_viewer(self):
        for extra in ({'user_id':'WindowsName'},{'follow':True},{'serial':''},{'viewer_token':'x'}):
            with self.subTest(extra=extra),self.environment() as e:
                request=self.command();request.update(extra)
                with self.assertRaises(ValueError):e.teleoperation.start(request)
                e.job.terminate_and_wait.assert_not_called();e.controller.action.assert_not_called()

    def test_prepare_retry_requires_actual_thread_exit_and_cleanup_confirmation(self):
        alive=N(is_alive=lambda:True);dead=N(is_alive=lambda:False)
        for thread,state,allowed in ((None,{},True),(alive,{'cleanup_confirmed':True},False),
                (dead,{},False),(dead,{'cleanup_confirmed':False},False),
                (dead,{'cleanup_confirmed':True,'hardware':{'active':None}},False),
                (dead,{'cleanup_confirmed':True,'hardware':{'active':True}},False),
                (dead,{'cleanup_confirmed':True,'hardware':{'active':False,'stop_confirmed':True}},True),
                (dead,{'cleanup_confirmed':True},True)):
            with self.subTest(state=state,alive=thread is alive):
                self.assertEqual(prepare_permission(thread,state)[0],allowed)

    def test_pre_driver_failure_requires_successful_resource_release_before_retry(self):
        for fail in (False,True):
            with self.subTest(fail=fail):
                close=Mock(side_effect=OSError('uncertain disconnect') if fail else None)
                hand=N(serial_number='HAND',disconnect=close);route=N(close=Mock())
                events=queue.Queue()
                sdk=N(SdkManager=N(instance=lambda:N()))
                with patch.dict(sys.modules,wuji_sdk=sdk),patch('device_discovery.connect_discovered',
                        return_value=(hand,route,{'id':'hand2_right'})):
                    paired_hand_worker('','HAND',{'id':'hand2_left','generation':'hand2'},queue.Queue(),events,Mock())
                values=list(events.queue)
                self.assertEqual(values[-1],{'type':'hand_session_closed','cleanup_confirmed':not fail})
                close.assert_called_once();route.close.assert_called_once()

    def test_closed_ack_is_not_trusted_if_delegated_worker_raises_afterward(self):
        events=queue.Queue();hand=N(serial_number='HAND',disconnect=Mock());route=N(close=Mock())
        def delegated(address,requests,forward,*args,**kwargs):
            forward.put({'type':'closed','hardware':{'active':False,'stop_confirmed':True}})
            raise ValueError('cleanup failed after ack')
        with patch.dict(sys.modules,wuji_sdk=N(SdkManager=N(instance=lambda:N()))), \
                patch('device_discovery.connect_discovered',return_value=(hand,route,{'id':'hand2_left'})), \
                patch('console_agent.worker',side_effect=delegated),patch('glove_agent.sys.platform','win32'):
            paired_hand_worker('','HAND',{'id':'hand2_left','generation':'hand2'},queue.Queue(),events,Mock())
        self.assertEqual(list(events.queue)[-1],{'type':'hand_session_closed','cleanup_confirmed':False})

    def test_bridge_prepare_uses_authoritative_permission_and_session_epoch(self):
        glove=GloveBridge(Mock());glove.thread=N(is_alive=lambda:True);glove.stdin=io.StringIO()
        glove.state.update(can_prepare=True,stream={'fresh':True,'age_ms':0,'timeout_ms':250})
        glove.last_update=__import__('time').monotonic()
        self.assertEqual(glove.snapshot()['session_id'],glove.session)
        glove.action({'name':'glove_prepare','serial':'HAND','address':''})
        self.assertIn('glove_prepare',glove.stdin.getvalue())
        glove.state['can_prepare']=False;glove.state['prepare_blocked_reason']='old owner cleanup pending'
        with self.assertRaisesRegex(ValueError,'old owner'):glove.action({'name':'glove_prepare','serial':'HAND','address':''})

    def test_fresh_glove_only_preview_can_bind_target_but_not_replace_source(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory(dir=fixtures.TEST_TEMP) as folder,patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=folder):
            controller=Controller(factory=Mock(),reports=Path(folder)/'reports')
            profile=controller.state['device_profile']
            binding={**{k:profile[k] for k in ('generation','side')},'glove_serial':fixtures.SN,'sdk_user':'linux-user','hand_serial':''}
            controller.retarget.select(binding,profile)
            preview={'connection':'receiving','glove_serial':fixtures.SN,'user':{'user_id':'linux-user'},
                'stream':{'fresh':True,'retarget':output_defaults(),'solver':{'engine':'sdk','applied':True}},'hardware':{},'feedback':{}}
            controller.glove.thread=N(is_alive=lambda:True)
            with patch.object(controller.glove,'snapshot',return_value=preview),patch('studio_component.enforce_session_owner'):
                result=controller.action({'name':'retarget_binding_save','binding':{**binding,'hand_serial':'HAND'}})
                self.assertEqual(result['mapping']['binding']['hand_serial'],'HAND')
                for changes in ({'glove_serial':'WG_OTHER'},{'sdk_user':'WindowsName'},{'side':'other'}):
                    with self.subTest(changes=changes),self.assertRaises(ValueError):
                        controller.action({'name':'retarget_binding_save','binding':{**binding,**changes}})
                for bad in ({'stream':{'fresh':False}},{'feedback':{'device_id':'HAND'}},{'hardware':{'active':False}}):
                    with self.subTest(bad=bad),patch.object(controller.glove,'snapshot',return_value={**preview,**bad}),self.assertRaises(ValueError):
                        controller.action({'name':'retarget_binding_save','binding':{**binding,'hand_serial':'OTHER'}})

    def test_target_with_different_saved_output_or_solver_is_rejected_atomically(self):
        from console_server import Controller
        for difference in ('output','engine','solver_parameters'):
            with self.subTest(difference=difference),tempfile.TemporaryDirectory(dir=fixtures.TEST_TEMP) as folder, \
                    patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=folder):
                controller=Controller(factory=Mock(),reports=Path(folder)/'reports');profile=controller.state['device_profile']
                binding={**{k:profile[k] for k in ('generation','side')},'glove_serial':fixtures.SN,'sdk_user':'linux-user','hand_serial':''}
                target={**binding,'hand_serial':'HAND'};controller.retarget.select(binding,profile)
                solver={'engine':'sdk','applied':True}
                if difference=='output':
                    values=output_defaults();values['gain'][0]=.5
                    controller.retarget.library.save(target,values,0)
                elif difference=='engine':controller.tuning.select_engine(target,'official_open')
                else:
                    controller.tuning.select_engine(target,'official_open');solver={'engine':'official_open','applied':True,'values_sha256':'wrong-values'}
                preview={'connection':'receiving','glove_serial':fixtures.SN,'user':{'user_id':'linux-user'},
                    'stream':{'fresh':True,'retarget':output_defaults(),'solver':solver},'hardware':{},'feedback':{}}
                controller.glove.thread=N(is_alive=lambda:True);before=controller.retarget.path.read_bytes()
                with patch.object(controller.glove,'snapshot',return_value=preview),patch.object(controller.glove,'action') as action, \
                        self.assertRaisesRegex(ValueError,'different mapping'):
                    controller.action({'name':'retarget_binding_save','binding':target})
                self.assertEqual(controller.retarget.path.read_bytes(),before);action.assert_not_called()

    def test_target_and_feedback_selection_only_changes_display_state(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory(dir=fixtures.TEST_TEMP) as folder,patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=folder):
            controller=Controller(factory=Mock(),reports=Path(folder)/'reports')
            with patch.object(controller.glove,'action') as glove:
                # Customer UI now uses one automatic source, without a picker.
                self.assertEqual(controller.state['teleoperation_view_mode'],'automatic')
                for mode in ('feedback','target'):
                    result=controller.action({'name':'teleoperation_view_mode','mode':mode})
                    self.assertEqual(result['teleoperation_view_mode'],mode)
                with self.assertRaises(ValueError):controller.action({'name':'teleoperation_view_mode','mode':'auto'})
                glove.assert_not_called();controller.factory.assert_not_called()

    def test_public_controller_action_and_fleet_callback_reach_only_original_scan(self):
        from console_server import Controller,ConsoleHTTPServer
        with self.environment() as e:
            controller=Controller(factory=Mock(),reports=e.folder/'actual-controller-reports')
            controller.official_viz=e.viewer;controller.state['device_profile']={'id':'hand2_left','generation':'hand2','side':'left'}
            controller.official_cli.snapshot=Mock(return_value={})
            controller.studio.snapshot=Mock(return_value={})
            controller.official_tools.snapshot=Mock(return_value={'check':{'busy':False}})
            server=object.__new__(ConsoleHTTPServer);server.controller=controller;server.configure_official_cli(startup=False)
            def scan(command):
                self.assertEqual(command,{'name':'glove_scan'})
                with controller.glove.lock:
                    controller.glove.session+=1;controller.glove.thread=N(is_alive=lambda:True)
                    controller.glove.state.update(connection='ready',users=[{'user_id':'linux-user'}])
            with patch.object(controller.glove,'action',side_effect=scan) as action, \
                    patch('studio_component.enforce_session_owner'),patch.object(controller.player,'command') as player:
                result=controller.action(self.command())
            action.assert_called_once_with({'name':'glove_scan'});controller.factory.assert_not_called()
            self.assertEqual(result['teleoperation_connection']['phase'],'select_user')
            self.assertEqual(controller.state['view_source'],'glove')
            self.assertTrue(all(call.args[0].get('name')=='demo_stop' for call in player.call_args_list))

    def test_public_handover_checks_real_fleet_closure_without_whitelisting_foreign_viewers(self):
        from console_server import Controller,ConsoleHTTPServer
        cases=(None,{'official_viz':{'active':True,'phase':'running','session_id':'b'*32}},
               {'official_viz':{'scanning':True,'phase':'idle'}},{'glove':{'busy':True}},
               {'teleoperation_connection':{'busy':True}}, {'calibration':{'cleanup_required':True}},'unavailable')
        for change in cases:
            with self.subTest(change=change),self.environment() as e:
                controller=Controller(factory=Mock(),reports=e.folder/'fleet-controller-reports')
                controller.official_viz=e.viewer;controller.state['device_profile']={'id':'hand2_left','generation':'hand2','side':'left'}
                controller.official_cli.snapshot=Mock(return_value={});controller.studio.snapshot=Mock(return_value={})
                controller.official_tools.snapshot=Mock(return_value={'check':{'busy':False}})
                remote={**copy.deepcopy(e.base),'official_viz':{'active':False,'phase':'idle','reserved':True}}
                if isinstance(change,dict):remote.update(change)
                get=Mock(side_effect=OSError('unavailable') if change=='unavailable' else lambda row,path:remote if path=='/api/state' else {})
                server=object.__new__(ConsoleHTTPServer);server.controller=controller
                server._fleet=N(lock=threading.RLock(),children={'foreign':{'id':'foreign'}},get=get)
                server.configure_official_cli(startup=False)
                def scan(command):
                    self.assertEqual(command,{'name':'glove_scan'})
                    with controller.glove.lock:
                        controller.glove.session+=1;controller.glove.thread=N(is_alive=lambda:True)
                        controller.glove.state.update(connection='ready')
                with patch.object(controller.glove,'action',side_effect=scan) as action,patch('studio_component.enforce_session_owner'):
                    if change is None:
                        controller.action(self.command());action.assert_called_once_with({'name':'glove_scan'})
                        self.assertTrue(any(call.args[1]=='/api/doctor' for call in get.call_args_list))
                    else:
                        with self.assertRaises(ValueError):controller.action(self.command())
                        action.assert_not_called();e.job.terminate_and_wait.assert_not_called()
                self.assertTrue(get.called);controller.factory.assert_not_called()

    def test_follow_requires_current_binding_and_exact_published_sdk_identity(self):
        from console_server import Controller
        for sdk_user in ('linux-user',''):
            with self.subTest(user=sdk_user),tempfile.TemporaryDirectory(dir=fixtures.TEST_TEMP) as folder, \
                    patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=folder):
                controller=Controller(factory=Mock(),reports=Path(folder)/'reports');profile=controller.state['device_profile']
                binding={**{k:profile[k] for k in ('generation','side')},'glove_serial':fixtures.SN,'sdk_user':sdk_user,'hand_serial':'HAND'}
                controller.retarget.select(binding,profile);controller.glove.thread=N(is_alive=lambda:True)
                actual={'connection':'receiving','profile':profile['id'],'glove_serial':fixtures.SN,'user':{'user_id':sdk_user},
                    'feedback':{'device_id':'HAND','identified_device':{'source':'sdk_identity','serial':'HAND','profile':profile['id']}}}
                changes=({'feedback':{'device_id':'OTHER','identified_device':actual['feedback']['identified_device']}},
                    {'feedback':{'device_id':'HAND','identified_device':{'source':'sdk_identity','serial':'OTHER','profile':profile['id']}}},
                    {'feedback':{'device_id':'HAND','identified_device':{'source':'advertisement','serial':'HAND','profile':profile['id']}}},
                    {'feedback':{'device_id':'HAND','identified_device':{'source':'sdk_identity','serial':'HAND','profile':'hand1_left'}}},
                    {'feedback':{'device_id':'HAND'}},{'profile':'hand1_left'},{'glove_serial':'WG_OTHER'},
                    {'user':{'user_id':'WindowsName'}},{'user':{}},{'connection':'ready'})
                with patch.object(controller.glove,'snapshot',return_value=actual),patch.object(controller.glove,'action') as action:
                    controller.action({'name':'glove_follow','workspace_clear':True})
                    action.assert_called_once_with({'name':'glove_follow','workspace_clear':True})
                    for change in changes:
                        with self.subTest(change=change),patch.object(controller.glove,'snapshot',return_value={**actual,**change}),self.assertRaisesRegex(ValueError,'Actual teleoperation'):
                            controller.action({'name':'glove_follow','workspace_clear':True})
                    self.assertEqual(action.call_count,1)
                    controller.glove.stopping=True
                    with self.assertRaises(ValueError):controller.action({'name':'glove_follow','workspace_clear':True})
                    self.assertEqual(action.call_count,1);controller.factory.assert_not_called()

    def test_prepared_hand_identity_is_published_after_verification_and_ownership_before_driver(self):
        for generation in ('hand1','hand2'):
            for failure in (None,'sdk','profile','ownership'):
                with self.subTest(generation=generation,failure=failure):
                    profile=generation+'_left';selected={'id':profile,'generation':generation,'side':'left'}
                    hand=N(serial_number='ACTUAL_HAND',disconnect=Mock());route=N(close=Mock())
                    ownership=N(close=Mock());events=queue.Queue();factory=Mock()
                    verified={**selected,'id':generation+'_right'} if failure=='profile' else selected
                    identity={'type':'identity','profile':profile,'device_id':'ACTUAL_HAND'}
                    def driver(address,requests,forward,*args,**kwargs):
                        self.assertEqual(list(events.queue),[identity])
                        owner.assert_called_once_with('ACTUAL_HAND')
                        ownership.close.assert_not_called()
                        if generation=='hand1':self.assertIs(kwargs['prepared_hand'],hand)
                        else:self.assertEqual(kwargs['prepared'],(hand,route))
                        forward.put({'type':'closed','hardware':{'active':False,'stop_confirmed':True}})
                    with patch.dict(sys.modules,wuji_sdk=N(SdkManager=N(instance=lambda:N()))), \
                            patch('glove_agent.sys.platform','linux'), \
                            patch('device_discovery.connect_discovered',return_value=(hand,route,verified),
                                  side_effect=ValueError('SDK identity failed') if failure=='sdk' else None) as connect, \
                            patch('device_ownership.DeviceOwnership',return_value=ownership,
                                  side_effect=ValueError('Ownership busy') if failure=='ownership' else None) as owner, \
                            patch('first_generation.worker_first',side_effect=driver) as first, \
                            patch('console_agent.worker',side_effect=driver) as second:
                        paired_hand_worker('endpoint','SELECTED_HAND',selected,queue.Queue(),events,factory)
                    connect.assert_called_once();factory.assert_not_called()
                    values=list(events.queue)
                    if failure is None:
                        self.assertEqual(values[0],identity)
                        (first if generation=='hand1' else second).assert_called_once()
                        (second if generation=='hand1' else first).assert_not_called()
                        ownership.close.assert_called_once()
                        self.assertEqual(values[-1],{'type':'hand_session_closed','cleanup_confirmed':True})
                    else:
                        self.assertFalse(any(event['type']=='identity' for event in values))
                        first.assert_not_called();second.assert_not_called()
                        if failure in ('sdk','profile'):owner.assert_not_called()


if __name__=='__main__':unittest.main()
