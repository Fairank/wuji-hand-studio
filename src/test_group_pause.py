"""Common pause/resume acceptance; fake SDK and clocks, no hardware or physics."""
import copy
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from demo_player import DemoPlayer
from group_coordinator import GroupCoordinator
from group_hardware import metadata
import test_group_control as fixtures
from test_hardware_showcase import feedback,diagnostics


class HardwarePauseTests(unittest.TestCase):
    def driver(self,tmp):
        hand,m=fixtures.GroupHardwareTests().driver(tmp)
        m.start_trial(dict(action='pair_wave_left',amplitude=.25,cycles=1,speed=1,
            lease='a'*32,workspace_clear=True,group_sync={'token':'b'*32}),feedback(1.,.1),diagnostics(1.),1.)
        self.tick(m,1.01)
        m.command(dict(name='hardware_group_commit',lease='a'*32,token='b'*32,start_s=2.,timing=metadata(m.run_profile)),feedback(1.2,.1),diagnostics(1.2),1.2)
        self.tick(m,2.)
        return hand,m

    def tick(self,m,now):
        row=feedback(now)
        for j,v in zip(row['joints'],m.target):j['position_rad']=v
        m.last_beat=now;m.tick(row,diagnostics(now),now)

    def control(self,m,action,at,epoch,now):
        m.command(dict(name='hardware_group_'+action,lease='a'*32,token='b'*32,at_s=at,epoch=epoch),feedback(now),diagnostics(now),now)

    @patch('group_hardware.clock_id',return_value='same-kernel')
    def test_two_hands_pause_longer_than_trial_deadline_and_resume_without_jump(self,_):
        with tempfile.TemporaryDirectory() as tmp:
            drivers=[self.driver(tmp)[1] for _ in range(2)]
            old_deadlines=[m.trial['deadline'] for m in drivers]
            for m in drivers:self.control(m,'pause',3.,1,2.1)
            for now in (2.11,2.5,2.99):
                for m in drivers:self.tick(m,now);self.assertFalse(m.paused)
            for m,offset in zip(drivers,(.001,.007)):
                self.tick(m,3.+offset);self.assertTrue(m.active,m.reason);self.assertEqual(m.phase,'paused')
                self.assertEqual(m.elapsed,1.);self.assertEqual(m.group_sync['applied_epoch'],1)
            for m in drivers:
                count=len(m.trial['trace']);self.tick(m,100.)
                self.assertTrue(m.active,m.reason);self.assertEqual(m.elapsed,1.);self.assertEqual(len(m.trial['trace']),count)
                self.control(m,'resume',101.,2,100.)
                self.tick(m,100.99);self.assertTrue(m.paused)
                target=m.target[:];self.tick(m,101.002)
                self.assertTrue(m.active,m.reason);self.assertFalse(m.paused);self.assertAlmostEqual(m.elapsed,1.002)
                self.assertLess(max(abs(a-b) for a,b in zip(target,m.target)),.01)
            for m,deadline in zip(drivers,old_deadlines):
                self.assertAlmostEqual(m.trial['deadline'],deadline+98.)
                self.assertEqual(m.group_sync['applied_epoch'],2)
                self.control(m,'pause',102.,3,101.1);self.tick(m,102.001)
                self.assertAlmostEqual(m.elapsed,2.);m.stop('test')

    @patch('group_hardware.clock_id',return_value='same-kernel')
    def test_old_token_epoch_pending_wrong_phase_and_single_hand_bypass_rejected(self,_):
        with tempfile.TemporaryDirectory() as tmp:
            _,m=self.driver(tmp)
            base=dict(name='hardware_group_pause',lease='a'*32,token='b'*32,at_s=3.,epoch=1)
            for change in (dict(token='c'*32),dict(epoch=0),dict(epoch=2),dict(epoch=True),dict(at_s=float('nan')),dict(name='hardware_group_resume')):
                with self.subTest(change=change),self.assertRaises(ValueError):m.command(dict(base,**change),feedback(2.1),diagnostics(2.1),2.1)
            self.control(m,'pause',3.,1,2.1)
            with self.assertRaises(ValueError):self.control(m,'pause',3.1,2,2.2)
            self.tick(m,3.)
            for name in ('hardware_pause','hardware_resume'):
                with self.assertRaises(ValueError):m.command(dict(name=name,lease='a'*32),feedback(3.),diagnostics(3.),3.)
            with self.assertRaises(ValueError):self.control(m,'resume',4.,1,3.)
            m.stop('test')

    @patch('group_hardware.clock_id',return_value='same-kernel')
    def test_late_pause_and_lost_heartbeat_while_paused_disable_hardware(self,_):
        with tempfile.TemporaryDirectory() as tmp:
            for failure in ('late','lease','feedback'):
                hand,m=self.driver(tmp);self.control(m,'pause',3.,1,2.1)
                if failure=='late':self.tick(m,3.04)
                else:
                    self.tick(m,3.)
                    if failure=='lease':m.tick(feedback(4.),diagnostics(4.),4.)
                    else:m.last_beat=4.;m.tick(feedback(3.),diagnostics(4.),4.)
                self.assertFalse(m.active,failure);self.assertFalse(m.owned,failure)


class PreviewPauseTests(unittest.TestCase):
    def test_participant_routes_exact_epoch_and_retains_paused_not_completed(self):
        from group_participant import GroupParticipant
        clock=[1.]
        with patch('demo_player.time',SimpleNamespace(monotonic=lambda:clock[0])),patch('group_participant.time',SimpleNamespace(monotonic=lambda:clock[0])):
            player=DemoPlayer();player.command(dict(name='demo_start',action='pair_wave_left',speed=1,cycles=1));player.schedule(2.)
            participant=GroupParticipant(SimpleNamespace(player=player));participant.active=True;participant.mode='preview';participant.token='b'*32;participant.start_s=2.
            clock[0]=2.1
            with self.assertRaises(ValueError):participant.handle(dict(name='group_pause',token='old',epoch=1,at_s=3.))
            participant.handle(dict(name='group_pause',token=participant.token,epoch=1,at_s=3.))
            clock[0]=3.2;state=participant.snapshot()
            self.assertEqual(state['phase'],'paused');self.assertTrue(state['active']);self.assertEqual(state['elapsed_s'],1.)
            with self.assertRaises(ValueError):participant.handle(dict(name='group_resume',token=participant.token,epoch=1,at_s=4.))
            participant.handle(dict(name='group_resume',token=participant.token,epoch=2,at_s=4.))
            clock[0]=4.2;self.assertEqual(participant.snapshot()['phase'],'playing');self.assertAlmostEqual(player.elapsed,1.2)

    def test_shared_future_pause_is_exact_even_with_different_snapshot_times(self):
        clock=[1.]
        with patch('demo_player.time.monotonic',side_effect=lambda:clock[0]):
            players=[DemoPlayer(),DemoPlayer()]
            for p in players:
                p.command(dict(name='demo_start',action='pair_wave_left',speed=.5,cycles=1));p.schedule(2.)
            clock[0]=2.1
            for p in players:p.schedule_control('pause',3.,1)
            for p,t in zip(players,(3.2,4.)):
                clock[0]=t;s=p.snapshot();self.assertEqual(s['elapsed_s'],.5);self.assertTrue(s['group_paused'])
            clock[0]=100.
            for p in players:p.schedule_control('resume',101.,2)
            clock[0]=101.2
            for p in players:
                s=p.snapshot();self.assertAlmostEqual(s['elapsed_s'],.6);self.assertTrue(s['running']);self.assertEqual(s['applied_epoch'],2)
            players[0].command(dict(name='demo_stop'));clock[0]=200.
            self.assertFalse(players[0].snapshot()['running'])


class CoordinatorPauseTests(unittest.TestCase):
    def coordinator(self):
        g=GroupCoordinator(lambda i:{},lambda i,c:{})
        self.addCleanup(g.pool.shutdown)
        g.token='b'*32;g.state=dict(active=True,phase='playing',can_pause=True,can_resume=False,members=[])
        return g

    def test_request_requires_current_lease_and_cannot_double_queue(self):
        g=self.coordinator()
        with self.assertRaises(ValueError):g.request_control('pause','old')
        self.assertEqual(g.request_control('pause',g.token),dict(requested='pause',epoch=1))
        self.assertEqual(g.state['phase'],'pausing');self.assertFalse(g.state['can_pause'])
        with self.assertRaises(ValueError):g.request_control('pause',g.token)
        with self.assertRaises(ValueError):g.request_control('resume',g.token)

    def test_transport_success_without_exact_worker_ack_never_claims_paused(self):
        t=dict(action='pause',epoch=1,at_s=5.,acknowledged=False)
        rows=[dict(clock_s=4.,control_epoch=0,applied_epoch=0,phase='playing') for _ in range(2)]
        self.assertFalse(GroupCoordinator.control_complete(rows,t))
        rows[0].update(control_epoch=1,pending_control=dict(action='pause',epoch=1,at_s=5.))
        self.assertFalse(GroupCoordinator.control_complete(rows,t));self.assertFalse(t['acknowledged'])
        rows[1]['clock_s']=4.9
        with self.assertRaisesRegex(ValueError,'acknowledgement'):GroupCoordinator.control_complete(rows,t)

    def test_both_worker_ack_then_both_applied_epochs_required(self):
        t=dict(action='resume',epoch=2,at_s=5.,acknowledged=False)
        rows=[dict(clock_s=4.,control_epoch=2,applied_epoch=1,phase='paused',pending_control=dict(action='resume',epoch=2,at_s=5.)) for _ in range(2)]
        self.assertFalse(GroupCoordinator.control_complete(rows,t));self.assertTrue(t['acknowledged'])
        rows[0].update(clock_s=5.01,applied_epoch=2,phase='playing',pending_control=None)
        self.assertFalse(GroupCoordinator.control_complete(rows,t))
        rows[1].update(clock_s=5.02,applied_epoch=2,phase='playing',pending_control=None)
        self.assertTrue(GroupCoordinator.control_complete(rows,t))
        rows[0]['applied_epoch']=3
        with self.assertRaisesRegex(ValueError,'epoch'):GroupCoordinator.control_complete(rows,t)

    def run_fixture(self,fault=None,capabilities=(1,1)):
        clock=[0.];calls=[];groups={};requests=[];g=None
        spec=dict(members=['L','R'],mode='preview',kind='pair',action='pair_wave',speed=1,cycles=1,amplitude=1)
        def read(i):
            state=groups[i];pending=state.get('pending_control')
            if state['phase']=='waiting' and clock[0]>=state['start_s']:state['phase']='playing'
            if pending and clock[0]>=pending['at_s']:
                if fault!='unapplied' or i!='R':
                    state.update(phase='paused' if pending['action']=='pause' else 'playing',applied_epoch=pending['epoch'],pending_control=None)
            if len(requests)==2 and clock[0]>45. and state['phase']=='playing':state['phase']='completed'
            return dict(group=dict(state,clock_s=clock[0]),connection='connected',stale=False)
        def post(i,c):
            name=c['name'];calls.append((i,name))
            if name=='group_prepare':groups[i]=dict(token=g.token,active=True,phase='preparing',elapsed_s=0.,control_version=capabilities[['L','R'].index(i)],control_epoch=0,applied_epoch=0)
            elif name=='group_arm':groups[i]['phase']='ready'
            elif name=='group_commit':groups[i].update(phase='waiting',start_s=c['start_s'])
            elif name in ('group_pause','group_resume'):
                if fault=='post' and i=='R':raise ValueError('injected member refusal')
                if fault!='noack' or i!='R':groups[i].update(control_epoch=c['epoch'],pending_control=dict(action=name[6:],epoch=c['epoch'],at_s=c['at_s']))
            return {}
        def sleep(dt):
            clock[0]+=dt;g.heartbeat=clock[0]
            if clock[0]>70:raise AssertionError('test loop did not finish')
            if not requests and g.state.get('can_pause'):
                requests.append('pause');g.request_control('pause',g.token)
            elif requests==['pause'] and clock[0]>40. and g.state.get('can_resume'):
                requests.append('resume');g.request_control('resume',g.token)
            elif capabilities!=(1,1) and clock[0]>2.:
                with self.assertRaises(ValueError):g.request_control('pause',g.token)
                self.assertFalse(g.cancel.is_set());g.stop()
        g=GroupCoordinator(read,post);self.addCleanup(g.pool.shutdown)
        g.token='b'*32;g.state=dict(active=True,phase='preparing',members=[dict(id=i,phase='preparing') for i in spec['members']])
        with patch('group_coordinator.time',SimpleNamespace(monotonic=lambda:clock[0],sleep=sleep)):
            g.run(spec,g.token)
        return g,calls,requests

    def test_full_coordinator_long_pause_and_resume_preserves_completion_budget(self):
        g,calls,requests=self.run_fixture()
        self.assertEqual(requests,['pause','resume']);self.assertEqual(g.state['phase'],'completed',g.state)
        self.assertEqual(sum(n=='group_resume' for _,n in calls),2)
        self.assertEqual({i for i,n in calls if n=='group_abort'},{'L','R'})

    def test_partial_send_missing_ack_or_missing_apply_stop_every_member(self):
        for fault in ('post','noack','unapplied'):
            with self.subTest(fault=fault):
                g,calls,_=self.run_fixture(fault)
                self.assertEqual(g.state['phase'],'failed',g.state)
                self.assertEqual({i for i,n in calls if n=='group_abort'},{'L','R'})
                self.assertNotIn('group_resume',[n for _,n in calls])

    def test_legacy_and_mixed_worker_capability_refuses_pause_without_dispatch(self):
        for versions in ((None,None),(1,None)):
            with self.subTest(versions=versions):
                g,calls,requests=self.run_fixture(capabilities=versions)
                self.assertEqual(requests,[]);self.assertFalse(g.state['can_pause'])
                self.assertNotIn('group_pause',[n for _,n in calls])


if __name__=='__main__':unittest.main()
