"""Pending Viewer-to-SDK task ownership; synthetic controllers only."""
import copy
import io
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock

from console_server import Controller
from task_control import execute as dispatch
from task_sessions import TaskSessions,context_digest,pending_teleoperation
from task_view import scope
import test_task_view as views
import test_teleoperation_handover as handovers


class PendingHandoverTaskTests(unittest.TestCase):
    def pending(self,ready=False):
        state=views.idle()
        state['teleoperation_connection']=dict(busy=True,phase='starting',serial='WG-A',side='right',
            viewer_token='a'*32,attempt_id='b'*32,sdk_session=None,viewer_released=True)
        if ready:
            state['teleoperation_connection'].update(busy=False,phase='select_user',sdk_session=7)
            state['glove'].update(busy=True,connection='ready',session_id=7,profile='hand2_right')
        return state

    def registry(self,state,send=None):
        return TaskSessions(lambda:[dict(id='main',origin='http://127.0.0.1:19000',state=copy.deepcopy(state))],
            send or Mock(return_value={'ok':True}))

    def test_released_viewer_gap_keeps_the_same_connecting_task_and_scope(self):
        state=self.pending();pending=copy.deepcopy(state)
        state['teleoperation_connection']={}
        state['official_viz']=dict(backend='windows_native',active=True,phase='running',session_id='a'*32,
            reported_device=dict(sn='WG-A',kind='glove',side='right'))
        service=self.registry(state);old=service.snapshot()['tasks'][0]
        state.clear();state.update(pending)
        row=service.snapshot()['tasks'][0]
        self.assertEqual(row['task_id'],old['task_id']);self.assertEqual(row['status'],'connecting')
        self.assertEqual(row['view']['page'],'glove')
        self.assertTrue(row['capabilities']['can_disconnect']);self.assertFalse(row['capabilities']['can_pair'])
        self.assertTrue(scope(views.Controller(state),row['task_id'])['current'])

    def test_matching_live_viewer_is_one_connecting_resource_but_other_viewer_conflicts(self):
        for number in ('WG-A','WG-B'):
            with self.subTest(number=number):
                state=self.pending();state['teleoperation_connection']['phase']='stopping'
                state['official_viz']=dict(backend='windows_native',active=True,phase='running',session_id='a'*32,
                    reported_device=dict(sn=number,kind='glove',side='right'))
                rows=self.registry(state).snapshot()['tasks']
                self.assertEqual(len(rows),1 if number=='WG-A' else 2)
                if number=='WG-A':
                    self.assertEqual(rows[0]['status'],'connecting');self.assertEqual(rows[0]['view']['page'],'glove')
                else:
                    self.assertTrue(all(row['status']=='conflict' for row in rows))
                    self.assertFalse(scope(views.Controller(state),views.task('glove','WG-A'))['current'])

    def test_ready_and_connecting_sdk_retain_only_the_exact_current_session(self):
        for connection in ('ready','connecting'):
            with self.subTest(connection=connection):
                state=self.pending(True);state['glove']['connection']=connection
                if connection=='connecting':state['teleoperation_connection'].update(busy=True,phase='starting')
                row=self.registry(state).snapshot()['tasks'][0]
                self.assertEqual(row['status'],'connecting');self.assertEqual(row['observed']['session'],7)
                self.assertEqual(row['view']['page'],'glove');self.assertTrue(row['capabilities']['can_disconnect'])
                self.assertTrue(scope(views.Controller(state),row['task_id'])['current'])

    def test_invalid_gap_proofs_do_not_create_an_owner(self):
        for key,value in (('viewer_token','bad'),('attempt_id',True),('side','left'),
                          ('serial','bad serial'),('busy',False),('phase','error')):
            with self.subTest(key=key):
                state=self.pending();state['teleoperation_connection'][key]=value
                self.assertIsNone(pending_teleoperation(state));self.assertEqual(self.registry(state).snapshot()['tasks'],[])
                self.assertFalse(scope(views.Controller(state),views.task('glove','WG-A'))['current'])

    def test_ready_history_replacement_and_foreign_worker_cannot_reuse_the_old_task(self):
        for change in ('new_sid','busy_false','foreign_sn','profile','side','hardware','feedback'):
            with self.subTest(change=change):
                state=self.pending(True)
                if change=='new_sid':state['glove']['session_id']=8
                elif change=='busy_false':state['glove']['busy']=False
                elif change=='foreign_sn':state['glove']['glove_serial']='WG-B'
                elif change=='profile':state['glove']['profile']='hand1_right'
                elif change=='side':state['teleoperation_connection']['side']='left'
                elif change=='hardware':state['glove']['hardware']={'active':True}
                else:state['glove']['feedback']={'device_id':'HAND'}
                self.assertIsNone(pending_teleoperation(state))
                self.assertEqual(self.registry(state).snapshot()['tasks'],[])
                self.assertFalse(scope(views.Controller(state),views.task('glove','WG-A'))['current'])

    def test_task_disconnect_reaches_exact_cancel_while_worker_holds_workflow_lock(self):
        for ready in (False,True):
            with self.subTest(ready=ready):
                state=self.pending(ready);sent=[]
                controller=N(snapshot=lambda:copy.deepcopy(state),glove=N(lease=None),hardware_lease=None,
                    calibration_handover=N(workflow_lock=threading.RLock()),action=lambda command:sent.append(command) or {'ok':True})
                controller._task_context_dispatch=lambda action,payload,captured:Controller._task_context_dispatch(controller,action,payload,captured)
                service=self.registry(state,lambda endpoint,context,action,payload:dispatch(controller,context,action,payload))
                row=service.snapshot()['tasks'][0];entered=threading.Event();release=threading.Event()
                def hold():
                    with controller.calibration_handover.workflow_lock:entered.set();release.wait(2)
                worker=threading.Thread(target=hold);worker.start();self.assertTrue(entered.wait(1))
                try:service.action(dict(task_id=row['task_id'],expected_revision=row['revision'],action='disconnect'))
                finally:release.set();worker.join(2)
                self.assertEqual(sent,[dict(name='teleoperation_handover_cancel',viewer_token='a'*32,attempt_id='b'*32)])

    def test_new_session_or_serial_invalidates_previously_rendered_task_disconnect(self):
        for change in ('session','serial','attempt'):
            with self.subTest(change=change):
                state=self.pending(True);send=Mock();service=self.registry(state,send)
                row=service.snapshot()['tasks'][0]
                context=context_digest(state)
                if change=='session':state['glove']['session_id']=8
                elif change=='serial':state['glove']['glove_serial']='WG-B'
                else:state['teleoperation_connection']['attempt_id']='c'*32
                with self.assertRaises(ValueError):service.action(dict(task_id=row['task_id'],expected_revision=row['revision'],action='disconnect'))
                send.assert_not_called()
                controller=N(snapshot=lambda:copy.deepcopy(state),_task_context_dispatch=Mock(),
                    calibration_handover=N(workflow_lock=threading.RLock()))
                with self.assertRaisesRegex(ValueError,'session changed'):dispatch(controller,context,'disconnect')
                controller._task_context_dispatch.assert_not_called()

    def test_late_error_replacement_or_open_does_not_let_pending_cancel_clear_a_new_owner(self):
        for change in ('error','session','attempt','preview'):
            with self.subTest(change=change),handovers.TeleoperationTests().environment() as env:
                env.teleoperation.start(handovers.TeleoperationTests().command())
                env.glove.stdin=io.StringIO()
                captured=dict(device_profile=copy.deepcopy(env.controller.state['device_profile']),
                    glove=env.glove.snapshot(),teleoperation_connection=env.teleoperation.snapshot())
                self.assertIsNotNone(pending_teleoperation(captured))
                adapter=N(calibration_handover=env.controller.calibration_handover,
                    calibrated_teleoperation=None,teleoperation_handover=env.teleoperation)
                adapter.action=lambda command:Controller.action(adapter,command)
                if change=='error':env.glove.state.update(connection='error',message='late current error')
                elif change=='session':env.glove.session+=1;env.glove.state.update(connection='error',message='new owner error')
                elif change=='attempt':env.teleoperation.state['attempt_id']='c'*32
                else:env.glove.state['connection']='receiving'
                before=copy.deepcopy(env.glove.state)
                with self.assertRaises(ValueError):Controller._task_context_dispatch(adapter,'disconnect',{},captured)
                self.assertEqual(env.glove.state,before)
                self.assertFalse(env.teleoperation._cancel);self.assertFalse(env.glove.stopping)
                self.assertEqual(env.glove.stdin.getvalue(),'')

    def test_error_and_completed_preview_use_current_session_disconnect_not_old_cancel(self):
        for connection in ('error','receiving'):
            with self.subTest(connection=connection):
                state=self.pending(True);state['glove'].update(connection=connection,glove_serial='WG-A',
                    user={'user_id':'Alice'},stream={'fresh':True},can_prepare=True,hardware={'active':False})
                state['teleoperation_connection']['phase']='error' if connection=='error' else 'preview'
                self.assertIsNone(pending_teleoperation(state));row=self.registry(state).snapshot()['tasks'][0]
                self.assertEqual(row['status'],'error' if connection=='error' else 'preview')
                sent=[];controller=N(action=lambda command:sent.append(command),glove=N(lease=None),hardware_lease=None)
                Controller._task_context_dispatch(controller,'disconnect',{},state)
                self.assertEqual(sent,[{'name':'glove_disconnect'}])
                self.assertTrue(scope(views.Controller(state),row['task_id'])['current'])

    def test_following_still_requires_current_hand_stop_and_never_uses_old_cancel(self):
        state=self.pending(True);state['glove'].update(connection='receiving',glove_serial='WG-A',
            hardware={'active':True},feedback={'device_id':'HAND'})
        controller=N(action=Mock(),glove=N(lease=None),hardware_lease=None)
        with self.assertRaisesRegex(ValueError,'Pause'):Controller._task_context_dispatch(controller,'disconnect',{},state)
        controller.action.assert_not_called()


if __name__=='__main__':unittest.main()
