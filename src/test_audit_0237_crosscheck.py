"""Independent owner recovery and cancel boundary checks; all state is fake."""
import copy
import threading
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock

from console_server import Controller as RealController
from task_sessions import TaskSessions,sdk_recovery
from task_view import scope,execute
import test_task_view as views
import test_task_sessions as sessions


class IndependentRecoveryTests(unittest.TestCase):
    def failed(self):
        state=views.idle()
        state['glove'].update(busy=True,connection='error',session_id=7,profile='hand2_right')
        state['calibrated_teleoperation']=dict(busy=False,phase='error',serial='WG-A',side='right',
            profile='Alice',sdk_session=7,run_id='a'*32,attempt_id='b'*32)
        return state

    def test_real_wearer_profile_shape_keeps_exact_failed_session_disconnect(self):
        state=self.failed();self.assertEqual(sdk_recovery(state),'WG-A')
        controller=views.Controller(state);ident=views.task('glove','WG-A')
        owner=scope(controller,ident);self.assertTrue(owner['current'])
        self.assertTrue(execute(controller,ident,owner['owner_context'],{'name':'glove_disconnect'})['ok'])
        self.assertEqual(controller.calls,[{'name':'glove_disconnect'}])

    def test_foreign_serial_new_session_and_history_never_grant_recovery(self):
        for key in ('foreign_serial','new_session','busy_false','worker_profile','wrong_side','conflicting_proof'):
            with self.subTest(key=key):
                state=self.failed()
                if key=='foreign_serial':state['glove']['glove_serial']='WG-OTHER'
                elif key=='new_session':state['glove']['session_id']=8
                elif key=='busy_false':state['glove']['busy']=False
                elif key=='worker_profile':state['glove']['profile']='hand1_right'
                elif key=='wrong_side':state['calibrated_teleoperation']['side']='left'
                else:state['teleoperation_connection']=dict(serial='WG-OTHER',side='right',
                    sdk_session=7,viewer_token='c'*32,attempt_id='d'*32)
                self.assertIsNone(sdk_recovery(state))
                self.assertFalse(scope(views.Controller(state),views.task('glove','WG-A'))['current'])

    def test_error_recovery_claim_cannot_hide_under_a_different_live_hand(self):
        state=sessions.hand()
        state['glove']=dict(busy=True,connection='error',session_id=4,glove_serial='GLOVE-FAILED',
            profile='hand2_left',user={'user_id':'u1'},hardware={'active':False},feedback={})
        sent=[];controller=N(action=lambda command:sent.append(command),glove=N(lease=None))
        registry=TaskSessions(lambda:[dict(id='main',origin='http://127.0.0.1:19000',state=copy.deepcopy(state))],
            lambda endpoint,context,action,payload:RealController._task_context_dispatch(controller,action,payload,state))
        tasks=registry.snapshot()['tasks'];self.assertTrue(tasks)
        for task in tasks:
            self.assertEqual(task['status'],'conflict')
            self.assertIsNot(task['capabilities'].get('can_disconnect'),True)
            with self.assertRaises(ValueError):
                registry.action(dict(task_id=task['task_id'],expected_revision=task['revision'],action='disconnect'))
        self.assertEqual(sent,[])
        self.assertFalse(scope(views.Controller(state),views.task('hand','HAND-A'))['current'])

    def test_exact_handover_cancel_remains_deliverable_under_its_worker_lock(self):
        state=views.idle();state['teleoperation_connection']=dict(busy=True,phase='stopping',
            serial='WG-A',side='right',viewer_token='a'*32,attempt_id='b'*32,sdk_session=None)
        controller=views.Controller(state);ident=views.task('glove','WG-A')
        context=scope(controller,ident)['owner_context']
        entered=threading.Event();release=threading.Event()
        def hold():
            with controller.calibration_handover.workflow_lock:entered.set();release.wait(2)
        worker=threading.Thread(target=hold);worker.start();self.assertTrue(entered.wait(1))
        try:
            command=dict(name='teleoperation_handover_cancel',viewer_token='a'*32,attempt_id='b'*32)
            for invalid in ({'name':'teleoperation_handover_cancel'},
                    {**command,'attempt_id':'bad'}, {**command,'viewer_token':True},
                    {**command,'serial':'WG-OTHER'}):
                with self.subTest(invalid=invalid),self.assertRaisesRegex(ValueError,'switching'):
                    execute(controller,ident,context,invalid)
            self.assertEqual(controller.calls,[])
            result=execute(controller,ident,context,command)
            self.assertTrue(result['ok']);self.assertNotIn('task_receipt',result)
            self.assertEqual(controller.calls,[command])
        finally:release.set();worker.join(2)

    def test_public_cancel_adapter_passes_only_exact_attempt_fields(self):
        service=Mock();continuation=Mock()
        controller=N(calibration_handover=N(),teleoperation_handover=service,
            calibrated_teleoperation=continuation)
        continuation.snapshot.return_value={'busy':False}
        for name,keys,target in (
                ('teleoperation_handover_cancel',{'viewer_token':'a'*32,'attempt_id':'b'*32},service),
                ('calibrated_teleoperation_cancel',{'run_id':'c'*32,'attempt_id':'d'*32},continuation)):
            with self.subTest(name=name):
                target.reset_mock()
                for invalid in ({'name':name},{'name':name,**keys,'serial':'FOREIGN'}):
                    with self.assertRaises(ValueError):RealController.action(controller,invalid)
                target.cancel.assert_not_called()
                RealController.action(controller,{'name':name,**keys})
                target.cancel.assert_called_once_with(keys)

    def test_receipt_uses_the_after_epoch_under_the_same_device_workflow_lock(self):
        state=self.failed();state['glove'].update(connection='receiving',glove_serial='WG-A',
            user={'user_id':'Alice'},stream={'fresh':True},can_prepare=True)
        state['calibrated_teleoperation']={}
        controller=views.Controller(state);ident=views.task('glove','WG-A')
        before=scope(controller,ident)['owner_context'];observations=[]
        snapshot=controller.snapshot
        def read():
            observations.append(controller.calibration_handover.workflow_lock._is_owned())
            return snapshot()
        controller.snapshot=read
        def action(command,*,_workflow_held=False):
            self.assertTrue(_workflow_held)
            self.assertTrue(controller.calibration_handover.workflow_lock._is_owned())
            state['glove']['session_id']=8
            return {'glove':copy.deepcopy(state['glove']),'task_receipt':{'forged':True}}
        controller.action=action
        result=execute(controller,ident,before,{'name':'glove_open','serial':'WG-A'})
        self.assertEqual(observations,[True,True])
        after=scope(controller,ident)['owner_context']
        self.assertNotEqual(before,after)
        self.assertEqual(result['task_receipt'],dict(task_id=ident,before_context=before,after_context=after))

    def test_explicit_domain_failure_cannot_receive_a_success_receipt(self):
        for result in ({'ok':False},{'error':'owned cleanup failed'}):
            with self.subTest(result=result):
                controller=views.Controller(sessions.hand('HAND-A'))
                ident=views.task('hand','HAND-A');context=scope(controller,ident)['owner_context']
                controller.action=Mock(return_value={**result,'task_receipt':{'forged':True}})
                self.assertNotIn('task_receipt',execute(controller,ident,context,{'name':'disconnect'}))


if __name__=='__main__':unittest.main()
