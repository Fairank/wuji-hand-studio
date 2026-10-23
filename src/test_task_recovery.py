"""Software-only cleanup ownership of a failed, still-running SDK worker."""
import copy
import hashlib
import threading
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock

from task_sessions import TaskSessions,sdk_recovery
from task_view import scope,execute


def state():
    return dict(connection='disconnected',hardware={'active':False},device_profile=dict(id='hand2_right',side='right'),
        glove=dict(busy=True,connection='error',session_id=7,profile='hand2_right',glove_serial='WG-A',
            user={'user_id':'Alice'},hardware={'active':False},feedback={}),calibration={},official_viz={})


class FailedSDKRecoveryTests(unittest.TestCase):
    def test_exact_worker_error_retains_disconnect_without_live_pair_or_reconnect(self):
        value=state();calls=[]
        service=TaskSessions(lambda:[dict(id='main',origin='http://127.0.0.1:18780',state=value)],
            lambda *args:calls.append(args),pair=lambda *args:None)
        row=service.snapshot()['tasks'][0]
        self.assertEqual(row['status'],'error');self.assertTrue(row['observed']['stale'])
        self.assertTrue(row['capabilities']['can_disconnect']);self.assertFalse(row['capabilities']['can_pair'])
        self.assertFalse(row['capabilities']['can_continue_teleoperation'])
        service.action(dict(task_id=row['task_id'],expected_revision=row['revision'],action='disconnect'))
        self.assertEqual(calls[0][2],'disconnect');self.assertEqual(len(calls),1)

    def test_recovery_scope_allows_only_original_stop_and_disconnect(self):
        value=state();calls=[]
        controller=N(snapshot=lambda:copy.deepcopy(value),calibration_handover=N(workflow_lock=threading.RLock()),
            action=lambda command:calls.append(command) or {'ok':True})
        ident=hashlib.sha256(b'glove:WG-A').hexdigest()[:24];current=scope(controller,ident)
        self.assertTrue(current['current'])
        for name in ('glove_follow','glove_open','glove_prepare','glove_keepalive','calibration_start'):
            with self.subTest(name=name),self.assertRaises(ValueError):execute(controller,ident,current['owner_context'],{'name':name})
        execute(controller,ident,current['owner_context'],{'name':'glove_disconnect'})
        self.assertEqual(calls,[{'name':'glove_disconnect'}])
        value['glove']['session_id']=8
        with self.assertRaises(ValueError):execute(controller,ident,current['owner_context'],{'name':'glove_disconnect'})

    def test_historical_draft_or_conflicting_profile_never_becomes_recovery_owner(self):
        for changed in ({'busy':False},{'connection':'disconnected'},{'profile':'hand2_left'},
                {'session_id':True},{'session_id':None},{'glove_serial':None},{'user':{}}):
            value=state();value['glove'].update(changed)
            with self.subTest(changed=changed):self.assertIsNone(sdk_recovery(value))

    def test_exact_server_attempt_can_recover_failure_before_glove_open(self):
        for name,extra in (('teleoperation_connection',{'viewer_token':'a'*32}),
                ('calibrated_teleoperation',{'run_id':'c'*32})):
            value=state();value['glove'].pop('glove_serial');value['glove'].pop('user')
            value[name]=dict(serial='WG-A',side='right',profile='Alice',sdk_session=7,attempt_id='b'*32,**extra)
            self.assertEqual(sdk_recovery(value),'WG-A')
            for changed in ({'sdk_session':8},{'sdk_session':True},{'attempt_id':'old'},{'side':'left'}):
                other=copy.deepcopy(value);other[name].update(changed)
                with self.subTest(name=name,changed=changed):self.assertIsNone(sdk_recovery(other))
            value['glove']['glove_serial']='WG-B'
            self.assertIsNone(sdk_recovery(value))

    def test_public_cancel_requires_exact_server_attempt_without_name_only_fallback(self):
        from console_server import Controller
        controller=Controller.__new__(Controller);controller.calibrated_teleoperation=None
        controller.calibration_handover=N();controller.teleoperation_handover=N(cancel=Mock(return_value={'cancelled':True}))
        for command in ({'name':'teleoperation_handover_cancel'},
                {'name':'teleoperation_handover_cancel','viewer_token':'a'*32},
                {'name':'teleoperation_handover_cancel','viewer_token':'a'*32,'attempt_id':'b'*32,'serial':'WG-A'}):
            with self.subTest(command=command),self.assertRaises(ValueError):controller.action(command)
        controller.teleoperation_handover.cancel.assert_not_called()
        self.assertTrue(controller.action(dict(name='teleoperation_handover_cancel',viewer_token='a'*32,attempt_id='b'*32))['cancelled'])
        controller.teleoperation_handover.cancel.assert_called_once_with(dict(viewer_token='a'*32,attempt_id='b'*32))


if __name__=='__main__':unittest.main()
