"""Saved bootstrap uses the existing total switch deadline; no vendor/device calls."""
from contextlib import contextmanager
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import test_connection_switch as fixtures

SN=fixtures.SN;USER=fixtures.USER

class StartupBudgetTests(unittest.TestCase):
    @contextmanager
    def environment(self,*,ready_after=13.,prepare_cost=0.,cancel_at=None):
        root=Path(os.environ.get('WUJI_CONTROL_QA_ROOT',tempfile.gettempdir()))/'startup-budget-0267';root.mkdir(parents=True,exist_ok=True)
        with patch.object(fixtures,'ROOT',root),fixtures.SwitchTests.environment(self,'official') as e:
            now=[0.];start_at=[None];callback=[None];opened=[];started=[]
            e.service.clock=lambda:now[0]
            proof=dict(source_kind='saved_profile',source_run_id=None,transfer_id='e'*32,serial=SN,side='left',
                source_profile='BudgetPerson',source_user_id=USER,model_sha256='d'*64,model_bytes=32)
            e.c._saved_profile_preflight=Mock(return_value=copy.deepcopy(proof))
            def prepare(value,*,cancel_check=None):
                now[0]+=prepare_cost;cancel_check();return dict(value,bundle_path='/synthetic-no-file.zip')
            e.c._prepare_saved_profile=prepare
            def start(bundle,value,*,expected_profile,timeout_ms=250):
                self.assertEqual(expected_profile,'hand2_left');start_at[0]=now[0];started.append(e.service.state['attempt_id'])
                e.gs.update(session_id=e.gs['session_id']+1,busy=True,connection='connecting',glove_serial=None,stream={})
                attempt=e.service.state['attempt_id']
                def publish():
                    with e.service.open_guard(attempt):
                        opened.append(now[0]);e.sdk(SN,USER);e.gs['stream'].update(solver=dict(engine='sdk',applied=True),timeout_ms=650)
                callback[0]=publish;return e.gs['session_id']
            e.c._start_saved_profile_session=start
            def sleep(amount):
                now[0]+=amount
                if cancel_at is not None and start_at[0] is not None and now[0]-start_at[0]>=cancel_at and not e.service.cancelled:
                    e.service.cancel(dict(name='connection_switch_cancel',request_id=e.service.state['request_id'],attempt_id=e.service.state['attempt_id']))
                if start_at[0] is not None and now[0]-start_at[0]>=ready_after and not opened and not e.service.cancelled:callback[0]()
            e.service.sleep=sleep
            yield e,now,opened,started,callback

    def command(self,e):return fixtures.SwitchTests.command(self,e,'teleoperation',timeout_ms=650)

    def test_saved_bootstrap_ready_after_twelve_seconds_still_completes_inside_total_budget(self):
        with self.environment() as (e,now,opened,started,callback):
            result=e.service.start(self.command(e));self.assertEqual(result['connection_switch']['phase'],'connected')
            self.assertGreaterEqual(now[0],13.);self.assertLess(now[0],45.);self.assertEqual(len(started),1);self.assertEqual(len(opened),1)
            e.c.glove.stop_session_confirmed.assert_not_called();self.assertFalse(any(x['name']=='glove_follow' for x in e.calls))

    def test_saved_bootstrap_uses_only_remaining_overall_budget_and_blocks_late_open(self):
        with self.environment(ready_after=20.,prepare_cost=30.) as (e,now,opened,started,callback):
            with self.assertRaisesRegex(ValueError,'time budget expired'):e.service.start(self.command(e))
            self.assertGreaterEqual(now[0],45.);self.assertLess(now[0],45.1);self.assertEqual(opened,[]);self.assertEqual(len(started),1)
            with self.assertRaisesRegex(ValueError,'purpose switch changed'):callback[0]()
            self.assertEqual(opened,[]);self.assertFalse(e.service.state['busy'])

    def test_explicit_cancel_during_extended_startup_still_blocks_late_callback(self):
        with self.environment(cancel_at=5.) as (e,now,opened,started,callback):
            with self.assertRaisesRegex(ValueError,'cancellation requested'):e.service.start(self.command(e))
            self.assertLess(now[0],6.);self.assertEqual(opened,[]);self.assertEqual(len(started),1)
            with self.assertRaisesRegex(ValueError,'purpose switch changed'):callback[0]()
            self.assertEqual(opened,[]);self.assertFalse(e.service.state['busy'])

    def test_other_connection_modes_keep_the_twelve_second_local_wait(self):
        root=Path(os.environ.get('WUJI_CONTROL_QA_ROOT',tempfile.gettempdir()))/'startup-budget-0267';root.mkdir(parents=True,exist_ok=True)
        for mode in ('official','teleoperation','hand'):
            with self.subTest(mode=mode),patch.object(fixtures,'ROOT',root),fixtures.SwitchTests.environment(self,'teleoperation' if mode=='official' else 'official') as e:
                e.service._wait=Mock(wraps=e.service._wait)
                command=fixtures.SwitchTests.command(self,e,mode,fixtures.HAND if mode=='hand' else SN,**({'user_id':USER} if mode=='teleoperation' else {}))
                e.service.start(command);waits=[call for call in e.service._wait.call_args_list if call.args[2]=='New device connection was not confirmed']
                self.assertEqual(len(waits),1);self.assertEqual(waits[0].args[1],12)

if __name__=='__main__':unittest.main()
