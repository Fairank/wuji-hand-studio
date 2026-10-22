"""Exact Viewer handover tests; all processes, jobs, profiles and devices fake."""
from contextlib import contextmanager,ExitStack
import copy
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as N,ModuleType
import unittest
from unittest.mock import Mock,patch

import cli_coordination as coordination
import calibration_cli
import native_cli
import official_visualization as visualization
from calibration_handover import CalibrationHandover,workflow_action
from console_server import Controller,ConsoleHTTPServer,Handler
from device_inventory import build_inventory

SN='WG_SYNTHETIC_ONLY';TOKEN='a'*32
TEST_TEMP=Path(__file__).resolve().parents[3]/'outputs/cli-workspace-0231/backend-review/test-temp'
TEST_TEMP.mkdir(parents=True,exist_ok=True)


class HandoverTests(unittest.TestCase):
    @contextmanager
    def environment(self,side='left'):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as folder,ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=folder))
            who=lambda pid:'live' if pid==os.getpid() else 'foreign'
            stack.enter_context(patch.object(coordination,'identity',side_effect=who))
            stack.enter_context(patch.object(visualization,'identity',side_effect=who))
            recover=stack.enter_context(patch('windows_process_job.recover_viz_job',return_value=True))
            stack.enter_context(patch('managed_runtime.status',return_value={'busy':False}))
            viewer=visualization.OfficialVisualization(folder)
            viewer.token=TOKEN;viewer.process=N(poll=Mock(return_value=None))
            viewer.native_job=N(close=Mock(return_value=True),terminate_and_wait=Mock(return_value=True))
            job=viewer.native_job
            viewer.thread=N(is_alive=Mock(return_value=False),join=Mock())
            relay=N(source='synthetic',close=Mock(return_value=True));viewer.transport=relay
            reported={'sn':SN,'model':'Wuji Glove'}
            if side is not None:reported['handedness']=side
            viewer.state.update(active=True,busy=False,phase='running',backend='windows_native',
                cli_version='2026.10.12',device={**reported,'handedness':'right'},reported_device=reported,
                scanned_at=1,devices=[{'sn':SN}],started_at=time.time())
            marker=dict(kind='viz',token=TOKEN,pid=os.getpid(),start='live',backend='windows_native',
                serial=SN,job_name='Local\\HandWorkbench-OfficialViz-'+TOKEN)
            coordination.atomic(Path(folder)/'session.json',marker)
            base=dict(connection='disconnected',hardware={'active':False},glove={'busy':False},
                calibration={'running':False},recording={'active':False},parameter_sync={'busy':False},
                program={'active':False},group={'active':False},unconfirmed_devices=[])
            calibration=N(lock=threading.RLock(),run={'running':False,'status':'idle'},
                preflight_guided=Mock(),start=Mock(),cancel=Mock())
            controller=N(lock=threading.RLock(),official_viz=viewer,calibration=calibration,doctor=N(snapshot=Mock(return_value={})),
                official_tools=N(snapshot=Mock(return_value={'check':{'busy':False}})),official_cli=N(),
                calibration_devices=Mock(return_value=[]))
            service=CalibrationHandover(controller);controller.calibration_handover=service
            def snapshot():
                return {**copy.deepcopy(base),'official_viz':viewer.snapshot(),
                        'calibration_connection':copy.deepcopy(service.state)}
            controller.snapshot=snapshot
            server=object.__new__(ConsoleHTTPServer);server.controller=controller
            server.configure_official_cli(startup=False)
            def start(side,serial,replace,**kwargs):
                self.assertFalse(coordination.reservation(folder))
                self.assertIsNone(viewer.native_job);self.assertIsNone(viewer.transport)
                calibration.run.update(running=True,status='starting',serial=serial,side=side,
                    id='synthetic-run',source='official_jsonl_native',interface=kwargs['interface'])
                return {'run':copy.deepcopy(calibration.run)}
            calibration.start.side_effect=start
            def cancel():
                calibration.run['status']='cancelling'
                return {'run':copy.deepcopy(calibration.run)}
            calibration.cancel.side_effect=cancel
            yield N(folder=Path(folder),viewer=viewer,job=job,relay=relay,marker=marker,base=base,
                controller=controller,calibration=calibration,service=service,server=server,recover=recover)

    def command(self,**changes):
        return {'name':'calibration_handover','serial':SN,'viewer_token':TOKEN,'interface':'guided',
                'profile':'Demo','side':'right','replace':False,**changes}

    def test_guided_handover_uses_exact_live_viewer_not_stale_advertisement(self):
        with self.environment() as e:
            result=e.service.start(self.command())
            e.calibration.preflight_guided.assert_called_once_with('Demo','left',False,create_profile=False)
            args,kw=e.calibration.start.call_args
            self.assertEqual(args,('left',SN,False))
            self.assertEqual(kw['connected']['side'],'left')
            self.assertEqual(kw['connected']['viewer_token'],TOKEN)
            self.assertEqual(kw['connected']['source'],'native_viewer')
            self.assertEqual(kw['interface'],'guided');self.assertEqual(kw['profile'],'Demo')
            self.assertFalse(result['calibration_connection']['busy'])
            self.assertEqual(result['calibration']['run']['serial'],SN)
            self.assertEqual(coordination.reservation(e.folder),{})
            self.assertEqual(e.recover.call_args.args[0],e.marker['job_name'])
            e.job.terminate_and_wait.assert_called_once();e.relay.close.assert_called_once()

    def test_terminal_unknown_reported_side_requires_explicit_side(self):
        with self.environment(side=None) as e:
            with self.assertRaisesRegex(ValueError,'hand side'):
                e.service.start(self.command(interface='terminal',side=None,profile=None))
            e.job.terminate_and_wait.assert_not_called();e.calibration.start.assert_not_called()
            e.service.start(self.command(interface='terminal',side='right',profile=None))
            e.calibration.preflight_guided.assert_not_called()
            self.assertEqual(e.calibration.start.call_args.args[0],'right')
            self.assertIsNone(e.calibration.start.call_args.kwargs['connected']['side'])

    def test_invalid_inputs_and_create_flag_never_stop_viewer(self):
        for change in ({'serial':'WG_OTHER'},{'viewer_token':'b'*32},{'viewer_token':'x'},
                       {'interface':'other'},{'replace':1},{'create_profile':'yes'},
                       {'create_profile':True,'interface':'terminal'},{'side':'Left'}):
            with self.subTest(change=change),self.environment() as e:
                with self.assertRaises(ValueError):e.service.start(self.command(**change))
                e.job.terminate_and_wait.assert_not_called();e.calibration.start.assert_not_called()

    def test_profile_default_unknown_or_overwrite_rejection_precedes_stop(self):
        for error in ('named user required','unknown user','confirm replacement','invalid model'):
            with self.subTest(error=error),self.environment() as e:
                e.calibration.preflight_guided.side_effect=ValueError(error)
                with self.assertRaisesRegex(ValueError,error):e.service.start(self.command())
                e.job.terminate_and_wait.assert_not_called();e.calibration.start.assert_not_called()
                self.assertTrue(e.service.snapshot()['connected'])

    def test_create_profile_intent_is_forwarded_without_early_mutation(self):
        with self.environment() as e:
            e.service.start(self.command(create_profile=True,profile='NewUser'))
            e.calibration.preflight_guided.assert_called_once_with('NewUser','left',False,create_profile=True)
            self.assertTrue(e.calibration.start.call_args.kwargs['create_profile'])

    def test_foreign_owner_bad_job_and_non_native_sessions_are_rejected(self):
        for changes in ({'pid':123456},{'start':'old'},{'job_name':'Local\\Other'},
                        {'serial':'WG_OTHER'},{'kind':'calibration'},{'backend':'linux'}):
            with self.subTest(changes=changes),self.environment() as e:
                coordination.atomic(e.folder/'session.json',{**e.marker,**changes})
                with self.assertRaises(ValueError):e.service.start(self.command())
                e.job.terminate_and_wait.assert_not_called();e.recover.assert_not_called()
        for changes in ({'active':False},{'phase':'starting'},{'busy':True},{'backend':'linux_controller'},
                        {'reported_device':{'sn':'WG_OTHER'}},{'reported_device':{'sn':SN,'model':'Wuji Hand 2'}}):
            with self.subTest(changes=changes),self.environment() as e:
                e.viewer.state.update(changes)
                with self.assertRaises(ValueError):e.service.start(self.command())
                e.job.terminate_and_wait.assert_not_called()

    def test_local_sdk_recording_motion_and_calibration_block_before_stop(self):
        for change in ({'connection':'connected'},{'glove':{'busy':True}}, {'hardware':{'active':True}},
                       {'recording':{'active':True}},{'group':{'active':True}},
                       {'program':{'active':True}},{'calibration':{'running':True}}):
            with self.subTest(change=change),self.environment() as e:
                e.base.update(change)
                with self.assertRaises(ValueError):e.service.start(self.command())
                e.job.terminate_and_wait.assert_not_called()

    def test_other_workspace_busy_unknown_or_viewer_is_not_whitelisted(self):
        for change in ({'connection':'connected'},{'glove':{'busy':True}},
                       {'official_viz':{'active':True,'phase':'running','session_id':'b'*32}},
                       {'calibration_connection':{'busy':True}}):
            with self.subTest(change=change),self.environment() as e:
                child={**copy.deepcopy(e.base),**change}
                e.server._fleet=N(lock=threading.RLock(),children={'child':{}},get=Mock(side_effect=lambda row,path:child if path=='/api/state' else {}))
                with self.assertRaises(ValueError):e.service.start(self.command())
                e.job.terminate_and_wait.assert_not_called()
        with self.environment() as e:
            e.server._fleet=N(lock=threading.RLock(),children={'child':{}},get=Mock(side_effect=OSError('offline')))
            with self.assertRaisesRegex(ValueError,'unavailable'):e.service.start(self.command())
            e.job.terminate_and_wait.assert_not_called()

    def test_stop_join_does_not_hold_global_gate_and_local_starts_are_blocked(self):
        with self.environment() as e:
            original=e.viewer.stop_owned;acquired=threading.Event();errors=[]
            def stop(*args):
                def reader_finally():
                    try:
                        with coordination.gate(e.folder):acquired.set()
                    except BaseException as error:errors.append(str(error))
                thread=threading.Thread(target=reader_finally);thread.start()
                self.assertTrue(acquired.wait(1));thread.join(1);self.assertEqual(errors,[])
                self.assertTrue(e.service.state['busy'])
                return original(*args)
            e.viewer.stop_owned=Mock(side_effect=stop)
            e.service.start(self.command())

    def test_unknown_job_or_relay_release_never_starts_calibration(self):
        for component in ('job','relay'):
            with self.subTest(component=component),self.environment() as e:
                getattr(e,component).close.return_value=False
                with self.assertRaises(ValueError):e.service.start(self.command())
                e.calibration.start.assert_not_called();self.assertTrue(coordination.reservation(e.folder))

    def test_other_workspace_acquisition_during_stop_is_not_terminated(self):
        with self.environment() as e:
            original=e.viewer.stop_owned;foreign={**e.marker,'kind':'calibration','token':'b'*32,'pid':123456}
            def stop(*args):
                result=original(*args);coordination.atomic(e.folder/'session.json',foreign);return result
            e.viewer.stop_owned=Mock(side_effect=stop)
            with self.assertRaisesRegex(ValueError,'Another session'):e.service.start(self.command())
            self.assertEqual(coordination.reservation(e.folder),foreign)
            self.assertEqual(e.recover.call_count,1);e.calibration.start.assert_not_called()
            self.assertTrue(e.service.state['viewer_released'])

    def test_cancel_during_stop_keeps_new_calibration_from_starting(self):
        with self.environment() as e:
            original=e.viewer.stop_owned
            def stop(*args):
                result=original(*args)
                thread=threading.Thread(target=e.service.request_cancel);thread.start();thread.join(1)
                return result
            e.viewer.stop_owned=Mock(side_effect=stop)
            with self.assertRaisesRegex(ValueError,'cancelled'):e.service.start(self.command())
            e.calibration.start.assert_not_called();self.assertEqual(coordination.reservation(e.folder),{})

    def test_start_failure_does_not_restart_viewer_or_claim_connection(self):
        with self.environment() as e:
            e.calibration.start.side_effect=ValueError('profile changed after disconnect')
            e.viewer.start=Mock()
            with self.assertRaisesRegex(ValueError,'profile changed'):e.service.start(self.command())
            e.viewer.start.assert_not_called();self.assertFalse(e.service.snapshot()['connected'])
            self.assertTrue(e.service.state['viewer_released'])

    def test_cancel_during_slow_start_cancels_exact_published_run(self):
        with self.environment() as e:
            original=e.calibration.start.side_effect
            def start(*args,**kwargs):
                self.assertTrue(callable(kwargs['cancel_check']))
                thread=threading.Thread(target=e.service.request_cancel);thread.start();thread.join(1)
                return original(*args,**kwargs)
            e.calibration.start.side_effect=start
            result=e.service.start(self.command())
            e.calibration.cancel.assert_called_once_with()
            self.assertEqual(result['calibration']['run']['id'],'synthetic-run')
            self.assertEqual(result['calibration']['run']['status'],'cancelling')
            self.assertEqual(result['calibration_connection']['phase'],'cancelling')

    def test_profile_read_needs_no_viewer_ownership_and_mutating_metadata_stays_idle(self):
        with self.environment() as e:
            e.calibration.profile_read_check()
            with self.assertRaises(ValueError):e.calibration.metadata_check()
            e.viewer.token='b'*32
            e.calibration.profile_read_check()

    def test_native_metadata_during_scan_or_foreign_calibration_only_reads_local_users(self):
        profile={'name':'Demo','is_default':False,'left_hand':{'calibrated':False},
                 'right_hand':{'calibrated':False}}
        for kind,pid in (('scan',os.getpid()),('calibration',123456)):
            with self.subTest(kind=kind),self.environment() as e:
                marker={**e.marker,'kind':kind,'pid':pid,'token':'b'*32}
                coordination.atomic(e.folder/'session.json',marker)
                e.viewer.state.update(active=False,busy=False,phase='idle',reported_device=None)
                e.viewer.token=None
                e.viewer.connection_record=Mock(side_effect=AssertionError('Local profiles require no Viewer'))
                service=calibration_cli.CalibrationCLI(reports=e.folder/'reports')
                e.controller.calibration=service;e.server.configure_official_cli(startup=False)
                def query(*args):
                    if args==('user','list'):return {'users':[copy.deepcopy(profile)]}
                    if args==('user','show'):return copy.deepcopy(profile)
                    self.fail('Local metadata issued an unexpected command: '+repr(args))
                with patch.object(calibration_cli,'load_config',return_value={'mode':'wsl','cli':'wuji'}), \
                        patch('official_device_discovery.use_native',return_value=True), \
                        patch.object(native_cli,'prepare',return_value={'version':'2026.10.12'}), \
                        patch.object(native_cli,'json_command',side_effect=query) as commands, \
                        patch('windows_process_job.OwnedProcess') as process:
                    with patch.object(coordination,'gate',side_effect=AssertionError('Local profiles need no global gate')):
                        state=service.snapshot(refresh=True)
                    self.assertTrue(state['metadata_current']);self.assertTrue(state['available'])
                    self.assertIsNone(state['metadata_error']);self.assertEqual(state['current'],profile)
                    self.assertEqual(state['users'],[profile])
                    for operation in ('create','switch'):
                        with self.assertRaisesRegex(ValueError,'Official CLI session active'):
                            service.profile(operation,'Demo')
                    connected=dict(source='native_viewer',serial=SN,owner_pid=os.getpid(),owner_start='live',
                        viewer_token=TOKEN,checked_at=time.time(),side='left')
                    with self.assertRaisesRegex(ValueError,'Official CLI session active'):
                        service.start('left',SN,interface='guided',profile='Demo',connected=connected)
                    self.assertEqual([call.args for call in commands.call_args_list],[('user','list'),('user','show')])
                    process.assert_not_called();e.viewer.connection_record.assert_not_called()
                    e.job.terminate_and_wait.assert_not_called()
                    self.assertEqual(coordination.reservation(e.folder),marker)

    def test_local_profile_read_still_waits_for_software_configuration(self):
        with self.environment() as e:
            for change,message in (('runtime','installation'),('closing','closing'),('tools','software checks')):
                with self.subTest(change=change):
                    e.controller.desktop_closing=change=='closing'
                    e.controller.official_tools.snapshot.return_value={'check':{'busy':change=='tools'}}
                    with patch('managed_runtime.status',return_value={'busy':change=='runtime'}):
                        with self.assertRaisesRegex(ValueError,message):e.calibration.profile_read_check()

    def test_controller_transition_guard_rejects_start_but_leaves_stops_callable(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as folder:
            controller=Controller(reports=Path(folder)/'reports')
            held=threading.Event();release=threading.Event()
            def hold():
                with controller.calibration_handover.workflow_lock:held.set();release.wait(2)
            thread=threading.Thread(target=hold);thread.start();self.assertTrue(held.wait(1))
            try:
                with patch.object(controller,'_workflow_action',return_value={'ok':True}) as action:
                    with self.assertRaisesRegex(ValueError,'transition'):controller.action({'name':'connect'})
                    action.assert_not_called()
                    self.assertEqual(controller.action({'name':'calibration_cancel'}),{'ok':True})
            finally:release.set();thread.join(1)

    def test_inventory_task_ownership_never_invents_feedback_freshness(self):
        row=build_inventory(controller={'calibration':{'running':True,'serial':SN,'side':'left','source':'official_jsonl_native'}},now=100)['devices'][0]
        self.assertTrue(row['in_use']);self.assertTrue(row['stale'])
        self.assertFalse(row['identity_verified']);self.assertFalse(row['official_available']);self.assertFalse(row['selectable'])
        self.assertIsNone(row['last_seen']);self.assertEqual(row['source'],'official_calibration_session')
        self.assertEqual(build_inventory(controller={'calibration':{'running':False,'serial':SN}},now=100)['devices'],[])

    def test_only_start_and_config_actions_use_transition_lock(self):
        for name in ('connect','official_viz_start','device_inventory_scan','glove_open','calibration_start','program_start'):
            self.assertTrue(workflow_action({'name':name}),name)
        for name in ('disconnect','calibration_cancel','official_viz_stop','calibration_terminal_input','glove_keepalive','view_camera'):
            self.assertFalse(workflow_action({'name':name}),name)

    def test_connection_and_guide_get_routes_are_passive_and_bounded(self):
        handler=object.__new__(Handler);handler.allowed_host=Mock(return_value=True);handler.reply=Mock()
        snapshot=Mock(return_value={'connected':False,'phase':'disconnected'})
        handler.server=N(controller=N(calibration_handover=N(snapshot=snapshot)))
        handler.path='/api/calibration-connection';handler.do_GET()
        handler.reply.assert_called_once_with(200,{'connected':False,'phase':'disconnected'})
        helper=ModuleType('official_calibration_guides');helper.read_guide=Mock(return_value=(b'synthetic-png','image/png'))
        with patch.dict('sys.modules',{'official_calibration_guides':helper}):
            handler.reply.reset_mock();handler.path='/api/calibration-guide?step=1';handler.do_GET()
            helper.read_guide.assert_called_once_with(1)
            handler.reply.assert_called_once_with(200,b'synthetic-png','image/png')
            helper.read_guide.reset_mock()
            for query in ('step=1&step=2','step=../1','step=-1','step=123',''):
                handler.reply.reset_mock();handler.path='/api/calibration-guide?'+query;handler.do_GET()
                self.assertEqual(handler.reply.call_args.args[0],503)
            helper.read_guide.assert_not_called()
            helper.read_guide.side_effect=OSError('offline reference')
            handler.reply.reset_mock();handler.path='/api/calibration-guide?step=2';handler.do_GET()
            self.assertEqual(handler.reply.call_args.args[0],503)

    def test_state_summary_distinguishes_native_cleanup_and_legacy_history(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as folder:
            controller=Controller(reports=Path(folder)/'reports')
            controller.official_cli.snapshot=Mock(return_value={})
            for source,uncertain in [('official_tty',True),('official_jsonl_native',True),('legacy_jsonl',False)]:
                controller.calibration.run.update(id='synthetic',serial=SN,running=False,source=source,
                    status='error',phase='capture',interface='guided',cleanup_confirmed=False)
                state=controller.snapshot()['calibration']
                self.assertEqual(state['cleanup_required'],uncertain)
                self.assertEqual(state['serial'],SN);self.assertEqual(state['interface'],'guided')
            controller.calibration.run['cleanup_confirmed']=True
            self.assertFalse(controller.snapshot()['calibration']['cleanup_required'])


if __name__=='__main__':unittest.main()
