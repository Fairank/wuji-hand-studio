import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from hardware_showcase import HardwareShowcase,validate_profile,NIDS
from view_camera import validate_camera,DEFAULT,preview_selected


def profile():
    return dict(schema=1,device_id='synthetic-left',side='left',hardware_reviewed=True,
        calibration_record='offline-test-fixture-not-a-real-calibration',lower_rad=[-1.]*20,
        upper_rad=[1.]*20,max_velocity_rad_s=.2,current_limit_A=.3,
        actions=dict(index=dict(hardware_reviewed=True,points=[dict(t=0.,q=[0.]*20),
            dict(t=1.,q=[.1]*20),dict(t=2.,q=[0.]*20)])))


def feedback(now,q=0.):
    return dict(host_s=now,joints=[dict(nid=n,position_rad=q,velocity_rad_s=0.,effort_A=0.) for n in NIDS])


def diagnostics(now):
    return dict(host_s=now,joints=[dict(nid=n,error=0,state=2) for n in NIDS])


class FakeHand:
    def __init__(self):self.calls=[];self.cap=.3;self.params=(3.,.05)
    def effort_limit(self):
        def write(value):self.cap=value;self.calls.append(('limit',value))
        return SimpleNamespace(set=write,get=lambda:[self.cap]*20)
    def mit_params(self):
        def write(value):self.params=value;self.calls.append(('params',value))
        return SimpleNamespace(set=write,get=lambda:[SimpleNamespace(kp=self.params[0],kd=self.params[1]) for _ in range(20)])
    def joint_command(self):return SimpleNamespace(publish=lambda:self)
    def send(self,commands):self.calls.append(('send',[c.position for c in commands]))
    def enable(self,**kwargs):self.calls.append(('enable',kwargs))
    def disable(self):self.calls.append(('disable',))
    def close(self):self.calls.append(('close',))


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'profile.json'
        self.hand=FakeHand()
    def tearDown(self):self.tmp.cleanup()
    def driver(self,exists=True):
        if exists:self.path.write_text(json.dumps(profile()))
        return HardwareShowcase(self.hand,'synthetic-left',lambda **k:SimpleNamespace(**k),self.path)
    def start(self,d):
        d.start(dict(action='index',speed=.5,cycles=1,lease='a'*32),feedback(1.),diagnostics(1.),1.)

    def test_no_profile_has_no_sdk_side_effect_even_stop(self):
        d=self.driver(False)
        with self.assertRaises(ValueError):self.start(d)
        d.stop('test');self.assertEqual(self.hand.calls,[])

    def test_profile_cannot_be_mirrored_or_use_unreviewed_paths(self):
        for field,value in [('device_id','other'),('side','right'),('hardware_reviewed',False),
                            ('calibration_record',''),('current_limit_A',1.5),('max_velocity_rad_s',1.)]:
            p=profile();p[field]=value
            with self.assertRaises(ValueError):validate_profile(p,'synthetic-left')
        p=profile();p['actions']['index']['points'][1]['q'][0]=2.
        with self.assertRaises(ValueError):validate_profile(p,'synthetic-left')

    def test_start_seeds_current_before_enable_and_slews(self):
        d=self.driver();self.start(d)
        names=[x[0] for x in self.hand.calls]
        self.assertLess(names.index('send'),names.index('enable'))
        d.tick(feedback(1.02),diagnostics(1.02),1.02)
        d.tick(feedback(1.04),diagnostics(1.04),1.04)
        sent=[c[1] for c in self.hand.calls if c[0]=='send']
        self.assertEqual(sent[0],[0.]*20)
        self.assertTrue(all(abs(x-y)<=.0061 for a,b in zip(sent,sent[1:]) for x,y in zip(a,b)))
        d.stop('done');self.assertFalse(d.active)
        self.assertIn(('disable',),self.hand.calls)

    def test_expired_browser_lease_stops_without_more_target_commands(self):
        d=self.driver();self.start(d)
        count=sum(c[0]=='send' for c in self.hand.calls)
        d.tick(feedback(2.),diagnostics(2.),2.)
        self.assertFalse(d.active);self.assertIn('网页',d.reason)
        self.assertEqual(sum(c[0]=='send' for c in self.hand.calls),count)

    def test_fault_stale_overcurrent_and_tracking_error_stop(self):
        for kind in ('fault','stale','current','tracking'):
            self.hand=FakeHand();d=self.driver();self.start(d)
            row=feedback(1.02);diag=diagnostics(1.02)
            if kind=='fault':diag['joints'][0]['error']=1
            if kind=='stale':row['host_s']=0.
            if kind=='current':row['joints'][0]['effort_A']=.5
            if kind=='tracking':row['joints'][0]['position_rad']=.3
            d.tick(row,diag,1.02)
            self.assertFalse(d.active,kind);self.assertIn(('disable',),self.hand.calls,kind)

    def test_readback_mismatch_disables_without_enable(self):
        d=self.driver()
        self.hand.effort_limit=lambda:SimpleNamespace(set=lambda _:None,get=lambda:[1.]*20)
        with self.assertRaises(ValueError):self.start(d)
        self.assertFalse(any(c[0]=='enable' for c in self.hand.calls));self.assertIn(('disable',),self.hand.calls)

    def test_pose_changed_during_parameter_rpc_never_enables_from_old_seed(self):
        d=self.driver()
        d.refresh_before_enable=lambda:(feedback(1.05,q=.02),diagnostics(1.05),1.05)
        with self.assertRaisesRegex(ValueError,'姿态发生变化'):self.start(d)
        self.assertFalse(any(c[0] in ('enable','send') for c in self.hand.calls))
        self.assertIn(('disable',),self.hand.calls)

    def ready_diag(self,now,enabled=None):
        return dict(host_s=now,joints=[dict(nid=n,error=0,state=2 if i==enabled else 1,
            state_name='Enabled' if i==enabled else 'Ready') for i,n in enumerate(NIDS)])

    def probe_command(self,**changes):
        return dict(dict(name='hardware_probe',index=6,direction=1,workspace_clear=True,lease='a'*32),**changes)

    def test_probe_without_profile_only_enables_selected_joint(self):
        d=self.driver(False)
        d.command(self.probe_command(),feedback(1.),self.ready_diag(1.),1.)
        enable=next(c for c in self.hand.calls if c[0]=='enable')
        self.assertEqual(enable[1]['joints'],[int(i==6) for i in range(20)])
        self.assertIsNone(d.profile)
        self.assertFalse(d.status()['ready'])
        q=[0.]*20
        for step in range(1,370):
            now=1.+step*.01
            row=feedback(now)
            for joint,value in zip(row['joints'],q):joint['position_rad']=value
            d.command(dict(name='hardware_keepalive',lease='a'*32),row,self.ready_diag(now,6),now)
            d.tick(row,self.ready_diag(now,6),now)
            if not d.active:break
            q=next(c[1] for c in reversed(self.hand.calls) if c[0]=='send')[:]
        self.assertFalse(d.active);self.assertTrue(d.probe_result['accepted'])
        self.assertFalse(d.probe_result['full_action_calibrated'])
        commands=[c[1] for c in self.hand.calls if c[0]=='send']
        self.assertTrue(all(abs(v)<1e-12 for a in commands for j,v in enumerate(a) if j!=6))
        self.assertAlmostEqual(max(a[6] for a in commands),.05235987756,places=7)
        self.assertTrue(all(abs(a[6]-b[6])<=.00151 for a,b in zip(commands,commands[1:])))

    def test_probe_refuses_invalid_requests_without_sdk_writes(self):
        for changes in ({'workspace_clear':False},{'index':20},{'index':True},{'direction':0},{'lease':'x'}):
            d=self.driver(False)
            with self.assertRaises(ValueError):d.command(self.probe_command(**changes),feedback(1.),self.ready_diag(1.),1.)
            self.assertEqual(self.hand.calls,[])
        d=self.driver(False)
        for row,diag in [(feedback(0.),self.ready_diag(1.)),(feedback(1.),self.ready_diag(1.,5))]:
            with self.assertRaises(ValueError):d.command(self.probe_command(),row,diag,1.)
        row=feedback(1.);row['joints'][6]['position_rad']=2.08
        with self.assertRaises(ValueError):d.command(self.probe_command(),row,self.ready_diag(1.),1.)
        self.assertEqual(self.hand.calls,[])

    def test_probe_no_movement_cannot_count_as_pass(self):
        d=self.driver(False);d.command(self.probe_command(),feedback(1.),self.ready_diag(1.),1.)
        for step in range(1,370):
            now=1+step*.01
            d.command(dict(name='hardware_keepalive',lease='a'*32),feedback(now),self.ready_diag(now,6),now)
            d.tick(feedback(now),self.ready_diag(now,6),now)
            if not d.active:break
        self.assertFalse(d.probe_result['accepted']);self.assertFalse(d.probe_result['feedback_motion_observed'])

    def test_probe_time_cap_and_disable_failure_not_success(self):
        d=self.driver(False);d.command(self.probe_command(),feedback(1.),self.ready_diag(1.),1.)
        d.last_beat=17.
        self.hand.disable=lambda:(_ for _ in ()).throw(RuntimeError('offline'))
        d.tick(feedback(17.),self.ready_diag(17.,6),17.)
        self.assertIsNone(d.status()['active']);self.assertFalse(d.probe_result['accepted'])
        self.assertFalse(d.probe_result['stop_confirmed'])

    def test_sdk_warning_does_not_require_custom_stability_or_loss_counter_gate(self):
        d=self.driver(False)
        def diag(now,enabled=None):
            value=self.ready_diag(now,enabled)
            value['comm']=dict(age_ms=100,e2e_received=1000,e2e_window_loss_x100=0,
                e2e_lost=0,e2e_reordered=0,e2e_duplicates=0,rpc_timeouts=0,comm_get_failures=0,sdk_dropped=0)
            for j in value['joints']:
                j.update(error=6,error_name='BusFrameLossHigh',severity='Warning',response_pct=100,bus_timeouts=1)
            return value
        d.observe_diagnostics(diag(1.),1.)
        self.assertTrue(d.check_probe_ready(feedback(1.),diag(1.),1.))
        self.assertEqual(self.hand.calls,[])
        for step in range(1,63):d.observe_diagnostics(diag(1+step*.05),1+step*.05)
        self.assertTrue(d.check_probe_ready(feedback(4.1),diag(4.1),4.1))
        d.command(self.probe_command(),feedback(4.1),diag(4.1),4.1)
        value=diag(4.12,6);value['comm']['e2e_lost']=1
        d.observe_diagnostics(value,4.12);d.tick(feedback(4.12),value,4.12)
        self.assertTrue(d.active);self.assertFalse(d.comm_healthy)
        d.stop('test complete')
        d=self.driver(False);d.comm_healthy=True
        for severity,name in [('ImmediateStop','BusFrameLossHigh'),('Warning','Unknown')]:
            value=diag(5.);value['joints'][0].update(severity=severity,error_name=name)
            self.assertFalse(d.check_probe_ready(feedback(5.),value,5.))

    def test_pause_resume_and_rejected_old_lease(self):
        d=self.driver();self.start(d)
        with self.assertRaises(ValueError):d.command(dict(name='hardware_pause',lease='b'*32),feedback(1.),diagnostics(1.),1.)
        d.command(dict(name='hardware_pause',lease='a'*32),feedback(1.02,.01),diagnostics(1.02),1.02)
        d.tick(feedback(1.04,.01),diagnostics(1.04),1.04)
        self.assertTrue(d.paused);self.assertEqual(d.target,[.01]*20)
        d.command(dict(name='hardware_resume',lease='a'*32),feedback(1.06),diagnostics(1.06),1.06)
        self.assertFalse(d.paused);d.stop('done')

    def test_camera_bounds_and_no_implicit_preview_on_signal_loss(self):
        c=copy.deepcopy(DEFAULT);c.update(azimuth=-10,elevation=200,distance=8)
        v=validate_camera(c);self.assertEqual((v['azimuth'],v['elevation'],v['distance']),(350.,85.,.9))
        c['distance']=float('nan')
        with self.assertRaises(ValueError):validate_camera(c)
        self.assertFalse(preview_selected(dict(view_source='feedback',stale=True,playback=dict(active=True))))
        self.assertTrue(preview_selected(dict(view_source='preview',playback=dict(active=True))))


if __name__=='__main__':unittest.main()
