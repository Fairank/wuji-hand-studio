"""Source-bound primary open/follow through the original complete controller pipeline."""
from contextlib import ExitStack,contextmanager,nullcontext
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import Handler,PORT
from demo_player import DemoPlayer
from retarget_workspace import RetargetWorkspace
from retarget_tuning import TuningStore
from task_view import scope,execute,_task
import test_glove_stale_intent_0258 as stale_fixture
SN=stale_fixture.SN;HAND=stale_fixture.HAND

TEMP=Path(__file__).resolve().parents[2]/'update-flow-0258/backend-audit/primary-test-temp'
TEMP.mkdir(parents=True,exist_ok=True)


class GlovePrimaryIntentTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as temporary,ExitStack() as patches:
            base=stale_fixture.GloveStaleIntentTests();c=base.controller();c.glove.verify_follow_sdk=Mock()
            c.group=N(active=False,internal=False)
            c.program=N(thread=None,snapshot=lambda:{'active':False})
            c.calibration=N(run={'running':False})
            c.calibrated_teleoperation=N(internal=False,snapshot=lambda:{'busy':False},cancel=Mock())
            c.doctor=N(snapshot=lambda:{'running':False});c.parameters=N(sync_status={'busy':False})
            c.player=DemoPlayer();c.state.update(connection='disconnected',recording={'active':False})
            c.retarget=RetargetWorkspace(Path(temporary)/'retarget');c.tuning=TuningStore(Path(temporary)/'tuning')
            c.retarget.select(dict(generation='hand2',side='left',glove_serial=SN,sdk_user='owner-A',hand_serial=HAND),c.state['device_profile'])
            for name in ('firmware_host.enforce_maintenance','studio_component.enforce_session_owner','cli_coordination.enforce'):
                patches.enter_context(patch(name))
            patches.enter_context(patch('cli_coordination.gate',side_effect=lambda *a,**kw:nullcontext()))
            yield N(c=c,b=c.glove,base=base)

    def command(self,e,name='glove_follow'):
        extra={'workspace_clear':True} if name=='glove_follow' else dict(serial=SN,user_id='owner-A',timeout_ms=250)
        return dict(e.base.intent(e.c,name),**extra)

    def test_old_or_partial_primary_intents_refuse_before_sdk_check_binding_or_send(self):
        with self.environment() as e:
            before=e.c.retarget.path.read_bytes()
            for name in ('glove_follow','glove_open'):
                original=self.command(e,name)
                commands=[dict(original,expected_session=43),dict(original,expected_revision=1),
                          {k:v for k,v in original.items() if k!='expected_session'},
                          {k:v for k,v in original.items() if k!='expected_revision'},dict(original,_workflow_held=True)]
                for command in commands:
                    with self.subTest(command=command),self.assertRaises(ValueError):e.c.action(command)
            e.b.verify_follow_sdk.assert_not_called();self.assertEqual(e.base.wires(e.c),[])
            self.assertEqual(e.c.retarget.path.read_bytes(),before)

    def test_current_follow_keeps_sdk_preflight_and_returns_exact_post_send_lease_owner(self):
        with self.environment() as e:
            result=e.c.action(self.command(e))
            e.b.verify_follow_sdk.assert_called_once();e.b._require_follow_sdk.assert_called_once()
            self.assertEqual((result['lease_session'],result['lease_revision']),(44,1))
            self.assertEqual(result['lease'],e.b.lease);self.assertNotIn('glove',result)
            wire=e.base.wires(e.c);self.assertEqual(len(wire),1)
            self.assertEqual(wire[0]['name'],'glove_follow')
            self.assertNotIn('expected_session',wire[0]);self.assertNotIn('expected_revision',wire[0])

    def test_current_open_uses_real_retarget_validation_and_only_returns_submission_identity(self):
        with self.environment() as e:
            e.b.state.update(connection='ready',glove_serial='')
            result=e.c.action(self.command(e,'glove_open'))
            self.assertEqual(result,dict(session_id=44,action_revision=1,open_pending=True));self.assertNotIn('glove',result)
            wire=e.base.wires(e.c)[0]
            self.assertEqual((wire['name'],wire['serial'],wire['user_id']),('glove_open',SN,'owner-A'))
            self.assertEqual(wire['solver']['engine'],'sdk');self.assertIn('retarget',wire)
            self.assertNotIn('expected_session',wire);self.assertNotIn('expected_revision',wire)
            self.assertEqual(e.b.state['connection'],'ready')
            self.assertEqual(e.b.snapshot()['connection'],'connecting');self.assertTrue(e.b.snapshot()['open_pending'])
            self.assertEqual(e.c.retarget.context(e.c.state['device_profile'])['binding']['glove_serial'],SN)

    def test_source_changed_after_sdk_preflight_or_at_final_bridge_boundary_never_starts(self):
        for boundary in ('sdk','bridge'):
            for field in ('session','action_revision'):
                with self.subTest(boundary=boundary,field=field),self.environment() as e:
                    command=self.command(e)
                    def change():setattr(e.b,field,getattr(e.b,field)+1)
                    if boundary=='sdk':e.b.verify_follow_sdk.side_effect=change
                    else:
                        original=e.b.action
                        def final(value):change();return original(value)
                        e.b.action=final
                    with self.assertRaises(ValueError):e.c.action(command)
                    e.b.verify_follow_sdk.assert_called_once();self.assertEqual(e.base.wires(e.c),[])
                    self.assertIsNone(e.b.lease);e.b._require_follow_sdk.assert_not_called()

    def test_original_sdk_typed_pair_and_freshness_rejections_remain_before_send(self):
        for fault in ('sdk','identity','stale'):
            with self.subTest(fault=fault),self.environment() as e:
                if fault=='sdk':e.b.verify_follow_sdk.side_effect=ValueError('synthetic stable SDK check failed')
                elif fault=='identity':e.b.state['feedback']['identified_device']['serial']='WH_OTHER'
                else:e.b.state['stream']['fresh']=False
                with self.assertRaises(ValueError):e.c.action(self.command(e))
                e.b.verify_follow_sdk.assert_called_once();self.assertEqual(e.base.wires(e.c),[])
                self.assertEqual(e.b.action_revision,0)

    def test_real_task_context_with_nonreentrant_lock_supports_scoped_and_legacy_follow(self):
        with self.environment() as e:
            task=_task('glove',SN);before=scope(e.c,task)['owner_context']
            result=execute(e.c,task,before,self.command(e));self.assertIn('task_receipt',result)
            self.assertNotEqual(before,result['task_receipt']['after_context'])
            e.base.ack(e.c,active=True);e.b.action({'name':'glove_stop'});e.base.ack(e.c,active=False)
            with self.assertRaises(ValueError):execute(e.c,task,before,{'name':'glove_follow','workspace_clear':True})
            current=scope(e.c,task)['owner_context'];count=len(e.base.wires(e.c))
            result=execute(e.c,task,current,{'name':'glove_follow','workspace_clear':True})
            self.assertEqual(len(e.base.wires(e.c)),count+1);self.assertIn('lease_revision',result)

    def post(self,c,command,task=None,context=None):
        body=json.dumps(command).encode();handler=object.__new__(Handler);handler.path='/api/action'
        handler.headers={'Host':f'127.0.0.1:{PORT}','X-Console-Token':c.csrf,
            'Content-Length':str(len(body)),'Content-Type':'application/json'}
        if task:handler.headers.update({'X-Task-Id':task,'X-Task-Context':context})
        handler.rfile=io.BytesIO(body);handler.server=N(controller=c);replies=[]
        handler.reply=lambda *args,**kw:replies.append((args,kw));handler.do_POST()
        self.assertEqual(len(replies),1);return replies[0][0]

    def test_main_http_requires_pair_for_all_local_actions_and_verified_task_context_is_the_only_alternative(self):
        with self.environment() as e:
            for name in ('glove_open','glove_follow','glove_stop','glove_disconnect'):
                original=self.command(e,name) if name in ('glove_open','glove_follow') else e.base.intent(e.c,name)
                for payload in ({k:v for k,v in original.items() if not k.startswith('expected_')},
                                {k:v for k,v in original.items() if k!='expected_revision'}):
                    with self.subTest(name=name,payload=payload):self.assertEqual(self.post(e.c,payload)[0],400)
            self.assertEqual(e.base.wires(e.c),[]);e.b.verify_follow_sdk.assert_not_called()
            task=_task('glove',SN);old=scope(e.c,task)['owner_context']
            self.assertEqual(self.post(e.c,self.command(e))[0],200)
            self.assertEqual(self.post(e.c,{'name':'glove_stop'},task,old)[0],400)
            current=scope(e.c,task)['owner_context']
            self.assertEqual(self.post(e.c,{'name':'glove_stop'},task,current)[0],200)
            self.assertEqual(len(e.base.wires(e.c)),2)
            self.assertEqual(self.post(e.c,{'name':'glove_stop'},'f'*24,current)[0],400)


if __name__=='__main__':unittest.main()
