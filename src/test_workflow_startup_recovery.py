"""Composite workflow startup recovery; all transports/threads are software fakes."""
from concurrent.futures import Future
from contextlib import nullcontext
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from calibrated_teleoperation import CalibratedTeleoperation
from group_coordinator import GroupCoordinator
from workspace_groups import Discovery
from test_calibration_portability import completed,MODEL
import test_task_pairing as pairing_fixtures


class ImmediateThread:
    def __init__(self,target=None,args=(),**_):self.target=target;self.args=args
    def start(self):self.target(*self.args)
    def is_alive(self):return False
    def join(self,timeout=None):pass


class FailedThread(ImmediateThread):
    def start(self):raise RuntimeError('synthetic startup unavailable')


def failure(kind):
    return dict(side_effect=RuntimeError('synthetic constructor unavailable')) if kind=='construct' else dict(side_effect=FailedThread)


class InlinePool:
    def map(self,function,items):return list(map(function,items))
    def submit(self,function,*args,**kwargs):
        future=Future()
        try:future.set_result(function(*args,**kwargs))
        except BaseException as error:future.set_exception(error)
        return future


class GroupStartupRecoveryTests(unittest.TestCase):
    def environment(self):
        states={key:dict(device_profile={'side':side},group={},hardware={'active':False})
                for key,side in [('left','left'),('right','right')]}
        calls=[]
        def post(key,command):
            calls.append((key,command['name']))
            group=states[key]['group'];name=command['name']
            if name=='group_prepare':group.update(token=command['token'],phase='preparing',active=True)
            elif name=='group_arm':group.update(phase='ready',clock_s=0.)
            elif name=='group_commit':group.update(phase='completed',active=False,elapsed_s=0.)
            return {'ok':True}
        service=GroupCoordinator(lambda key:copy.deepcopy(states[key]),post)
        service.pool.shutdown(wait=True);service.pool=InlinePool()
        from bimanual_program import catalog
        row=next(r for r in catalog() if r['kind']=='same')
        command=dict(members=['left','right'],mode='preview',kind='same',action=row['id'],speed=1.,cycles=1,amplitude=.25)
        return service,command,calls

    def test_construct_and_start_failure_drop_only_new_local_lease_then_retry(self):
        for kind in ('construct','start'):
            with self.subTest(kind=kind):
                service,command,calls=self.environment()
                with patch('group_coordinator.threading.Thread',ImmediateThread),patch('performance_program.program',return_value={'duration_s':0.}):
                    old=service.start(command)['group_lease']
                    self.assertEqual(service.snapshot()['phase'],'completed')
                calls.clear()
                with patch('group_coordinator.threading.Thread',**failure(kind)),self.assertRaisesRegex(RuntimeError,'unavailable'):
                    service.start(command)
                self.assertFalse(service.snapshot()['active']);self.assertEqual(service.snapshot()['phase'],'failed')
                self.assertIsNone(service.token);self.assertIsNone(service.thread);self.assertEqual(calls,[])
                with self.assertRaisesRegex(ValueError,'Expired'):service.beat(old)
                with patch('group_coordinator.threading.Thread',ImmediateThread),patch('performance_program.program',return_value={'duration_s':0.}):
                    new=service.start(command)['group_lease']
                self.assertNotEqual(old,new);self.assertEqual(service.snapshot()['phase'],'completed')

    def test_already_active_group_rejection_preserves_original_lease_and_thread(self):
        service,command,calls=self.environment();original=object()
        service.state.update(active=True,phase='playing');service.token='a'*32;service.thread=original
        with patch('group_coordinator.threading.Thread') as spawn,self.assertRaisesRegex(ValueError,'already active'):
            service.start(command)
        spawn.assert_not_called();self.assertEqual(service.token,'a'*32);self.assertIs(service.thread,original)
        self.assertEqual(calls,[]);self.assertTrue(service.snapshot()['active'])


class PairingStartupRecoveryTests(unittest.TestCase):
    def test_construct_and_start_failure_preserve_endpoints_and_allow_exact_retry(self):
        for kind in ('construct','start'):
            with self.subTest(kind=kind):
                host,server,source,target=pairing_fixtures.TaskPairingTests().fixture();service=server.pairing
                before={k:copy.deepcopy(c.state) for k,c in host._fleet.controllers.items()}
                with patch('task_pairing.threading.Thread',**failure(kind)),self.assertRaisesRegex(RuntimeError,'unavailable'):
                    service.start(source,target)
                self.assertFalse(service.snapshot()['busy']);self.assertIsNone(service.thread)
                self.assertFalse(service.snapshot()['release_requested']);self.assertFalse(service.snapshot()['released_hand'])
                for key,controller in host._fleet.controllers.items():
                    self.assertEqual(controller.calls,[]);self.assertEqual(controller.state,before[key])
                with patch('task_pairing.threading.Thread',ImmediateThread):service.start(source,target)
                self.assertEqual(service.snapshot()['phase'],'paired');self.assertFalse(service.snapshot()['busy'])
                with self.assertRaisesRegex(ValueError,'session changed'):service.start(source,target)

    def test_existing_pending_pairing_is_never_cleared_by_duplicate_start(self):
        host,server,source,target=pairing_fixtures.TaskPairingTests().fixture();service=server.pairing
        service.state.update(busy=True,phase='releasing_feedback',run_id='a'*32)
        before=service.snapshot();original=object();service.thread=original
        with patch('task_pairing.threading.Thread') as spawn,self.assertRaisesRegex(ValueError,'pairing is in progress'):
            service.start(source,target)
        spawn.assert_not_called();self.assertEqual(service.snapshot(),before);self.assertIs(service.thread,original)
        self.assertEqual(host._fleet.controllers['source'].calls,[]);self.assertEqual(host._fleet.controllers['target'].calls,[])


class ContinuationStartupRecoveryTests(unittest.TestCase):
    def test_success_duplicate_construct_or_start_failure_cancel_then_retry(self):
        for kind in ('construct','start'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                model=Path(folder)/'right_hand.urdf';model.write_bytes(MODEL)
                calibration=completed(model);glove_state={'session_id':17,'connection':'connecting','busy':True}
                def start(_):
                    glove_state.update(session_id=glove_state['session_id']+1,connection='connecting',busy=True)
                    return glove_state['session_id']
                spawn=Mock(side_effect=start);stop=Mock();export=Mock(side_effect=lambda proof,**_:proof)
                service=CalibratedTeleoperation(lambda:copy.deepcopy(calibration),spawn,stop,
                    lambda:copy.deepcopy(glove_state),Mock(),export,gate_factory=nullcontext)
                offered=service.snapshot();command={key:offered[key] for key in ('run_id','expected_revision')}
                service.execute(command,synchronous=True);bundle=spawn.call_args.args[0]
                glove_state.update(connection='receiving',glove_serial=bundle['serial'],model_provenance=bundle)
                before=copy.deepcopy(service.state)
                with patch('calibrated_teleoperation.threading.Thread') as worker,self.assertRaisesRegex(ValueError,'session has not ended'):
                    service.execute(command)
                worker.assert_not_called();self.assertEqual(service.state,before);stop.assert_not_called()
                glove_state.update(connection='disconnected',busy=False);spawn.reset_mock();export.reset_mock()
                with patch('calibrated_teleoperation.threading.Thread',**failure(kind)),self.assertRaisesRegex(RuntimeError,'unavailable'):
                    service.execute(command)
                result=service.snapshot();self.assertFalse(result['busy']);self.assertTrue(result['can_continue'])
                self.assertIsNone(result['sdk_session']);self.assertIsNone(service.thread)
                self.assertTrue(service.cancel_and_wait(.001));self.assertFalse(service.snapshot()['busy'])
                spawn.assert_not_called();stop.assert_not_called();export.assert_not_called()
                with patch('calibrated_teleoperation.threading.Thread',ImmediateThread):service.execute(command)
                spawn.assert_called_once();export.assert_called_once();self.assertIsNotNone(service.snapshot()['sdk_session'])


class DiscoveryStartupRecoveryTests(unittest.TestCase):
    def test_success_repeat_construct_or_start_failure_then_retry(self):
        for kind in ('construct','start'):
            with self.subTest(kind=kind):
                client=Mock();client.agent_command='synthetic-never-executed'
                client.exec_command.side_effect=lambda *_a,**_k:(io.StringIO(),io.StringIO('WUJI_JSON:{"type":"discovery","devices":[]}\n'),io.StringIO())
                factory=Mock(return_value=client);service=Discovery(factory)
                with patch('workspace_groups.threading.Thread',ImmediateThread),patch('workspace_groups.threading.Timer'):
                    for _ in range(2):self.assertFalse(service.start()['running'])
                factory.reset_mock();client.exec_command.reset_mock()
                with patch('workspace_groups.threading.Thread',**failure(kind)),self.assertRaisesRegex(RuntimeError,'unavailable'):
                    service.start()
                self.assertFalse(service.snapshot()['running']);self.assertIn('unavailable',service.snapshot()['error'])
                factory.assert_not_called();client.exec_command.assert_not_called()
                with patch('workspace_groups.threading.Thread',ImmediateThread),patch('workspace_groups.threading.Timer'):
                    result=service.start()
                self.assertFalse(result['running']);self.assertEqual(result['error'],'');factory.assert_called_once()


if __name__=='__main__':unittest.main()
