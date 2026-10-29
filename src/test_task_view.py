"""Embedded task ownership, using cached synthetic controller states only."""
import copy
import hashlib
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock

from task_view import scope,execute


def task(kind,number):return hashlib.sha256((kind+':'+number).encode()).hexdigest()[:24]


def idle():
    return dict(connection='disconnected',hardware={'active':False},session_generation=1,
        device_profile=dict(id='hand2_right',side='right',generation='hand2'),
        glove=dict(busy=False,connection='disconnected',hardware={},feedback={}),official_viz={},calibration={})


class Controller:
    def __init__(self,state):
        self.state=state;self.calibration_handover=N(workflow_lock=threading.RLock());self.calls=[]
        self.after_snapshot=None
    def snapshot(self):
        result=copy.deepcopy(self.state)
        if self.after_snapshot:self.after_snapshot()
        return result
    def action(self,command):
        if command['name']=='calibrated_teleoperation_cancel':
            current=self.state.get('calibrated_teleoperation') or {}
            if any(command[k]!=current.get(k) for k in ('run_id','attempt_id')):
                raise ValueError('Continuation attempt changed')
        self.calls.append(copy.deepcopy(command));return {'ok':True}


class TaskViewTests(unittest.TestCase):
    def hand(self,number='WH-A'):
        s=idle();s.update(connection='connected',device_id=number,
            identified_device=dict(source='sdk_identity',serial=number,profile='hand2_right'))
        return s
    def glove(self,number='WG-A'):
        s=idle();s['glove'].update(busy=True,connection='receiving',session_id=7,glove_serial=number,
            profile='hand2_right',user={'user_id':'Alice'},stream={'fresh':True},can_prepare=True)
        return s
    def test_sdk_hand_and_glove_ids_match_serial_hash_in_a_local_child(self):
        for kind,state,number in [('hand',self.hand(),'WH-A'),('glove',self.glove(),'WG-A')]:
            with self.subTest(kind=kind):
                c=Controller(state);value=scope(c,task(kind,number))
                self.assertTrue(value['current']);self.assertRegex(value['owner_context'],r'^[a-f0-9]{64}$')
                self.assertEqual(c.calls,[])
                c.state=self.hand('WH-B') if kind=='hand' else self.glove('WG-B')
                self.assertEqual(scope(c,task(kind,number)),dict(task_id=task(kind,number),current=False,owner_context=None))

    def test_native_viewer_calibration_and_continuation_keep_the_exact_glove_task(self):
        variants=[]
        s=idle();s['official_viz']=dict(backend='windows_native',active=True,phase='running',session_id='v'*32,
            reported_device=dict(kind='glove',sn='WG-A',side='right'));variants.append(s)
        s=idle();s['calibration']=dict(id='a'*32,serial='WG-A',source='official_jsonl_native',running=True,cleanup_confirmed=False);variants.append(s)
        s=idle();s['calibrated_teleoperation']=dict(busy=True,attempt_id='b'*32,run_id='a'*32,serial='WG-A',side='right');variants.append(s)
        s=idle();s['calibrated_teleoperation']=dict(can_continue=True,serial='WG-A',side='right',run_id='a'*32);variants.append(s)
        for state in variants:
            with self.subTest(state=state):
                c=Controller(state);self.assertTrue(scope(c,task('glove','WG-A'))['current'])
                self.assertFalse(scope(c,task('glove','WG-B'))['current'])

    def test_handover_release_gap_and_owned_sdk_selection_session_are_current(self):
        s=idle();s['calibration_connection']=dict(busy=True,serial='WG-A')
        self.assertTrue(scope(Controller(s),task('glove','WG-A'))['current'])
        s=idle();s['glove'].update(busy=True,connection='ready',session_id=8)
        s['teleoperation_connection']=dict(busy=False,phase='select_user',serial='WG-A',side='right',
            viewer_token='a'*32,attempt_id='b'*32,sdk_session=8)
        c=Controller(s);self.assertTrue(scope(c,task('glove','WG-A'))['current'])
        c.state['glove']['session_id']=9;self.assertFalse(scope(c,task('glove','WG-A'))['current'])

    def test_overlapping_or_unverified_owners_fail_closed(self):
        states=[]
        s=self.hand();s['glove']=self.glove()['glove'];states.append(s)
        s=self.hand();s['identified_device']['serial']='WH-OTHER';states.append(s)
        s=idle();s['glove'].update(busy=True,connection='connecting');s['calibrated_teleoperation']=dict(can_continue=True,serial='WG-A',side='right');states.append(s)
        s=self.glove();s['calibration_connection']=dict(busy=True,serial='WG-B');states.append(s)
        s=idle();s['calibration_connection']=dict(busy=True);states.append(s)
        for state in states:
            with self.subTest(state=state):
                self.assertFalse(scope(Controller(state),task('glove','WG-A'))['current'])
                self.assertFalse(scope(Controller(state),task('hand','WH-A'))['current'])

    def test_all_old_page_commands_including_stop_and_keepalive_reject_replacement(self):
        c=Controller(self.glove());ident=task('glove','WG-A');context=scope(c,ident)['owner_context']
        c.state=self.glove('WG-B')
        for name in ('glove_follow','glove_stop','glove_disconnect','glove_keepalive','hardware_stop','session_close'):
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'changed'):
                execute(c,ident,context,{'name':name})
        self.assertEqual(c.calls,[])

    def test_same_serial_new_session_and_profile_change_invalidate_old_context(self):
        for change in ('session','profile'):
            with self.subTest(change=change):
                c=Controller(self.glove());ident=task('glove','WG-A');context=scope(c,ident)['owner_context']
                if change=='session':c.state['glove']['session_id']=8
                else:c.state['device_profile']['id']='hand1_right'
                with self.assertRaisesRegex(ValueError,'changed'):execute(c,ident,context,{'name':'glove_stop'})
                self.assertEqual(c.calls,[])

    def test_same_glove_new_handover_token_invalidates_old_release_gap_context(self):
        s=idle();s['calibration_connection']=dict(busy=True,serial='WG-A',viewer_token='a'*32)
        c=Controller(s);ident=task('glove','WG-A');context=scope(c,ident)['owner_context']
        c.state['calibration_connection']['viewer_token']='b'*32
        self.assertTrue(scope(c,ident)['current'])
        with self.assertRaisesRegex(ValueError,'changed'):
            execute(c,ident,context,{'name':'calibration_cancel'})
        self.assertEqual(c.calls,[])

    def test_valid_command_dispatch_is_under_workflow_lock_without_rewriting_payload(self):
        c=Controller(self.hand());ident=task('hand','WH-A');context=scope(c,ident)['owner_context']
        def action(command):
            self.assertTrue(c.calibration_handover.workflow_lock._is_owned());return copy.deepcopy(command)
        c.action=action;command={'name':'hardware_keepalive','lease':'synthetic'}
        result=execute(c,ident,context,command)
        self.assertEqual({k:v for k,v in result.items() if k!='task_receipt'},command)

    def test_task_cannot_select_another_device_or_use_group_root_routes(self):
        c=Controller(self.glove());ident=task('glove','WG-A');context=scope(c,ident)['owner_context']
        commands=[{'name':'glove_open','serial':'WG-B'}, {'name':'official_viz_start','serial':'WG-B'},
            {'name':'connect'}, {'name':'retarget_binding_save','binding':{'glove_serial':'WG-B'}},
            {'name':'fleet_stop'}, {'name':'ensemble_stop','workspace':'main'}]
        for command in commands:
            with self.subTest(command=command),self.assertRaises(ValueError):execute(c,ident,context,command)
        self.assertEqual(c.calls,[])

    def test_receipt_binds_only_a_successful_same_task_owner_transition(self):
        c=Controller(self.glove());ident=task('glove','WG-A');before=scope(c,ident)['owner_context']
        def action(command,*,_workflow_held=False):
            self.assertTrue(_workflow_held)
            c.state['glove']['session_id']=8
            return dict(calibration_connection={'busy':False},task_receipt={'forged':True})
        c.action=action
        result=execute(c,ident,before,{'name':'glove_open','serial':'WG-A'})
        after=scope(c,ident)['owner_context']
        self.assertNotEqual(before,after)
        self.assertEqual(result['task_receipt'],dict(task_id=ident,before_context=before,after_context=after))
        c.state['glove']['session_id']=9
        self.assertNotEqual(result['task_receipt']['after_context'],scope(c,ident)['owner_context'])

    def test_failed_action_or_different_resource_has_no_transition_receipt(self):
        for failed,replace in ((True,False),(False,True)):
            with self.subTest(failed=failed,replace=replace):
                c=Controller(self.glove());ident=task('glove','WG-A');before=scope(c,ident)['owner_context']
                def action(command):
                    if replace:c.state=self.glove('WG-B')
                    return dict(ok=not failed,task_receipt={'forged':True})
                c.action=action
                self.assertNotIn('task_receipt',execute(c,ident,before,{'name':'glove_disconnect'}))

    def test_same_viewer_new_teleoperation_attempt_invalidates_old_context(self):
        s=self.glove();s['teleoperation_connection']=dict(serial='WG-A',viewer_token='a'*32,attempt_id='b'*32,sdk_session=7)
        c=Controller(s);ident=task('glove','WG-A');before=scope(c,ident)['owner_context']
        c.state['teleoperation_connection']['attempt_id']='c'*32
        with self.assertRaisesRegex(ValueError,'changed'):
            execute(c,ident,before,{'name':'teleoperation_handover_cancel','viewer_token':'a'*32,'attempt_id':'b'*32})
        self.assertEqual(c.calls,[])

    def test_exact_attempt_cancel_fast_path_does_not_wait_for_worker_workflow_lock(self):
        s=idle();s['calibrated_teleoperation']=dict(busy=True,serial='WG-A',side='right',run_id='a'*32,attempt_id='b'*32)
        c=Controller(s);ident=task('glove','WG-A');context=scope(c,ident)['owner_context']
        acquired=threading.Event();release=threading.Event()
        def hold():
            with c.calibration_handover.workflow_lock:acquired.set();release.wait(2)
        thread=threading.Thread(target=hold);thread.start();self.assertTrue(acquired.wait(1))
        try:
            with self.assertRaisesRegex(ValueError,'switching'):execute(c,ident,context,{'name':'glove_stop'})
            command=dict(name='calibrated_teleoperation_cancel',run_id='a'*32,attempt_id='b'*32)
            result=execute(c,ident,context,command)
            self.assertTrue(result['ok']);self.assertNotIn('task_receipt',result)
            c.after_snapshot=lambda:c.state['calibrated_teleoperation'].update(attempt_id='c'*32)
            with self.assertRaisesRegex(ValueError,'attempt changed'):execute(c,ident,context,command)
            self.assertEqual(c.calls,[command])
        finally:release.set();thread.join(2)

    def group(self):
        return dict(task_id=task('ensemble','main'),kind='ensemble',revision=1,status='running',
            intent=dict(group_id='main',members=['main','child']),devices=dict(hands=['WH-A','WH-B']))

    def test_ensemble_scope_and_dispatch_never_fall_back_to_root_actor(self):
        c=Controller(idle());group=self.group();read=lambda:[copy.deepcopy(group)];dispatch=Mock(return_value={'ok':True})
        lock=threading.RLock()
        ident=group['task_id'];context=scope(c,ident,read_groups=read)['owner_context']
        command=dict(name='ensemble_stop',workspace='main')
        self.assertTrue(execute(c,ident,context,command,read_groups=read,group_action=dispatch,group_lock=lock)['ok'])
        dispatch.assert_called_once_with(command);self.assertEqual(c.calls,[])
        for command in ({'name':'hardware_stop'},{'name':'fleet_stop'}, {'name':'ensemble_stop','workspace':'a'*16},
                        {'name':'workspace_camera','workspace':'main','member':'other'}):
            with self.subTest(command=command),self.assertRaises(ValueError):
                execute(c,ident,context,command,read_groups=read,group_action=dispatch,group_lock=lock)
        group['revision']=2
        with self.assertRaisesRegex(ValueError,'changed'):
            execute(c,ident,context,{'name':'ensemble_stop','workspace':'main'},read_groups=read,group_action=dispatch,group_lock=lock)
        self.assertEqual(dispatch.call_count,1);self.assertEqual(c.calls,[])

    def test_device_never_reads_groups_and_ensemble_keeps_catalog_before_workflow_order(self):
        c=Controller(self.hand());ident=task('hand','WH-A');context=scope(c,ident)['owner_context']
        read=Mock(side_effect=AssertionError('Device task must not read group registry'))
        execute(c,ident,context,{'name':'hardware_stop'},read_groups=read,group_action=Mock(),group_lock=threading.RLock())
        read.assert_not_called()
        c=Controller(idle());group=self.group();lock=threading.RLock()
        def read():
            self.assertTrue(lock._is_owned());self.assertFalse(c.calibration_handover.workflow_lock._is_owned())
            return [group]
        context=scope(c,group['task_id'],read_groups=lambda:[group])['owner_context']
        def dispatch(command):
            self.assertTrue(lock._is_owned());self.assertFalse(c.calibration_handover.workflow_lock._is_owned())
            return {'ok':True}
        execute(c,group['task_id'],context,{'name':'ensemble_stop','workspace':'main'},read_groups=read,group_action=dispatch,group_lock=lock)
        self.assertEqual(c.calls,[])
        with self.assertRaises(ValueError):
            execute(c,group['task_id'],context,{'name':'ensemble_stop','workspace':'main'},read_groups=read,group_action=dispatch)

    def test_invalid_duplicate_or_conflicting_group_metadata_is_not_current(self):
        c=Controller(idle());group=self.group();ident=group['task_id']
        self.assertFalse(scope(c,ident,read_groups=lambda:[group,group])['current'])
        for changed in ({'revision':True},{'status':'conflict'},{'devices':{'hands':['WH-A','WH-A']}},
                        {'intent':{'group_id':'main','members':['main','main']}}):
            with self.subTest(changed=changed):
                self.assertFalse(scope(c,ident,read_groups=lambda:[{**group,**changed}])['current'])


if __name__=='__main__':unittest.main()
