"""Single-task playback controls with fake transport/clocks, no devices/physics."""
import copy
import tempfile
import threading
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock, patch

from demo_player import DemoPlayer
from program_runner import ProgramRunner, ProgramEntryPaused
from task_control import execute
from task_sessions import TaskSessions, context_digest, hand_playback
from test_task_sessions import hand
import test_task_control as control_fixtures
import test_group_control as group_fixtures
from test_hardware_showcase import feedback, diagnostics


class SingleTaskCapabilitiesTests(unittest.TestCase):
    def state(self):
        state=hand();state['hardware_control_id']='a'*64
        state['hardware'].update(active=True,paused=False,phase='playing',source='supervised_low_current_trial')
        return state

    def registry(self,state):
        calls=[]
        service=TaskSessions(lambda:[dict(id='main',origin='http://127.0.0.1:17880',state=copy.deepcopy(state))],
            lambda *args:calls.append(args) or {'submitted':True})
        return service,calls

    def test_same_run_pauses_resumes_and_never_exposes_start(self):
        state=self.state();service,calls=self.registry(state)
        running=service.snapshot()['tasks'][0]
        self.assertTrue(running['capabilities']['can_pause']);self.assertFalse(running['capabilities']['can_resume'])
        service.action(dict(task_id=running['task_id'],expected_revision=running['revision'],action='pause'))
        state['hardware']['paused']=True
        paused=service.snapshot()['tasks'][0]
        self.assertEqual(paused['status'],'paused');self.assertEqual(paused['revision'],running['revision'])
        self.assertFalse(paused['capabilities']['can_pause']);self.assertTrue(paused['capabilities']['can_resume'])
        self.assertTrue(paused['capabilities']['can_stop'])
        service.action(dict(task_id=paused['task_id'],expected_revision=paused['revision'],action='resume'))
        self.assertEqual([x[2] for x in calls],['pause','resume'])
        self.assertNotIn('can_start',paused['capabilities'])

    def test_new_run_rejects_old_resume_even_when_device_is_identical(self):
        state=self.state();state['hardware']['paused']=True;service,calls=self.registry(state)
        old=service.snapshot()['tasks'][0];state['hardware_control_id']='b'*64
        with self.assertRaisesRegex(ValueError,'Task changed'):
            service.action(dict(task_id=old['task_id'],expected_revision=old['revision'],action='resume'))
        self.assertEqual(calls,[])

    def test_idle_stale_unknown_probe_or_group_has_no_resume(self):
        for change in ('idle','stale','unknown','probe','group'):
            state=self.state();state['hardware']['paused']=True
            if change=='idle':state.update(hardware={'active':False},hardware_control_id=None)
            if change=='stale':state['stale']=True
            if change=='unknown':state['hardware']['active']=None
            if change=='probe':state['hardware']['source']='bounded_single_joint_check'
            if change=='group':state['group']={'active':True}
            with self.subTest(change=change):self.assertFalse(hand_playback(state)['can_resume'])

    def test_program_gap_can_pause_without_starting_next_entry(self):
        state=hand();state['program']=dict(active=True,paused=False,mode='hardware',run_id='c'*32)
        self.assertTrue(hand_playback(state)['can_pause'])
        state['program']['paused']=True;self.assertTrue(hand_playback(state)['can_resume'])
        state['hardware_control_id']='b'*64;state['hardware'].update(active=True,paused=False,phase='playing')
        self.assertFalse(hand_playback(state)['can_resume'])  # pause not acknowledged
        self.assertFalse(hand_playback(state)['paused'])
        state['hardware']['paused']=True;self.assertTrue(hand_playback(state)['can_resume'])

    def test_completed_preview_does_not_offer_resume_which_would_rewind(self):
        state=hand();state['playback']=dict(active=True,running=False,cycles=1,duration_s=2.,elapsed_s=2.)
        self.assertFalse(hand_playback(state)['can_resume']);self.assertTrue(hand_playback(state)['can_stop'])

    def test_program_and_preview_run_identity_are_in_task_context(self):
        base=hand();scope=context_digest(base)
        for key,value in (('program',{'run_id':'c'*32}),('playback',{'clock_at':'synthetic-new-run'})):
            self.assertNotEqual(scope,context_digest(dict(base,**{key:value})))

    def test_completed_group_history_does_not_block_a_later_single_preview(self):
        state=hand();state['hardware']['group_sync']={'active':False,'phase':'stopped'}
        state['playback']=dict(active=True,running=True,cycles=1,duration_s=20.,elapsed_s=2.)
        self.assertTrue(hand_playback(state)['can_pause'])
        state['hardware']['group_sync']['active']=None
        self.assertFalse(hand_playback(state)['can_pause'])


class SingleTaskAdapterTests(unittest.TestCase):
    def test_real_controller_routes_lease_pause_resume_without_stopping_or_restarting(self):
        with control_fixtures.ControllerContinuationTests().environment() as e:
            c=e.c;c.hardware_lease='exact-secret-lease'
            state=SingleTaskCapabilitiesTests().state()
            with patch.object(c,'action',return_value={'submitted':True}) as action:
                c._task_context_dispatch('pause',{},state)
                state['hardware']['paused']=True;c._task_context_dispatch('resume',{},state)
            self.assertEqual([call.args[0] for call in action.call_args_list],[
                dict(name='hardware_pause',lease='exact-secret-lease'),dict(name='hardware_resume',lease='exact-secret-lease')])
            snapshot=c.snapshot();self.assertRegex(snapshot['hardware_control_id'],'^[0-9a-f]{64}$')
            self.assertNotEqual(snapshot['hardware_control_id'],c.hardware_lease)

    def test_resume_never_follows_glove_and_stop_still_disables_exact_hand(self):
        with control_fixtures.ControllerContinuationTests().environment() as e:
            c=e.c;c.hardware_lease='exact-secret-lease';state=SingleTaskCapabilitiesTests().state()
            with patch.object(c,'action') as action:
                with self.assertRaises(ValueError):c._task_context_dispatch('resume',{},dict(state,glove={'busy':True}))
                action.assert_not_called();c._task_context_dispatch('stop',{},state)
                self.assertEqual([call.args[0]['name'] for call in action.call_args_list],['program_stop','hardware_stop'])
            for action_name in ('pause','resume'):
                with patch.object(c,'action') as action,self.assertRaisesRegex(ValueError,'ensemble'):
                    c._task_context_dispatch(action_name,{},dict(state,group={'active':True}))
                action.assert_not_called()

    def test_typed_final_scope_check_and_dispatch_hold_owner_lock(self):
        state=SingleTaskCapabilitiesTests().state();lock=threading.RLock()
        def dispatch(*args):
            self.assertTrue(lock._is_owned());return {'submitted':True}
        c=N(lock=lock,snapshot=lambda:state,calibration_handover=N(workflow_lock=threading.RLock()),_task_context_dispatch=dispatch)
        scope=context_digest(state);execute(c,scope,'pause')
        state['hardware_control_id']='d'*64
        with self.assertRaisesRegex(ValueError,'session changed'):execute(c,scope,'resume')

    def test_safety_stop_bypasses_an_actually_held_cli_gate(self):
        from cli_coordination import gate
        for owner in ('glove','unknown_hand','pending_hand'):
            with self.subTest(owner=owner),control_fixtures.ControllerContinuationTests().environment() as e:
                c=e.c;state=SingleTaskCapabilitiesTests().state()
                if owner=='glove':state.update(glove={'busy':True},hardware={'active':False});c.hardware_lease=None
                else:
                    state['hardware']['active']=None if owner=='unknown_hand' else False
                    c.hardware_lease='pending-exact-lease'
                entered=threading.Event();release=threading.Event();held=threading.Event()
                def hold():
                    with gate():
                        held.set();entered.set()
                        try:release.wait(3)
                        finally:held.clear()
                thread=threading.Thread(target=hold);thread.start();self.assertTrue(entered.wait(1))
                try:
                    with patch.object(c,'snapshot',return_value=copy.deepcopy(state)),patch.object(c,'action') as action:
                        execute(c,context_digest(state),'pause')
                        self.assertTrue(held.is_set(),'Stop must arrive while maintenance still holds its gate')
                        self.assertEqual([call.args[0]['name'] for call in action.call_args_list],
                            ['glove_stop'] if owner=='glove' else ['program_stop','hardware_stop'])
                finally:release.set();thread.join(2)
                self.assertFalse(thread.is_alive())

    def test_pending_hand_stop_cannot_turn_into_pause_when_start_ack_arrives(self):
        with control_fixtures.ControllerContinuationTests().environment() as e:
            c=e.c;c.hardware_lease='pending-exact-lease';pending=SingleTaskCapabilitiesTests().state()
            pending['hardware']['active']=False;ready=copy.deepcopy(pending);ready['hardware']['active']=True
            with patch.object(c,'snapshot',side_effect=[pending,ready]),patch.object(c,'action') as action, \
                    patch('cli_coordination.gate',side_effect=AssertionError('Safety stop must bypass gate')):
                execute(c,context_digest(pending),'pause')
            self.assertEqual([call.args[0]['name'] for call in action.call_args_list],['program_stop','hardware_stop'])

    def test_repeated_pause_or_resume_of_same_run_is_noop_not_stop_or_restart(self):
        with control_fixtures.ControllerContinuationTests().environment() as e:
            c=e.c;c.hardware_lease='exact-secret-lease';state=SingleTaskCapabilitiesTests().state()
            with patch.object(c,'action') as action:
                self.assertEqual(c._task_context_dispatch('resume',{},state),{'paused':False})
                state['hardware']['paused']=True
                self.assertEqual(c._task_context_dispatch('pause',{},state),{'paused':True})
                action.assert_not_called()

    def test_real_controller_blocks_a_playlist_entry_paused_after_external_check(self):
        with control_fixtures.ControllerContinuationTests().environment() as e:
            c=e.c;c.program.thread=threading.current_thread();c.program.update(active=True,paused=True)
            c.program.heartbeat=__import__('time').monotonic()
            with patch.object(c,'send') as send:
                for name in ('hardware_trial','demo_start'):
                    with self.assertRaises(ProgramEntryPaused):c._action({'name':name})
                send.assert_not_called();self.assertFalse(c.player.snapshot()['active'])
            c.program.thread=None;c.program.update(active=False,paused=False)


class PlaylistPauseTests(unittest.TestCase):
    def runner(self):
        controller=N(lock=threading.RLock(),hardware_lease='a'*32,_action=Mock(),action=Mock(),player=DemoPlayer())
        runner=ProgramRunner(controller);runner.lease='p'*32;runner.heartbeat=__import__('time').monotonic()
        runner.update(active=True,paused=False,mode='hardware',run_id='c'*32)
        return runner,controller

    def test_failed_pause_does_not_report_paused_and_retry_preserves_lease_and_run(self):
        runner,c=self.runner();c._action.side_effect=ValueError('synthetic transport failure')
        with self.assertRaises(ValueError):runner.pause(True)
        self.assertFalse(runner.snapshot()['paused']);c._action.side_effect=None
        runner.pause(True);runner.pause(False)
        self.assertFalse(runner.snapshot()['paused']);self.assertEqual(runner.lease,'p'*32)
        self.assertEqual(runner.snapshot()['run_id'],'c'*32)
        self.assertEqual([x.args[0]['name'] for x in c._action.call_args_list],['hardware_pause','hardware_pause','hardware_resume'])

    def test_paused_boundary_does_not_start_until_resume_and_cancel_cannot_restart(self):
        runner,c=self.runner();runner.update(paused=True)
        def resume(_):
            c.action.assert_not_called();runner.pause(False)
        with patch('program_runner.time.sleep',side_effect=resume):runner.start_cycle({'name':'hardware_trial'})
        c.action.assert_called_once_with({'name':'hardware_trial'})
        c.action.reset_mock();runner.stop()
        with self.assertRaisesRegex(ValueError,'Stopped'):runner.start_cycle({'name':'hardware_trial'})
        with self.assertRaisesRegex(ValueError,'No active'):runner.pause(False)
        c.action.assert_not_called()

    def test_preview_pause_holds_elapsed_and_resume_does_not_rewind_completed_entry(self):
        now=[1.]
        with patch('demo_player.time',N(monotonic=lambda:now[0])):
            runner,c=self.runner();runner.update(mode='preview')
            c.player.command(dict(name='demo_start',action='wave',speed=1,cycles=1))
            now[0]=3.;runner.pause(True);elapsed=c.player.snapshot()['elapsed_s']
            now[0]=100.;self.assertEqual(c.player.snapshot()['elapsed_s'],elapsed)
            runner.pause(False);now[0]=100.1;self.assertAlmostEqual(c.player.snapshot()['elapsed_s'],elapsed+.1)
            now[0]=200.;c.player.snapshot();runner.pause(True);runner.pause(False)
            self.assertEqual(c.player.snapshot()['elapsed_s'],c.player.snapshot()['duration_s'])
            self.assertFalse(c.player.snapshot()['running'])

    def test_entry_pause_race_retries_same_entry_only_after_explicit_resume(self):
        runner,c=self.runner();command={'name':'hardware_trial','action':'same-selected-entry'}
        def action(value):
            if c.action.call_count==1:
                runner.update(paused=True);raise ProgramEntryPaused('Synthetic boundary race')
            return {'lease':'a'*32}
        c.action.side_effect=action
        with patch('program_runner.time.sleep',side_effect=lambda _:runner.pause(False)):
            self.assertEqual(runner.start_cycle(command),{'lease':'a'*32})
        self.assertEqual(c.action.call_count,2)
        self.assertTrue(all(call.args[0] is command for call in c.action.call_args_list))


class SingleHardwarePauseTests(unittest.TestCase):
    def driver(self,tmp):
        hand,m=group_fixtures.GroupHardwareTests().driver(tmp)
        m.start_trial(dict(action='pair_wave_left',amplitude=.25,cycles=1,speed=1,
            lease='a'*32,workspace_clear=True),feedback(1.,.1),diagnostics(1.),1.)
        self.tick(m,1.01);return hand,m

    def row(self,m,now):
        row=feedback(now)
        for joint,value in zip(row['joints'],m.target):joint['position_rad']=value
        return row

    def tick(self,m,now):
        m.last_beat=now;m.tick(self.row(m,now),diagnostics(now),now)

    def command(self,m,name,now):
        m.command(dict(name='hardware_'+name,lease='a'*32),self.row(m,now),diagnostics(now),now)

    def test_long_pause_and_duplicate_pause_preserve_elapsed_then_shift_deadline_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            hand,m=self.driver(tmp);self.tick(m,1.02);deadline=m.trial['deadline'];elapsed=m.elapsed
            self.command(m,'pause',1.03);self.command(m,'pause',20.)
            count=len(m.trial['trace']);self.tick(m,100.)
            self.assertTrue(m.active,m.reason);self.assertTrue(m.paused);self.assertEqual(m.elapsed,elapsed)
            self.assertEqual(len(m.trial['trace']),count);target=m.target[:]
            self.command(m,'resume',101.);self.assertAlmostEqual(m.trial['deadline'],deadline+99.97)
            self.command(m,'resume',101.1);self.assertAlmostEqual(m.trial['deadline'],deadline+99.97)
            self.tick(m,101.01);self.assertTrue(m.active,m.reason);self.assertLessEqual(m.elapsed-elapsed,.011)
            self.assertLess(max(abs(x-y) for x,y in zip(target,m.target)),.01)
            self.assertEqual(sum(x[0]=='enable' for x in hand.calls),1);m.stop('done')

    def test_paused_heartbeat_and_feedback_loss_still_stop_and_disable(self):
        with tempfile.TemporaryDirectory() as tmp:
            for failure in ('heartbeat','feedback'):
                hand,m=self.driver(tmp);self.command(m,'pause',1.03)
                if failure=='feedback':m.last_beat=3.
                m.tick(self.row(m,1.03 if failure=='feedback' else 3.),diagnostics(3.),3.)
                self.assertFalse(m.active,failure);self.assertFalse(m.owned,failure)
                self.assertIn(('disable',),hand.calls)


if __name__=='__main__':unittest.main()
