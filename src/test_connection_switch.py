"""Stateful switch transactions; transports, SDK and devices are isolated."""
from contextlib import contextmanager
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

import cli_coordination as coordination
from connection_switch import ConnectionSwitch,owner,task_id
from device_profiles import profile
from task_sessions import context_digest
from task_view import execute,scope

ROOT=Path(__file__).resolve().parents[3]/'outputs/customer-flow-0242/backend-temp'
ROOT.mkdir(parents=True,exist_ok=True)
SN='WG_SYNTH_A';OTHER='WG_SYNTH_B';HAND='WH_SYNTH_A';USER='actual-linux-uuid'


class SwitchTests(unittest.TestCase):
    @contextmanager
    def environment(self,mode='teleoperation'):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder,patch('bridge_config.load_config',return_value={'mode':'wsl'}), \
                patch('official_device_discovery.use_native',return_value=True), \
                patch('native_cli.resolve',return_value={'binary':'SYNTHETIC-verified-native-cli','version':'2026.10.12'}):
            c=N(lock=threading.RLock(),calibration_handover=N(workflow_lock=threading.RLock()),
                teleoperation_handover=N(_runtime=Mock()),factory=Mock(side_effect=AssertionError('No actual transport')),
                session_epochs={1:1})
            state=dict(connection='disconnected',hardware={'active':False},session_generation=1,
                device_profile=profile('hand2_left'),device_id=None,identified_device=None,
                unconfirmed_devices=[],retarget_binding=dict(generation='hand2',side='left',glove_serial=SN,sdk_user=USER,hand_serial=HAND),
                program={'active':False},group={'active':False},calibration={},calibration_connection={},
                teleoperation_connection={},calibrated_teleoperation={},maintenance={},official_studio={})
            gs=dict(busy=False,connection='disconnected',session_id=4,users=[dict(user_id=USER)],hardware={},feedback={})
            vs={}
            c.state=state;c.glove=N(snapshot=lambda:copy.deepcopy(gs))
            c.official_viz=N(folder=Path(folder),snapshot=lambda:copy.deepcopy(vs))
            def native(sn):
                vs.update(backend='windows_native',active=True,busy=False,phase='running',session_id='a'*32,
                    reported_device=dict(sn=sn,handedness='left'),device=dict(sn=sn,device_type='wuji_glove',handedness='left'))
                coordination.atomic(Path(folder)/'session.json',dict(kind='viz',token='a'*32))
            def sdk(sn=SN,user=USER):
                gs.update(busy=True,connection='receiving',glove_serial=sn,profile='hand2_left',
                    user={'user_id':user},stream={'fresh':True},can_prepare=True,
                    session_cleanup={'error':None,'cleanup_confirmed':False})
            if mode=='teleoperation':sdk()
            elif mode=='official':native(SN)
            elif mode=='hand':state.update(connection='connected',device_id=HAND,identified_device=dict(source='sdk_identity',serial=HAND,profile='hand2_left'))
            c.program=N(snapshot=lambda:copy.deepcopy(state['program']),stop=Mock(side_effect=lambda:state['program'].update(active=False)))
            service=ConnectionSwitch(c);c.connection_switch=service
            gs['controller_config']=service._config()
            service.catalog.update(status='ready',checked_at=__import__('time').time(),users=[dict(user_id=USER)])
            service.catalog_config=service._config()
            def snapshot():
                value={**copy.deepcopy(state),'glove':copy.deepcopy(gs),'official_viz':copy.deepcopy(vs)}
                value['connection_switch']=service.snapshot(value)
                return value
            c.snapshot=snapshot
            rows=[dict(sn=sn,kind='glove',side='left',generation='glove1',stale=False) for sn in (SN,OTHER)]
            rows.append(dict(sn=HAND,kind='hand',side='left',generation='hand2',stale=False))
            c.device_inventory=Mock(side_effect=lambda:dict(devices=copy.deepcopy(rows),workspace_errors=[]))
            calls=[]
            def action(command):
                calls.append(copy.deepcopy(command));name=command['name']
                if name=='connection_switch':return service.start(command)
                if name=='glove_scan':gs.update(busy=True,connection='ready',session_id=gs['session_id']+1,
                    glove_serial=None,devices=[dict(sn=SN),dict(sn=OTHER)],users=[dict(user_id=USER)],session_cleanup={})
                elif name=='glove_open':sdk(command['serial'],command['user_id'])
                elif name=='official_viz_start':native(command['serial'])
                elif name=='device_profile_select':state['device_profile']=profile(command['profile'])
                elif name=='connect':state.update(connection='connected',device_id=command['serial'],
                    identified_device=dict(serial=command['serial'],source='sdk_identity',profile=state['device_profile']['id']))
                elif name in ('glove_stop','hardware_stop'):
                    (gs['hardware'] if name=='glove_stop' else state['hardware']).update(active=False,stop_confirmed=True)
                    state['unconfirmed_devices']=[]
                else:raise AssertionError('Unexpected or motor-start action: '+name)
            c.action=Mock(side_effect=action)
            def stop_sdk(session,**expected):
                self.assertEqual(session,gs['session_id'])
                for key,field in (('expected_serial','glove_serial'),('expected_profile','profile')):
                    if key in expected:self.assertEqual(expected[key],gs.get(field))
                if 'expected_user' in expected:self.assertEqual(expected['expected_user'],gs['user']['user_id'])
                self.assertIs(gs['hardware'].get('active',False),False)
                gs.update(busy=False,connection='disconnected',session_cleanup={'cleanup_confirmed':True})
                return {'cleanup_confirmed':True}
            c.glove.stop_session_confirmed=Mock(side_effect=stop_sdk)
            def disconnect_pending(session,**kw):
                self.assertEqual(session,e_session:=gs['session_id'])
                gs.update(busy=False,connection='disconnected',session_cleanup={'cleanup_confirmed':True})
            c.glove.disconnect_session=Mock(side_effect=disconnect_pending)
            def record(sn,token,**_):
                self.assertEqual((sn,token),(vs['reported_device']['sn'],vs['session_id']))
                return dict(serial=sn,viewer_token=token,side='left',source='native_viewer')
            def stop_native(sn,token,**kw):
                record(sn,token);vs.clear();vs.update(phase='idle',active=False,busy=False);(Path(folder)/'session.json').unlink()
            c.official_viz.connection_record=Mock(side_effect=record)
            c.official_viz.stop_owned=Mock(side_effect=stop_native)
            c.official_viz.discover_inline=Mock(return_value=[dict(sn=SN)])
            def stop_hand(generation,sn):
                self.assertEqual((generation,sn),(state['session_generation'],state['device_id']))
                state.update(connection='disconnected',device_id=None,identified_device=None,session_generation=generation+1)
            c.stop_feedback_confirmed=Mock(side_effect=stop_hand)
            service.idle_check=Mock();service.preflight_check=Mock()
            yield N(c=c,service=service,state=state,gs=gs,vs=vs,rows=rows,calls=calls,sdk=sdk,native=native,folder=Path(folder))

    def command(self,e,mode='official',sn=SN,**target):
        return dict(name='connection_switch',request_id='b'*32,expected_owner=context_digest(e.c.snapshot()),
                    target=dict(mode=mode,serial=sn,**target))

    def test_sdk_to_cli_and_back_preserve_exact_wearer_profile_binding_without_motion(self):
        with self.environment() as e:
            binding=copy.deepcopy(e.state['retarget_binding'])
            first=e.service.start(self.command(e))
            self.assertEqual(first['connection_switch']['phase'],'connected')
            self.assertFalse(first['connection_switch']['busy'])
            e.c.glove.stop_session_confirmed.assert_called_once_with(4,expected_serial=SN,expected_user=USER,expected_profile='hand2_left')
            self.assertEqual(e.service.resume,dict(serial=SN,user_id=USER,profile='hand2_left',binding=binding))
            cmd=self.command(e,'teleoperation');cmd['request_id']='c'*32
            result=e.service.start(cmd)
            self.assertEqual(e.gs['glove_serial'],SN);self.assertEqual(e.gs['user']['user_id'],USER)
            self.assertEqual(e.state['retarget_binding'],binding)
            self.assertEqual(result['connection_switch_receipt']['request_id'],'c'*32)
            self.assertEqual([x['name'] for x in e.calls],['official_viz_start','glove_scan','glove_open'])

    def test_new_device_and_hand_switch_connect_only_explicit_target(self):
        for mode,sn,target in [('teleoperation',OTHER,{'user_id':USER}),('hand',HAND,{})]:
            with self.subTest(mode=mode),self.environment() as e:
                result=e.service.start(self.command(e,mode,sn,**target))
                self.assertEqual(result['connection_switch_receipt']['target']['serial'],sn)
                self.assertEqual(owner(e.c.snapshot())['serial'],sn)
                self.assertFalse(any(x['name'] in ('glove_follow','hardware_start','glove_prepare') for x in e.calls))

    def test_same_target_is_idempotent_without_release_or_rescan(self):
        for mode in ('teleoperation','official','hand'):
            with self.subTest(mode=mode),self.environment(mode) as e:
                result=e.service.start(self.command(e,mode,HAND if mode=='hand' else SN))
                self.assertTrue(result['connection_switch']['unchanged'])
                e.c.glove.stop_session_confirmed.assert_not_called();e.c.official_viz.stop_owned.assert_not_called()
                e.c.action.assert_not_called()

    def test_missing_user_or_unknown_target_keeps_native_viewer(self):
        with self.environment('official') as e:
            for cmd in (self.command(e,'teleoperation'),self.command(e,'teleoperation','WG_MISSING',user_id=USER)):
                with self.assertRaises(ValueError):e.service.start(cmd)
            e.c.official_viz.stop_owned.assert_not_called();e.c.action.assert_not_called()

    def test_catalog_uuid_not_display_name_and_explicit_empty_default_are_distinct(self):
        for user in ('',USER):
            with self.subTest(user=user),self.environment('official') as e:
                e.gs['users']=[]
                e.service.catalog.update(status='ready',checked_at=__import__('time').time(),users=[dict(user_id=user)])
                e.service.catalog_config=e.service._config()
                original=e.c.action.side_effect
                def action(c):
                    original(c)
                    if c['name']=='glove_scan':e.gs['users']=[dict(user_id=user)]
                e.c.action.side_effect=action
                e.service.start(self.command(e,'teleoperation',user_id=user))
                self.assertEqual(e.gs['user']['user_id'],user)
        with self.environment('official') as e:
            with self.assertRaises(ValueError):e.service.start(self.command(e,'teleoperation',user_id='Display Name'))
            e.c.official_viz.stop_owned.assert_not_called()

    def test_stale_owner_conflict_unknown_or_foreign_target_never_releases(self):
        cases=('stale_cas','conflict','foreign','unknown_stop','cleanup','workspace_error')
        for case in cases:
            with self.subTest(case=case),self.environment() as e:
                cmd=self.command(e)
                if case=='stale_cas':e.gs['session_id']+=1
                elif case=='conflict':e.rows[0]['conflict']=True
                elif case=='foreign':e.rows[0]['workspace_owners']=[{'id':'other'}]
                elif case=='unknown_stop':e.gs['hardware']={'active':None}
                elif case=='cleanup':e.gs['session_cleanup']={'error':'disconnect failed'}
                else:e.c.device_inventory.return_value={'devices':e.rows,'workspace_errors':[{}]};e.c.device_inventory.side_effect=None
                with self.assertRaises(ValueError):e.service.start(cmd)
                e.c.glove.stop_session_confirmed.assert_not_called();e.c.action.assert_not_called()

    def test_exact_active_follow_and_playlist_are_stopped_before_release(self):
        for mode in ('teleoperation','hand'):
            with self.subTest(mode=mode),self.environment(mode) as e:
                hw=e.gs['hardware'] if mode=='teleoperation' else e.state['hardware']
                hw.update(active=True,stop_confirmed=False)
                if mode=='teleoperation':e.gs['feedback']={'device_id':HAND}
                e.state['program']['active']=mode=='hand'
                result=e.service.start(self.command(e,'official'))
                self.assertEqual(e.calls[0]['name'],'glove_stop' if mode=='teleoperation' else 'hardware_stop')
                self.assertEqual(result['connection_switch']['phase'],'connected')
                if mode=='hand':e.c.program.stop.assert_called_once()

    def test_unconfirmed_stop_keeps_original_owner_and_never_opens_new(self):
        with self.environment() as e:
            e.gs.update(hardware={'active':True},feedback={'device_id':HAND})
            now=[0.];e.service.clock=lambda:now[0];e.service.sleep=lambda _:now.__setitem__(0,now[0]+7.)
            e.c.action.side_effect=lambda c:e.calls.append(c)
            with self.assertRaises(ValueError):e.service.start(self.command(e))
            self.assertEqual(e.gs['connection'],'receiving');e.c.glove.stop_session_confirmed.assert_not_called()
            self.assertEqual([x['name'] for x in e.calls],['glove_stop'])

    def test_unknown_cleanup_is_sticky_and_failed_new_sdk_is_not_retried(self):
        with self.environment() as e:
            def failed(*a,**kw):
                e.gs.update(busy=False,connection='error',session_cleanup={'error':'missing ACK'})
                raise ValueError('missing ACK')
            e.c.glove.stop_session_confirmed.side_effect=failed
            with self.assertRaisesRegex(ValueError,'missing ACK'):e.service.start(self.command(e))
            e.c.action.assert_not_called()
            cmd=self.command(e);cmd['request_id']='d'*32
            with self.assertRaises(ValueError):e.service.start(cmd)
            self.assertEqual(e.c.glove.stop_session_confirmed.call_count,1)
        with self.environment('official') as e:
            original=e.c.action.side_effect
            def fail(c):
                original(c)
                if c['name']=='glove_open':e.gs.update(connection='error',message='exact glove unavailable')
            e.c.action.side_effect=fail
            with self.assertRaisesRegex(ValueError,'exact glove unavailable'):e.service.start(self.command(e,'teleoperation',user_id=USER))
            self.assertEqual(e.gs['connection'],'disconnected')
            self.assertEqual(sum(c['name']=='glove_open' for c in e.calls),1)

    def test_gate_released_during_join_but_workflow_lock_blocks_other_starts(self):
        with self.environment() as e:
            original=e.c.glove.stop_session_confirmed.side_effect;checks=[]
            def stop(*a,**kw):
                def other():
                    with coordination.gate(e.folder):checks.append('gate')
                    if not e.service.workflow_lock.acquire(blocking=False):checks.append('workflow')
                t=threading.Thread(target=other);t.start();t.join(1)
                self.assertFalse(t.is_alive());return original(*a,**kw)
            e.c.glove.stop_session_confirmed.side_effect=stop
            e.service.start(self.command(e));self.assertEqual(checks,['gate','workflow'])

    def test_race_cancel_or_new_foreign_marker_after_release_never_connects(self):
        for race in ('cancel','foreign'):
            with self.subTest(race=race),self.environment() as e:
                original=e.c.glove.stop_session_confirmed.side_effect
                def stop(*a,**kw):
                    value=original(*a,**kw)
                    if race=='cancel':e.service.cancelled=True
                    else:coordination.atomic(e.folder/'session.json',{'kind':'calibration','token':'f'*32})
                    return value
                e.c.glove.stop_session_confirmed.side_effect=stop
                with self.assertRaises(ValueError):e.service.start(self.command(e))
                e.c.action.assert_not_called()

    def test_scan_age_and_transport_rate_do_not_change_owner_cas(self):
        with self.environment() as e:
            cmd=self.command(e)
            e.gs.update(devices_at=123,stream={'fresh':True,'retarget_hz':99});e.vs['scanned_at']=456
            e.service.start(cmd)

    def test_duplicate_request_never_repeats_switch_and_saved_receipt_survives_lost_reply(self):
        with self.environment() as e:
            cmd=self.command(e);result=e.service.start(cmd);calls=len(e.calls)
            with self.assertRaisesRegex(ValueError,'already submitted'):e.service.start(cmd)
            self.assertEqual(len(e.calls),calls)
            # Failure of a replay must not erase an already completed receipt.
            self.assertEqual(result['connection_switch_receipt']['request_id'],cmd['request_id'])
            self.assertEqual(e.service.state['receipt'],result['connection_switch_receipt'])

    def test_task_transition_receipt_is_exact_and_new_scope_exposes_only_current_proof(self):
        with self.environment() as e:
            ident=task_id('glove',SN);ctx=scope(e.c,ident)['owner_context']
            result=execute(e.c,ident,ctx,self.command(e,'teleoperation',OTHER,user_id=USER))
            receipt=result['task_transition_receipt'];target=task_id('glove',OTHER)
            self.assertEqual(receipt['source_task_id'],ident);self.assertEqual(receipt['target_task_id'],target)
            self.assertFalse(scope(e.c,ident)['current'])
            self.assertEqual(scope(e.c,target)['last_transition_receipt'],receipt)
            e.gs['session_id']+=1
            self.assertNotIn('last_transition_receipt',scope(e.c,target))

    def test_metadata_early_failure_does_not_manufacture_live_reader_or_sticky_busy(self):
        with self.environment('official') as e:
            e.c.teleoperation_handover._runtime.side_effect=ValueError('runtime unavailable')
            with self.assertRaisesRegex(ValueError,'runtime unavailable'):e.service.users()
            self.assertFalse(e.service.catalog['busy']);e.c.factory.assert_not_called()

    def test_read_only_directory_uses_original_transport_and_only_metadata_command(self):
        with self.environment('official') as e:
            stream=io.StringIO();catalog=dict(version=1,users=[dict(user_id=USER,name='Alex',is_default=False,models={'left':True,'right':False})])
            client=N(agent_command='exact controller',exec_command=Mock(return_value=(stream,io.StringIO('WUJI_JSON:'+json.dumps({'type':'sdk_user_catalog','catalog':catalog})+'\n'),None)),close=Mock())
            e.c.factory.side_effect=None;e.c.factory.return_value=client
            e.service.users()
            self.assertEqual(stream.getvalue(),'{"name":"sdk_user_catalog"}\n')
            self.assertEqual(e.service.catalog['users'],catalog['users'])
            e.c.action.assert_not_called();e.c.official_viz.stop_owned.assert_not_called()
            self.assertFalse(e.service.catalog['busy'])

    def test_invalid_protocol_user_and_history_from_other_controller_preflight_keeps_owner(self):
        for user in ('space user','a'*101):
            with self.subTest(user=user),self.environment('official') as e:
                e.service.catalog['users']=[dict(user_id=user)]
                with self.assertRaises(ValueError):e.service.start(self.command(e,'teleoperation',user_id=user))
                e.c.official_viz.stop_owned.assert_not_called()
        with self.environment('official') as e:
            e.service.catalog_config='old controller';e.gs['users']=[dict(user_id=USER)]
            with self.assertRaises(ValueError):e.service.start(self.command(e,'teleoperation',user_id=USER))
            e.c.official_viz.stop_owned.assert_not_called()

    def test_failed_metadata_close_is_retained_and_only_explicit_read_retries_original_close(self):
        with self.environment('official') as e:
            report=dict(version=1,users=[]);stream=io.StringIO()
            client=N(agent_command='original',exec_command=Mock(return_value=(stream,
                io.StringIO('WUJI_JSON:'+json.dumps({'type':'sdk_user_catalog','catalog':report})+'\n'),None)),
                close=Mock(side_effect=[OSError('close unknown'),None]))
            next_client=N(agent_command='fresh',exec_command=Mock(return_value=(io.StringIO(),
                io.StringIO('WUJI_JSON:'+json.dumps({'type':'sdk_user_catalog','catalog':report})+'\n'),None)),close=Mock())
            e.c.factory.side_effect=[client,next_client]
            with self.assertRaisesRegex(ValueError,'cleanup unconfirmed'):e.service.users()
            self.assertTrue(e.service.catalog['busy']);self.assertIs(e.service.catalog_client,client)
            with self.assertRaises(ValueError):e.service.start(self.command(e,'teleoperation',user_id=USER))
            e.c.official_viz.stop_owned.assert_not_called()
            e.service.users();self.assertFalse(e.service.catalog['busy']);self.assertEqual(client.close.call_count,2)
            next_client.close.assert_called_once()

    def test_retryable_exact_cleanup_can_be_retried_but_bare_eof_cannot(self):
        for retryable in (False,True):
            with self.subTest(retryable=retryable),self.environment() as e:
                e.gs.update(connection='error',session_cleanup={'error':'disconnect failed','retryable':retryable})
                if retryable:
                    e.service.start(self.command(e));e.c.glove.stop_session_confirmed.assert_called_once()
                else:
                    with self.assertRaises(ValueError):e.service.start(self.command(e))
                    e.c.glove.stop_session_confirmed.assert_not_called()

    def test_current_target_rescan_cannot_expire_owner_cas_or_skip_exact_open_user(self):
        with self.environment('official') as e:
            e.service.start(self.command(e,'teleoperation',user_id=USER,timeout_ms=500))
            self.assertEqual(e.calls[-1],dict(name='glove_open',serial=SN,user_id=USER,timeout_ms=500))

    def test_original_foreign_workspace_guard_is_checked_before_releasing_old_owner(self):
        with self.environment() as e:
            e.service.preflight_check.side_effect=ValueError('another workspace active')
            with self.assertRaisesRegex(ValueError,'another workspace'):e.service.start(self.command(e))
            e.c.glove.stop_session_confirmed.assert_not_called();e.c.action.assert_not_called()

    def test_pending_switch_and_unknown_cleanup_never_look_idle_to_update_or_exit(self):
        from desktop_tools import idle
        from desktop_lifecycle import needs_confirmation,close_sessions
        for transition in ({'busy':True},{'user_catalog':{'busy':True}},{'cleanup_error':'unknown'}):
            state=dict(connection='disconnected',hardware={'active':False},glove={'busy':False},connection_switch=transition)
            self.assertFalse(idle(state,{}));self.assertTrue(needs_confirmation(state))
            c=N(snapshot=lambda:state,action=Mock(),doctor=N(snapshot=lambda:{}))
            self.assertFalse(close_sessions(c)['ok']);c.action.assert_not_called()

    def test_wearer_metadata_never_switches_user_scans_or_opens_a_device(self):
        from sdk_user_catalog import read_users,validate_report
        manager=N(list_users=Mock(return_value=[dict(user_id=USER,display_name='Alex',is_default=False)]),
                  export_user_data=Mock(return_value=dict(owner_user_id=USER,components=[
                      dict(name='hand_model.left',state='present'),dict(name='hand_model.right',state='missing')])))
        original=tempfile.TemporaryDirectory
        with patch('sdk_user_catalog.tempfile.TemporaryDirectory',side_effect=lambda **_:original(dir=ROOT)):
            result=read_users(manager)
        self.assertEqual(result,[dict(user_id=USER,name='Alex',is_default=False,models={'left':True,'right':False})])
        validate_report(dict(version=1,users=result));manager.list_users.assert_called_once()
        self.assertEqual(manager.export_user_data.call_args.args[0],USER)

    def handler(self,server,path,payload=None,**headers):
        from console_server import Handler
        h=object.__new__(Handler);h.server=server;h.path=path;h.allowed_host=Mock(return_value=True)
        body=json.dumps(payload).encode() if payload is not None else b''
        h.headers={'Content-Length':str(len(body)),**headers};h.rfile=io.BytesIO(body)
        h.reply=Mock();h._task_view_options=lambda:{}
        return h

    def test_private_child_preflight_checks_registration_exact_attempt_and_parent_gate(self):
        from console_server import Handler
        for fault,status in ((None,200),('token',403),('attempt',400),('parent_busy',400)):
            with self.subTest(fault=fault),self.environment() as e:
                e.service.state.update(busy=True,phase='preflight',request_id='a'*32,attempt_id='b'*32)
                state=e.c.snapshot();ident='c'*16;child=dict(id=ident,owner_token='trusted-private-token',port=1)
                fleet=N(lock=threading.RLock(),children={ident:child},get=Mock(return_value=state))
                server=N(controller=e.c,_fleet=fleet,fleet=fleet,switch_siblings_idle=Mock())
                payload=dict(workspace=ident,request_id='a'*32,attempt_id='b'*32,expected_owner=context_digest(state))
                if fault=='attempt':payload['attempt_id']='d'*32
                if fault=='parent_busy':server.switch_siblings_idle.side_effect=ValueError('independent task is active')
                h=self.handler(server,'/api/connection-switch-owner-check',payload,
                    **{'X-Workbench-Owner':'wrong' if fault=='token' else child['owner_token']})
                with patch.dict('os.environ',{'WUJI_FLEET_PARENT':''}):Handler.do_POST(h)
                self.assertEqual(h.reply.call_args.args[0],status)
                if fault in ('token','attempt'):server.switch_siblings_idle.assert_not_called()
                else:server.switch_siblings_idle.assert_called_once_with(ident)
                e.c.action.assert_not_called()

    def test_result_endpoint_exact_request_only_and_unchanged_completion_is_recoverable(self):
        from console_server import Handler
        with self.environment() as e:
            result=e.service.start(self.command(e,'teleoperation'))
            h=self.handler(N(controller=e.c),'/api/connection-switch?request_id='+result['connection_switch']['request_id'])
            Handler.do_GET(h);self.assertEqual(h.reply.call_args.args[0],200)
            self.assertTrue(h.reply.call_args.args[1]['connection_switch']['unchanged'])
            bad=self.handler(N(controller=e.c),'/api/connection-switch?request_id='+'f'*32)
            Handler.do_GET(bad);self.assertEqual(bad.reply.call_args.args[0],400)
            e.c.action.assert_not_called()

    def test_parent_task_scope_proxy_uses_registered_endpoint_only(self):
        from console_server import Handler
        with self.environment(None) as e,patch.dict('os.environ',{'WUJI_FLEET_PARENT':''}):
            ident='c'*16;target=task_id('glove',OTHER);child={'id':ident,'port':1}
            reply=dict(task_id=target,current=True,owner_context='a'*64,last_transition_receipt={'version':1})
            fleet=N(lock=threading.RLock(),children={ident:child},get=Mock(return_value=reply))
            server=N(controller=e.c,_fleet=fleet,fleet=fleet,
                tasks=N(snapshot=Mock(return_value={'tasks':[dict(task_id=target,endpoint_id=ident,status='preview')]})))
            h=self.handler(server,'/api/task-view?task_id='+target);Handler.do_GET(h)
            self.assertEqual(h.reply.call_args.args,(200,reply))
            fleet.get.assert_called_once_with(child,'/api/task-view?task_id='+target)
            server.tasks.snapshot.return_value={'tasks':[]};fleet.get.reset_mock()
            h=self.handler(server,'/api/task-view?task_id='+target);Handler.do_GET(h)
            self.assertFalse(h.reply.call_args.args[1]['current']);fleet.get.assert_not_called()

    def test_physical_feedback_release_requires_real_reader_exit_and_final_worker_ack(self):
        from console_server import Controller
        for fault in (None,'wrong_session','worker_missing','transport_unknown','reader_alive','stop_unknown'):
            with self.subTest(fault=fault):
                thread=N(join=Mock(),is_alive=Mock(return_value=fault=='reader_alive'))
                record=dict(serial=HAND,thread=thread,reader_finished=True,transport_closed=fault!='transport_unknown',
                    worker_closed=fault!='worker_missing',hardware={'active':False,'stop_confirmed':fault!='stop_unknown'})
                c=N(lock=threading.RLock(),generation=2,state={'device_id':HAND,'hardware':{'active':False}},
                    feedback_sessions={2:record},hardware_lease=None,action=Mock())
                if fault:
                    with self.assertRaises(ValueError):Controller.stop_feedback_confirmed(c,3 if fault=='wrong_session' else 2,HAND)
                    if fault=='wrong_session':c.action.assert_not_called()
                else:
                    result=Controller.stop_feedback_confirmed(c,2,HAND)
                    self.assertTrue(result['cleanup_confirmed']);c.action.assert_called_once_with({'name':'disconnect'})

    def test_failed_switch_does_not_reuse_earlier_attempt_error_or_release_flag(self):
        with self.environment() as e:
            e.service.state.update(source_released=True,cleanup_error='old failure',phase='failed')
            e.gs['session_cleanup']={'cleanup_confirmed':True}
            result=e.service.start(self.command(e))
            self.assertIsNone(result['connection_switch']['cleanup_error'])
            self.assertTrue(result['connection_switch']['source_released'])

    def test_parent_owner_check_failure_precedes_release_and_late_parent_change_blocks_new_open(self):
        for stage in ('before','after'):
            with self.subTest(stage=stage),self.environment() as e:
                e.service.check_parent=Mock(side_effect=[ValueError('parent unavailable')] if stage=='before' else [None,ValueError('another task acquired')])
                with self.assertRaises(ValueError):e.service.start(self.command(e))
                if stage=='before':e.c.glove.stop_session_confirmed.assert_not_called()
                else:e.c.glove.stop_session_confirmed.assert_called_once()
                e.c.action.assert_not_called()

    def test_viewer_or_hand_cleanup_failure_recovery_uses_its_own_proof_then_can_reuse(self):
        for mode in ('official','hand'):
            with self.subTest(mode=mode),self.environment(mode) as e:
                target='teleoperation';cmd=self.command(e,target,user_id=USER)
                stop=e.c.official_viz.stop_owned if mode=='official' else e.c.stop_feedback_confirmed
                original=stop.side_effect;stop.side_effect=ValueError('original cleanup failed')
                with self.assertRaisesRegex(ValueError,'original cleanup'):e.service.start(cmd)
                e.c.action.assert_not_called()
                # An explicit retry on the original interface confirms those
                # exact resources; an unrelated glove ACK is never substituted.
                if mode=='official':original(SN,'a'*32)
                else:
                    original(1,HAND)
                    e.c.feedback_sessions={1:dict(worker_closed=True,reader_finished=True,transport_closed=True,
                        thread=N(is_alive=lambda:False),hardware={'active':False,'stop_confirmed':True})}
                state=e.c.snapshot();self.assertIsNone(state['connection_switch']['cleanup_error'])
                cmd=self.command(e,target,user_id=USER);cmd['request_id']='e'*32
                e.service.start(cmd);self.assertEqual(e.gs['connection'],'receiving')

    def test_strict_completed_calibration_reuses_real_continuation_proof_and_new_model(self):
        from calibrated_teleoperation import CalibratedTeleoperation
        from test_calibration_portability import MODEL,completed,bundle_context
        with self.environment('official') as e:
            model=e.folder/'left_hand.urdf';model.write_bytes(MODEL)
            calibration=completed(model);run=calibration['run'];run.update(serial=SN,side='left',expected_side='left')
            run['result']['result']['handedness']='left'
            opened=[]
            def start(bundle):
                opened.append(bundle);e.sdk(SN,USER);e.gs['session_id']+=1
                e.gs['model_provenance']=dict(bundle,owner_user_id=USER)
                return e.gs['session_id']
            export=Mock(side_effect=lambda proof,**kw:{**bundle_context(e.folder/'bundle',side='left'),**proof})
            continuation=CalibratedTeleoperation(lambda:copy.deepcopy(calibration),start,Mock(),e.c.glove.snapshot,
                Mock(),export,owner_workspace='test-workspace',workflow_lock=e.service.workflow_lock,
                owner_check=Mock())
            e.c.calibrated_teleoperation=continuation
            original=e.c.snapshot
            def snapshot():
                value=original();value['calibrated_teleoperation']=continuation.snapshot()
                value['connection_switch']=e.service.snapshot(value);return value
            e.c.snapshot=snapshot
            result=e.service.start(self.command(e,'teleoperation'))
            self.assertEqual(len(opened),1);export.assert_called_once()
            self.assertEqual(opened[0]['serial'],SN)
            self.assertEqual(opened[0]['model_sha256'],__import__('hashlib').sha256(MODEL).hexdigest())
            self.assertEqual(result['connection_switch']['phase'],'connected');e.c.action.assert_not_called()
            self.assertEqual(continuation.snapshot()['phase'],'preview')

    def test_explicit_user_choice_never_silently_imports_another_calibration_wearer(self):
        with self.environment('official') as e:
            e.state['calibrated_teleoperation']=dict(can_continue=True,serial=SN,side='left',run_id='f'*32,expected_revision='e'*64)
            e.c.calibrated_teleoperation=N(_current_proof=Mock(side_effect=AssertionError('Explicit user must not be overridden')))
            e.service.start(self.command(e,'teleoperation',user_id=USER))
            self.assertEqual(e.calls[-1]['user_id'],USER)

    def test_metadata_explicit_false_close_retains_busy_until_original_close_confirms(self):
        with self.environment('official') as e:
            report=dict(version=1,users=[])
            client=N(agent_command='owned',exec_command=Mock(return_value=(io.StringIO(),
                io.StringIO('WUJI_JSON:'+json.dumps({'type':'sdk_user_catalog','catalog':report})+'\n'),None)),
                close=Mock(return_value=False))
            e.c.factory.side_effect=None;e.c.factory.return_value=client
            with self.assertRaisesRegex(ValueError,'cleanup unconfirmed'):e.service.users()
            self.assertTrue(e.service.catalog['busy']);self.assertIs(e.service.catalog_client,client)
            with self.assertRaises(ValueError):e.service.users()
            self.assertEqual(e.c.factory.call_count,1)

    def test_actual_controller_dispatch_binding_and_server_gates_across_bidirectional_switch(self):
        from console_server import Controller,ConsoleHTTPServer
        with self.environment() as e,patch.dict('os.environ',WUJI_OFFICIAL_CLI_DATA=str(e.folder),
                WUJI_STUDIO_COMPONENT_DATA=str(e.folder/'studio'),WUJI_FLEET_PARENT=''), \
                patch('device_profiles.selected_profile',return_value=profile('hand2_left')), \
                patch('managed_runtime.status',return_value={'busy':False,'ready':True}):
            c=Controller(factory=Mock(side_effect=AssertionError('No actual controller transport')),
                         reports=e.folder/'actual-controller'/'reports')
            class Glove:
                lock=threading.RLock();stopping=False;lease=None
                @property
                def busy(self):return e.gs['busy']
                @property
                def session(self):return e.gs['session_id']
                def snapshot(self):return copy.deepcopy(e.gs)
                def action(self,command):return e.c.action.side_effect(command)
                def stop_session_confirmed(self,*a,**kw):return e.c.glove.stop_session_confirmed(*a,**kw)
            c.glove=Glove();c.official_viz=e.c.official_viz
            c.official_viz.start=lambda sn,theme:e.native(sn)
            c.official_viz.check_scan=Mock()
            c.device_inventory=e.c.device_inventory
            c.sdk_session_configs[4]=c.connection_switch._config()
            c.teleoperation_handover._runtime=Mock()
            binding=copy.deepcopy(e.state['retarget_binding']);c.retarget.select(binding,c.state['device_profile'])
            server=object.__new__(ConsoleHTTPServer);server.controller=c
            server.configure_official_cli(startup=False)
            before=c.snapshot();cmd=dict(name='connection_switch',request_id='1'*32,
                expected_owner=before['connection_switch']['expected_owner'],target=dict(mode='official',serial=SN))
            first=c.action(cmd);self.assertEqual(first['connection_switch']['phase'],'connected')
            self.assertEqual(c.connection_switch.resume['binding'],binding)
            cmd=dict(name='connection_switch',request_id='2'*32,
                expected_owner=c.snapshot()['connection_switch']['expected_owner'],target=dict(mode='teleoperation',serial=SN))
            result=c.action(cmd)
            self.assertEqual(result['connection_switch']['phase'],'connected')
            self.assertEqual(c.retarget.selection(c.state['device_profile']),binding)
            opened=[x for x in e.calls if x['name']=='glove_open']
            self.assertEqual(len(opened),1);self.assertEqual(opened[0]['solver']['engine'],'sdk')
            self.assertEqual(opened[0]['user_id'],USER)
            self.assertEqual(c.snapshot()['glove']['controller_config'],c.connection_switch._config())
            self.assertEqual([x['name'] for x in e.calls],['glove_scan','glove_open'])

    def test_timeout_selection_survives_cli_roundtrip(self):
        with self.environment() as e:
            e.gs['stream']['timeout_ms']=500
            e.service.start(self.command(e));self.assertEqual(e.service.resume['timeout_ms'],500)
            cmd=self.command(e,'teleoperation');cmd['request_id']='c'*32
            e.service.start(cmd);self.assertEqual(e.calls[-1]['timeout_ms'],500)

    def test_same_target_failed_owner_retries_original_cleanup_and_reopens_instead_of_unchanged(self):
        with self.environment() as e:
            e.gs.update(connection='error',session_cleanup={'retryable':True,'error':'glove close failed'})
            result=e.service.start(self.command(e,'teleoperation'))
            self.assertFalse(result['connection_switch'].get('unchanged'))
            e.c.glove.stop_session_confirmed.assert_called_once_with(4,expected_serial=SN,expected_user=USER,expected_profile='hand2_left')
            self.assertEqual(e.gs['connection'],'receiving')
            self.assertEqual([x['name'] for x in e.calls],['glove_scan','glove_open'])

    def test_partial_open_retry_uses_verified_cleanup_identity_and_keeps_task_scope(self):
        with self.environment() as e:
            e.gs.update(connection='error',glove_serial=None,profile=None,user={'user_id':''},
                session_cleanup=dict(session_id=4,retryable=True,error='open failed before connected',
                    identity=dict(glove_serial=SN,sdk_user_id=USER,profile='hand2_left')))
            captured=owner(e.c.snapshot());self.assertEqual((captured['serial'],captured['user_id']),(SN,USER))
            ident=task_id('glove',SN);before=scope(e.c,ident);self.assertTrue(before['current'])
            def stop(session,**expected):
                self.assertEqual((session,expected['expected_serial'],expected['expected_user'],expected['expected_profile']),
                                 (4,SN,USER,'hand2_left'))
                e.gs.update(busy=False,connection='disconnected',session_cleanup={'cleanup_confirmed':True})
                return {'cleanup_confirmed':True}
            e.c.glove.stop_session_confirmed.side_effect=stop
            result=execute(e.c,ident,before['owner_context'],self.command(e,'teleoperation'))
            self.assertEqual(e.gs['connection'],'receiving');self.assertEqual(e.gs['user']['user_id'],USER)
            self.assertEqual(result['task_receipt']['task_id'],ident)

    def test_mismatched_cleanup_ack_identity_never_becomes_source_or_task(self):
        for field,value in (('session_id',3),('identity',dict(glove_serial=OTHER,sdk_user_id=USER,profile='hand2_right'))):
            with self.subTest(field=field),self.environment() as e:
                e.gs.update(connection='error',glove_serial=None,profile=None,
                    session_cleanup=dict(session_id=4,retryable=True,error='failed',
                        identity=dict(glove_serial=SN,sdk_user_id=USER,profile='hand2_left')))
                e.gs['session_cleanup'][field]=value
                self.assertFalse(scope(e.c,task_id('glove',SN))['current'])
                with self.assertRaises(ValueError):e.service.start(self.command(e,'teleoperation'))
                e.c.glove.stop_session_confirmed.assert_not_called()

    def test_stop_during_new_sdk_publication_sets_exact_pending_stop_before_any_open(self):
        with self.environment('official') as e:
            original=e.c.action.side_effect
            def start(command):
                original(command)
                if command['name']=='glove_scan':e.service.cancelled=True
            e.c.action.side_effect=start
            with self.assertRaisesRegex(ValueError,'Switch stopped'):e.service.start(self.command(e,'teleoperation',user_id=USER))
            e.c.glove.disconnect_session.assert_called_once_with(5,pending_only=True)
            self.assertFalse(any(c['name']=='glove_open' for c in e.calls))
            self.assertEqual(e.gs['connection'],'disconnected')

    def test_missing_hand_runtime_keeps_exact_source_then_explicit_retry_connects(self):
        for mode in ('teleoperation','official'):
            with self.subTest(source=mode),self.environment(mode) as e:
                before=owner(e.c.snapshot())
                e.c.teleoperation_handover._runtime.side_effect=ValueError('SYNTHETIC runtime unavailable')
                with self.assertRaisesRegex(ValueError,'runtime unavailable'):
                    e.service.start(self.command(e,'hand',HAND))
                self.assertEqual(owner(e.c.snapshot()),before)
                e.c.glove.stop_session_confirmed.assert_not_called();e.c.official_viz.stop_owned.assert_not_called()
                e.c.action.assert_not_called()
                e.c.teleoperation_handover._runtime.side_effect=None
                result=e.service.start(self.command(e,'hand',HAND))
                self.assertEqual(result['connection_switch_receipt']['target']['serial'],HAND)
                self.assertEqual(owner(e.c.snapshot())['mode'],'hand')
                self.assertEqual([x['name'] for x in e.calls],['connect'])

    def test_native_component_missing_or_corrupt_never_releases_and_explicit_retry_reuses_source(self):
        from cli_release_store import ReleaseStoreError
        for error in (FileNotFoundError('SYNTHETIC missing CLI'),ReleaseStoreError('SYNTHETIC CLI failed SHA-256')):
            with self.subTest(error=type(error).__name__),self.environment() as e, \
                    patch('native_cli.resolve',side_effect=error) as resolve, \
                    patch('native_cli.prepare',side_effect=AssertionError('Preflight must not prepare software')):
                before=owner(e.c.snapshot())
                with self.assertRaisesRegex(ValueError,'Settings first'):
                    e.service.start(self.command(e))
                resolve.assert_called_once_with(strict=True)
                self.assertEqual(owner(e.c.snapshot()),before)
                e.c.glove.stop_session_confirmed.assert_not_called();e.c.action.assert_not_called()
                self.assertFalse(e.service.snapshot(e.c.snapshot())['busy'])
                resolve.side_effect=None;resolve.return_value={'binary':'SYNTHETIC-verified-native-cli','version':'2026.10.12'}
                cmd=self.command(e);cmd['request_id']='d'*32
                result=e.service.start(cmd)
                self.assertEqual(result['connection_switch']['phase'],'connected')
                e.c.glove.stop_session_confirmed.assert_called_once()

    def test_non_native_official_route_without_exact_handover_contract_keeps_source_and_configuration(self):
        for mode in ('wsl','local'):
            config={'mode':mode,'cli':'/custom/bin/wuji','python':'/custom/python','agent_directory':'/custom/source'}
            with self.subTest(mode=mode),self.environment() as e,patch('bridge_config.load_config',return_value=config), \
                    patch('official_device_discovery.use_native',return_value=False), \
                    patch('doctor.local_run',side_effect=AssertionError('Unsupported handover must not start the CLI')) as run, \
                    patch('native_cli.resolve',side_effect=AssertionError('Custom route must not use native CLI')) as resolve, \
                    patch('bridge_config.save_config',side_effect=AssertionError('Custom route must not be rewritten')) as save, \
                    patch('managed_runtime.WslController',side_effect=AssertionError('No source publication during preflight')):
                before=owner(e.c.snapshot())
                with self.assertRaisesRegex(ValueError,'current connection was retained'):e.service.start(self.command(e))
                self.assertEqual(owner(e.c.snapshot()),before)
                run.assert_not_called();resolve.assert_not_called();save.assert_not_called()
                e.c.glove.stop_session_confirmed.assert_not_called();e.c.action.assert_not_called()
                e.c.teleoperation_handover._runtime.assert_not_called()
                self.assertEqual(__import__('bridge_config').load_config(),config)

    def test_official_preflight_pending_and_expired_budget_cannot_release_or_start(self):
        with self.environment() as e,patch('native_cli.resolve') as resolve:
            now=[0.];e.service.clock=lambda:now[0]
            def inspect(**kwargs):
                self.assertEqual(kwargs,{'strict':True})
                self.assertTrue(e.c.snapshot()['connection_switch']['busy'])
                self.assertFalse(e.c.snapshot()['connection_switch']['can_switch'])
                now[0]=46.
                return {'binary':'SYNTHETIC-verified-native-cli','version':'2026.10.12'}
            resolve.side_effect=inspect
            with self.assertRaisesRegex(ValueError,'time budget expired'):e.service.start(self.command(e))
            e.c.glove.stop_session_confirmed.assert_not_called();e.c.action.assert_not_called()
            self.assertEqual(e.gs['connection'],'receiving')



if __name__=='__main__':unittest.main()
