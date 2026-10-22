"""Cross-module task acceptance using typed synthetic controllers, never SDKs."""
import copy
from http.server import ThreadingHTTPServer
import json
import io
from pathlib import Path
import re
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from task_control import execute
from task_server import TaskServer
from task_sessions import TaskSessions, context_digest


def empty():
    return dict(connection='disconnected',device_id=None,identified_device=None,
                device_profile=dict(id='hand2_left',side='left'),session_generation=1,
                hardware=dict(active=False),glove={},official_viz={},calibration={},
                unconfirmed_devices=[],program=dict(active=False),group=dict(active=False))


def hand(sn='HAND-A'):
    state=empty();state.update(connection='connected',device_id=sn,stale=False,
        identified_device=dict(source='sdk_identity',serial=sn,profile='hand2_left'))
    return state


def glove(sn='GLOVE-A',user=''):
    state=empty();state['glove']=dict(connection='receiving',glove_serial=sn,
        profile='hand2_left',session_id=4,user=dict(user_id=user),stream=dict(fresh=True),
        feedback={},hardware={},can_prepare=True)
    return state


class SyntheticController:
    def __init__(self,state):
        self.state=copy.deepcopy(state);self.calls=[]
        self.calibration_handover=NS(workflow_lock=threading.RLock())
        self.fail_prepare=None

    def snapshot(self):return copy.deepcopy(self.state)

    def task_context_action(self,context,action,payload=None):
        return execute(self,context,action,payload)

    def _task_context_dispatch(self,action,payload,state):
        self.calls.append((action,copy.deepcopy(payload)))
        if action=='disconnect':
            self.state.update(connection='disconnected',device_id=None,identified_device=None)
            if self.state.get('glove',{}).get('connection')=='receiving':self.state['glove']={}
            self.state['session_generation']+=1
        elif action=='pause':self.state['hardware']['active']=False
        elif action=='pair_prepare':
            if self.fail_prepare:raise ValueError(self.fail_prepare)
            g=self.state['glove'];g['feedback']=dict(connection='connected',stale=False,
                device_id=payload['hand_serial'],identified_device=dict(source='sdk_identity',
                serial=payload['hand_serial'],profile=payload['profile']))
            g['hardware']=dict(active=False);g['can_prepare']=False
        elif action=='unpair':
            self.state['glove'].update(feedback={},hardware={},can_prepare=True)
        return dict(requested=True)


class SyntheticFleet:
    def __init__(self,controllers):
        self.controllers=controllers;self.lock=threading.RLock()
        self.children={key:dict(id=key,port=19001+i) for i,key in enumerate(controllers)}
    def inventory_snapshots(self):
        return [dict(id=key,state=value.snapshot()) for key,value in self.controllers.items()]
    def get(self,record,path):
        assert path=='/api/state';return self.controllers[record['id']].snapshot()
    def scoped_post(self,record,context,action,payload):
        return self.controllers[record['id']].task_context_action(context,action,payload)


def server(main=None,**children):
    host=NS(controller=SyntheticController(main or empty()),server_address=('127.0.0.1',19000),
            _fleet=SyntheticFleet({key:SyntheticController(state) for key,state in children.items()}))
    service=TaskServer(host);return host,service


def command(task,action,**extra):
    return dict(name='task_session_action',task_id=task['task_id'],expected_revision=task['revision'],action=action,**extra)


class TaskServerIntegrationTests(unittest.TestCase):
    def test_active_group_only_blocks_its_actual_participants_not_idle_catalog_sibling(self):
        host,service=server(target=hand())
        run=dict(active=True,members=[dict(id='a'),dict(id='b')])
        host._workspaces=NS(lock=threading.RLock(),rows=[dict(id='catalog',members=['a','b','target'])],
            coordinators={'catalog':NS(snapshot=lambda:copy.deepcopy(run))})
        task=next(t for t in service.registry.snapshot()['tasks'] if t['endpoint_id']=='target')
        service.check_group(task)
        for member in ('a','b'):
            with self.assertRaisesRegex(ValueError,'ensemble'):
                service.check_group(dict(task,endpoint_id=member))
        self.assertEqual(host._fleet.controllers['target'].calls,[])

    def test_unknown_active_group_members_still_block_catalog_owners(self):
        host,service=server(target=hand())
        for members in (None,[],[dict(id='a')],[dict(id='a'),dict(id='a')],
                [dict(id='a'),dict(id='outside')],['a','b']):
            with self.subTest(members=members):
                host._workspaces=NS(lock=threading.RLock(),rows=[dict(id='catalog',members=['a','b','target'])],
                    coordinators={'catalog':NS(snapshot=lambda members=members:dict(active=True,members=members))})
                with self.assertRaisesRegex(ValueError,'ensemble'):
                    service.check_group(dict(endpoint_id='target'))

    def test_discovery_selection_and_snapshot_never_dispatch(self):
        state=empty();state['devices']=[dict(sn='HAND-A')];state['selected_device']='HAND-A'
        host,service=server(state)
        for _ in range(3):self.assertEqual(service.snapshot()['tasks'],[])
        self.assertEqual(host.controller.calls,[])

    def test_main_pause_uses_empty_typed_payload_and_one_exact_controller(self):
        state=hand();state['hardware']['active']=True
        host,service=server(state,other=hand('HAND-B'))
        task=next(t for t in service.snapshot()['tasks'] if t['endpoint_id']=='main')
        service.action(command(task,'pause'))
        self.assertEqual(host.controller.calls,[('pause',{})])
        self.assertEqual(host._fleet.controllers['other'].calls,[])

    def test_child_disconnect_passes_strict_owner_adapter(self):
        host,service=server(one=hand(),two=hand('HAND-B'))
        task=next(t for t in service.snapshot()['tasks'] if t['endpoint_id']=='one')
        service.action(command(task,'disconnect'))
        self.assertEqual(host._fleet.controllers['one'].calls,[('disconnect',{})])
        self.assertEqual(host._fleet.controllers['two'].calls,[])

    def test_same_serial_new_epoch_invalidates_old_action(self):
        host,service=server(one=hand());task=service.snapshot()['tasks'][0]
        host._fleet.controllers['one'].state['session_generation']+=1
        with self.assertRaisesRegex(ValueError,'Task changed'):service.action(command(task,'disconnect'))
        self.assertEqual(host._fleet.controllers['one'].calls,[])

    def test_private_destination_error_is_preserved_without_retry_or_other_owner(self):
        host,service=server(one=hand());calls=[]
        def reject(*args):
            calls.append(args)
            raise HTTPError('http://127.0.0.1:19001/api/task-owner',409,'Conflict',{},
                io.BytesIO(b'{"ok":false,"error":"Exact source mapping differs"}'))
        host._fleet.scoped_post=reject
        with self.assertRaisesRegex(ValueError,'Exact source mapping differs'):
            service.dispatch('one',context_digest(host._fleet.controllers['one'].state),'disconnect',{})
        self.assertEqual(len(calls),1);self.assertEqual(host.controller.calls,[])

    def test_flat_calibration_epoch_is_in_owner_digest(self):
        a=empty();a['calibration']=dict(id='a'*32,running=False)
        b=copy.deepcopy(a);b['calibration']['id']='b'*32
        self.assertNotEqual(context_digest(a),context_digest(b))

    def test_missing_robot_profile_never_creates_verified_hand(self):
        state=hand();state['device_profile']={};state['identified_device']['profile']=None
        host,service=server(state)
        self.assertEqual(service.snapshot()['tasks'],[])

    def test_pure_glove_empty_hardware_is_not_unknown_but_explicit_null_stays_unknown(self):
        host,service=server(source=glove());task=service.snapshot()['tasks'][0]
        self.assertTrue(task['capabilities']['can_pair'])
        host._fleet.controllers['source'].state['glove']['hardware']={'active':None}
        task=service.snapshot()['tasks'][0]
        self.assertFalse(task['capabilities']['can_pair'])

    def test_registry_rejects_unqualified_target_before_pair_callback(self):
        for mutation in ('stale','running','journal','wrong_profile','same_endpoint','native'):
            with self.subTest(mutation=mutation):
                host,service=server(source=glove(),target=hand())
                calls=[];service.registry.pair=lambda *args:calls.append(args)
                target_state=host._fleet.controllers['target'].state
                if mutation=='stale':target_state['stale']=True
                elif mutation=='running':target_state['hardware']['active']=True
                elif mutation=='journal':target_state['unconfirmed_devices']=[dict(source='hand')]
                elif mutation=='wrong_profile':
                    target_state['device_profile']=dict(id='hand2_right',side='right')
                    target_state['identified_device']['profile']='hand2_right'
                elif mutation=='native':
                    target_state.update(connection='disconnected',device_id=None)
                    target_state['official_viz']=dict(backend='windows_native',active=True,phase='running',session_id=8,
                        reported_device=dict(sn='HAND-A',kind='hand',side='left'))
                if mutation=='same_endpoint':
                    source_state=host._fleet.controllers['source'].state
                    source_state.update({k:v for k,v in hand().items() if k!='glove'})
                    del host._fleet.controllers['target'];del host._fleet.children['target']
                rows=service.snapshot()['tasks'];source=next(t for t in rows if t['kind']=='glove');target=next(t for t in rows if t['kind']=='hand')
                with self.assertRaises(ValueError):service.action(command(source,'pair',hand_task_id=target['task_id'],hand_revision=target['revision']))
                self.assertEqual(calls,[])

    def test_native_view_and_calibration_use_official_workspace(self):
        state=empty();state['official_viz']=dict(backend='windows_native',active=True,phase='running',session_id=8,
            reported_device=dict(sn='GLOVE-A',kind='glove',side='left'))
        host,service=server(state);task=service.snapshot()['tasks'][0]
        self.assertEqual(task['view']['page'],'official-viz');self.assertTrue(task['view']['allow_native'])

    def test_new_continuation_record_is_historical_not_fresh_stream_and_exact_glove_only(self):
        state=empty();state['calibration']=dict(id='a'*32,running=False)
        state['calibrated_teleoperation']=dict(can_continue=True,serial='GLOVE-A',side='left')
        host,service=server(state)
        task=service.snapshot()['tasks'][0]
        self.assertEqual(task['status'],'calibrated');self.assertTrue(task['observed']['stale'])
        self.assertIsNone(task['observed']['hardware_active']);self.assertTrue(task['observed']['continuation_verified'])
        self.assertTrue(task['capabilities']['can_continue_teleoperation'])
        self.assertFalse(task['capabilities']['can_pair'])

    def test_other_hand_never_inherits_glove_continuation(self):
        state=hand();state['calibrated_teleoperation']=dict(can_continue=True,serial='GLOVE-A',side='left')
        host,service=server(state);tasks=service.snapshot()['tasks']
        h=next(t for t in tasks if t['kind']=='hand')
        self.assertFalse(h['capabilities']['can_continue_teleoperation'])
        self.assertFalse(h['observed']['continuation_verified'])

    def test_proof_while_a_viewer_owns_the_glove_does_not_offer_implicit_handover(self):
        state=empty();state['calibrated_teleoperation']=dict(can_continue=True,serial='GLOVE-A',side='left')
        state['official_viz']=dict(backend='windows_native',active=True,phase='running',session_id=8,
            reported_device=dict(sn='GLOVE-A',kind='glove',side='left'))
        host,service=server(state);task=service.snapshot()['tasks'][0]
        self.assertEqual(task['status'],'viewing');self.assertFalse(task['capabilities']['can_continue_teleoperation'])
        self.assertEqual(host.controller.calls,[])

    def test_two_current_feedback_owners_are_conflict_not_a_hidden_old_hand_alias(self):
        source=glove();source['glove'].update(can_prepare=False,hardware={'active':False},
            feedback=dict(connection='connected',stale=False,device_id='HAND-A',
                identified_device=dict(source='sdk_identity',serial='HAND-A',profile='hand2_left')))
        host,service=server(source=source,target=hand());tasks=service.snapshot()['tasks']
        self.assertEqual(len(tasks),2)
        for task in tasks:
            self.assertEqual(task['status'],'conflict');self.assertNotIn('paired_into',task)
            self.assertTrue(task['capabilities'].get('can_disconnect',False))
            self.assertFalse(task['capabilities'].get('can_pair',False))

    def test_two_glove_pairs_claiming_same_hand_are_both_conflict_and_do_not_alias_history(self):
        def paired(sn):
            source=glove(sn);source['glove'].update(can_prepare=False,hardware={'active':False},
                feedback=dict(connection='connected',stale=False,device_id='HAND-A',
                    identified_device=dict(source='sdk_identity',serial='HAND-A',profile='hand2_left')))
            return source
        host,service=server(history=hand())
        service.snapshot()
        host._fleet.controllers['history'].state=empty()
        for key,sn in (('one','GLOVE-A'),('two','GLOVE-B')):
            host._fleet.controllers[key]=SyntheticController(paired(sn))
            host._fleet.children[key]=dict(id=key,port=19010+len(host._fleet.children))
        tasks=service.snapshot()['tasks'];pairs=[task for task in tasks if task['kind']=='glove_pair']
        self.assertEqual(len(pairs),2)
        for task in pairs:
            self.assertEqual(task['status'],'conflict');self.assertFalse(task['capabilities'].get('can_unpair',False))
        old=next(task for task in tasks if task['kind']=='hand')
        self.assertNotIn('paired_into',old)
        # Conflicting owners cannot pair/unpair/continue, but each exact idle
        # controller can still release its own session explicitly.
        one=next(task for task in pairs if task['endpoint_id']=='one')
        service.action(command(one,'disconnect'))
        self.assertEqual(host._fleet.controllers['one'].calls,[('disconnect',{})])
        self.assertEqual(host._fleet.controllers['two'].calls,[])
        remaining=next(task for task in service.snapshot()['tasks'] if task['endpoint_id']=='two')
        self.assertEqual(remaining['status'],'preview');self.assertTrue(remaining['capabilities']['can_unpair'])

    def test_real_async_pair_dispatches_exact_payload_and_observes_combined_owner(self):
        host,service=server(source=glove(),target=hand())
        rows=service.snapshot()['tasks'];source=next(t for t in rows if t['kind']=='glove');target=next(t for t in rows if t['kind']=='hand')
        service.action(command(source,'pair',hand_task_id=target['task_id'],hand_revision=target['revision']))
        service.pairing.thread.join(2);self.assertFalse(service.pairing.thread.is_alive())
        self.assertEqual(service.pairing.snapshot()['phase'],'paired')
        self.assertEqual(host._fleet.controllers['target'].calls,[('disconnect',{})])
        self.assertEqual(host._fleet.controllers['source'].calls,[('pair_prepare',dict(hand_serial='HAND-A',profile='hand2_left',sdk_user=''))])
        rows=service.snapshot()['tasks'];paired=next(t for t in rows if t['kind']=='glove_pair')
        old=next(t for t in rows if t['kind']=='hand')
        self.assertEqual(old['paired_into'],paired['task_id'])
        self.assertFalse(any(action=='glove_follow' for c in host._fleet.controllers.values() for action,_ in c.calls))

    def test_async_pair_error_is_visible_for_original_source_and_retains_partial_release(self):
        host,service=server(source=glove(),target=hand());host._fleet.controllers['source'].fail_prepare='Synthetic mapping mismatch'
        rows=service.snapshot()['tasks'];source=next(t for t in rows if t['kind']=='glove');target=next(t for t in rows if t['kind']=='hand')
        service.action(command(source,'pair',hand_task_id=target['task_id'],hand_revision=target['revision']))
        service.pairing.thread.join(2);result=service.snapshot()
        self.assertTrue(result['pairing']['released_hand'])
        source=next(t for t in result['tasks'] if t['task_id']==source['task_id'])
        self.assertIn('Synthetic mapping mismatch',source['capabilities']['blocked_reason'])
        self.assertEqual(host._fleet.controllers['target'].state['connection'],'disconnected')
        self.assertEqual([a for a,_ in host._fleet.controllers['target'].calls],['disconnect'])
        host._fleet.controllers['source'].state['glove']['session_id']+=1
        source=next(t for t in service.snapshot()['tasks'] if t['task_id']==source['task_id'])
        self.assertNotIn('Synthetic mapping mismatch',source['capabilities']['blocked_reason'])

    def test_actual_http_handler_assets_csp_and_exact_public_task_action(self):
        import console_server
        state=hand();state['hardware']['active']=True
        host,unused=server(state)
        controller=host.controller;controller.csrf='synthetic-task-csrf'
        controller.state['csrf']=controller.csrf
        http=ThreadingHTTPServer(('127.0.0.1',0),console_server.Handler)
        http.controller=controller;http._fleet=host._fleet
        http._tasks=TaskServer(http);http.tasks=http._tasks
        port=http.server_address[1];origin=f'http://127.0.0.1:{port}'
        thread=threading.Thread(target=http.serve_forever,daemon=True)
        try:
            with patch.object(console_server,'PORT',port),patch.object(console_server,'ORIGIN',origin),patch.dict('os.environ',{'WUJI_FLEET_PARENT':'','WUJI_FLEET_OWNER_TOKEN':''}):
                thread.start()
                with urlopen(origin+'/api/task-sessions',timeout=2) as response:task=json.load(response)['tasks'][0]
                self.assertEqual(controller.calls,[])
                for name in ('task_workspace.js','task_workspace.css'):
                    with urlopen(origin+'/'+name,timeout=2) as response:body=response.read()
                    self.assertEqual(body,(Path(console_server.__file__).parent/'web'/name).read_bytes())
                with urlopen(origin+'/?embedded=1&task_view=1&task_id='+task['task_id'],timeout=2) as response:
                    policy=response.headers['Content-Security-Policy'];html=response.read().decode()
                self.assertIn("frame-ancestors 'self'",policy)
                nonce=re.search(r'<meta name="workbench-style-nonce" content="([^"]+)',html).group(1)
                self.assertIn("'nonce-"+nonce+"'",policy)
                self.assertNotIn('unsafe-inline',policy);self.assertNotIn('unsafe-eval',policy)
                self.assertIn('/task_workspace.js',html);self.assertIn('/task_workspace.css',html)
                headers={'Content-Type':'application/json','Origin':origin,'X-Console-Token':controller.csrf}
                request=Request(origin+'/api/action',data=json.dumps(command(task,'pause')).encode(),headers=headers)
                with urlopen(request,timeout=2) as response:self.assertTrue(json.load(response)['ok'])
                self.assertEqual(controller.calls,[('pause',{})])
                request=Request(origin+'/api/action',data=json.dumps(command(task,'disconnect',serial='OTHER')).encode(),headers=headers)
                with self.assertRaises(HTTPError):urlopen(request,timeout=2)
                self.assertEqual(controller.calls,[('pause',{})])
        finally:
            if thread.is_alive():http.shutdown();thread.join(2)
            http.server_close()


if __name__=='__main__':unittest.main()
