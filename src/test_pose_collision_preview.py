"""Injected static geometry/graphics only. No MuJoCo or SDK import."""
import copy
import hashlib
import io
import json
import struct
import sys
import threading
import types
import unittest
from unittest.mock import Mock,patch

from collision_preview import SOURCE
from mapping_collision import ALGORITHM,COVERAGE
from glove_view import pose
from pose_view import GloveCollisionPreview,PoseView


def fixture(profile='hand2_right'):
    return dict(view_source='glove',teleoperation_view_mode='constrained',device_profile=dict(id=profile,generation=profile.split('_')[0]),
        stale=True,metrics={},glove=dict(connection='receiving',busy=True,profile=profile,session_id=7,
        glove_serial='SYNTHETIC-GLOVE',user=dict(user_id=''),stream=dict(seq=1,age_ms=10,timeout_ms=250,
        fresh=True,q=[.1]*20,solver=dict(engine='sdk'),retarget=dict(gain=[1.]*20,offset_deg=[0.]*20,smoothing_ms=0.))))


class FakePreview:
    def __init__(self,profile,session):
        self.profile=profile;self.session_id=session;self.calls=[];self.closed=False
        self.penetration=0.;self.limit=0.;self.stable=True;self.verified=True;self.limited=False;self.progressed=True
    def close(self):self.closed=True
    def advance(self,frame,**kwargs):
        self.calls.append((frame,kwargs))
        return dict(source=SOURCE,preview_only=True,hardware_validated=False,target_modified=False,
            profile=self.profile,session_id=self.session_id,source_seq=frame.seq,display_q=[.05]*20,
            source_target_sha256=hashlib.sha256(frame.target_bytes).hexdigest(),stable=self.stable,
            native_warning_count=0,max_all_contact_penetration_m=self.penetration,
            max_inter_finger_penetration_m=self.penetration,limit_excess_rad=self.limit,
            coverage=COVERAGE,algorithm=ALGORITHM,geometry_only=True,frame_verified=self.verified,
            path_certified=True,geometry_pairs_verified=290 if self.verified else 0,visual_meshes_verified=26,
            coverage_sha256='a'*64,minimum_verified_clearance_m=.0002 if self.verified else None,
            budget_limited=self.limited,progressed=self.progressed)


class CollisionOwnerTests(unittest.TestCase):
    def setUp(self):
        self.created=[]
        def factory(p,s):
            preview=FakePreview(p,s);self.created.append(preview);return preview
        self.owner=GloveCollisionPreview(factory);self.state=fixture()

    def test_factory_is_lazy_and_never_runs_for_other_modes_or_unknown_source(self):
        for fields in (dict(teleoperation_view_mode='feedback'),dict(view_source='feedback')):
            state=copy.deepcopy(self.state);state.update(fields)
            self.assertIsNone(self.owner.update(state,now=10.))
        for edit in (lambda g:g.update(connection='disconnected'),lambda g:g.update(busy=False),
                     lambda g:g['stream'].update(fresh=False),lambda g:g['stream'].update(age_ms=251),
                     lambda g:g.update(profile='hand2_left'),lambda g:g.update(glove_serial=''),
                     lambda g:g.update(session_id=None),lambda g:g.update(user={}),
                     lambda g:g['stream'].update(q=[float('nan')]*20)):
            state=copy.deepcopy(self.state);edit(state['glove'])
            out=self.owner.update(state,now=10.);self.assertEqual(out['status'],'blocked')
        self.assertEqual(self.created,[])

    def test_ready_is_independent_and_duplicate_seq_never_steps_again(self):
        before=json.dumps(self.state,sort_keys=True)
        result=self.owner.update(self.state,now=10.)
        self.assertEqual(result['status'],'ready')
        self.assertIn('映射预览 · 碰撞约束',result['message'])
        self.assertEqual(pose(self.state['glove'],'hand2','constrained',result)[0],[.05]*20)
        self.assertEqual(json.dumps(self.state,sort_keys=True),before)
        self.owner.update(self.state,now=10.05)
        self.assertEqual(len(self.created[0].calls),1)
        self.state['glove']['stream']['seq']=2
        self.owner.update(self.state,now=10.1)
        self.assertEqual(self.created[0].calls[-1][1]['substeps'],25)

    def test_leaving_mode_and_source_expiry_close_the_independent_model(self):
        self.owner.update(self.state,now=10.)
        self.state['glove']['stream']['fresh']=False
        out=self.owner.update(self.state,now=10.1)
        self.assertFalse(self.created[0].closed);self.assertEqual(out['status'],'blocked')
        self.state['teleoperation_view_mode']='feedback'
        self.assertIsNone(self.owner.update(self.state,now=10.2))
        self.assertTrue(self.created[0].closed)
        self.assertIsNone(self.owner.key)

    def test_small_source_timeout_is_not_replaced_by_global_five_hundred_ms(self):
        self.state['glove']['stream'].update(timeout_ms=100,age_ms=150)
        self.assertEqual(self.owner.update(self.state,now=10.)['status'],'blocked')
        self.assertFalse(self.created)

    def test_exact_session_serial_user_mapping_or_model_change_resets(self):
        edits=[lambda g:g.update(session_id=8),lambda g:g.update(glove_serial='OTHER-SYNTHETIC'),
               lambda g:g.update(user=dict(user_id='named-fixture')),
               lambda g:g['stream']['retarget'].update(smoothing_ms=10.),
               lambda g:g.update(model_provenance=dict(model_sha256='a'*64))]
        for edit in edits:
            self.owner.reset();self.created=[];state=fixture()
            self.owner.update(state,now=10.);old=self.created[-1]
            edit(state['glove']);self.owner.update(state,now=10.05)
            self.assertTrue(old.closed);self.assertEqual(len(self.created),2)

    def test_solver_frame_statistics_do_not_reset_model(self):
        self.owner.update(self.state,now=10.)
        self.state['glove']['stream']['seq']=2
        self.state['glove']['stream']['solver'].update(frames=500,solve_ms=1.)
        self.owner.update(self.state,now=10.05)
        self.assertEqual(len(self.created),1)

    def test_quality_block_does_not_recreate_model_and_next_new_frame_can_recover(self):
        self.owner.update(self.state,now=10.)
        preview=self.created[0];preview.verified=False;preview.limited=True;preview.progressed=False
        self.state['glove']['stream']['seq']=2
        out=self.owner.update(self.state,now=10.05)
        self.assertEqual(out['status'],'blocked');self.assertNotIn('display_q',out)
        self.assertIsNone(pose(self.state['glove'],'hand2','constrained',out)[0])
        self.assertFalse(preview.closed)
        preview.verified=True;preview.limited=False;preview.progressed=True;self.state['glove']['stream']['seq']=3
        self.assertEqual(self.owner.update(self.state,now=10.1)['status'],'ready')
        self.assertEqual(len(self.created),1)

    def test_joint_overshoot_is_blocked_without_changing_source_or_clamping(self):
        self.owner.update(self.state,now=10.);self.created[0].limit=.012
        self.state['glove']['stream']['seq']=2
        out=self.owner.update(self.state,now=10.05)
        self.assertEqual(out['status'],'blocked');self.assertNotIn('display_q',out)
        self.assertEqual(self.state['glove']['stream']['q'],[.1]*20)

    def test_unverified_state_closes_then_retries_on_new_data_without_manual_source_switch(self):
        self.owner.update(self.state,now=10.);self.created[0].stable=None
        self.state['glove']['stream']['seq']=2
        out=self.owner.update(self.state,now=10.05)
        self.assertEqual(out['status'],'error');self.assertTrue(self.created[0].closed)
        self.state['glove']['stream']['seq']=3
        self.assertEqual(self.owner.update(self.state,now=10.1)['status'],'error')
        self.assertEqual(len(self.created),1)
        self.state['glove']['stream']['seq']=4
        self.assertEqual(self.owner.update(self.state,now=10.6)['status'],'ready')
        self.assertEqual(len(self.created),2)

    def test_same_sequence_cannot_change_raw_target(self):
        self.owner.update(self.state,now=10.)
        self.state['glove']['stream']['q'][0]=.2
        self.assertEqual(self.owner.update(self.state,now=10.05)['status'],'error')
        self.assertTrue(self.created[0].closed)

    def test_hand1_has_the_same_complete_290_pair_coverage(self):
        state=fixture('hand1_left');out=self.owner.update(state,now=10.)
        self.assertEqual(out['status'],'ready');self.assertEqual(out['geometry_pairs_verified'],290)
        self.assertEqual(pose(state['glove'],'hand1','constrained',out)[1],'glove_constrained')

    def test_decoder_never_falls_back_on_unknown_mismatched_or_excessive_result(self):
        good=self.owner.update(self.state,now=10.)
        for edit in (dict(stable=None),dict(max_all_contact_penetration_m=.0011),dict(limit_excess_rad=.0011),
                     dict(source_seq=99),dict(bridge_session_id=99),dict(glove_serial='OTHER'),dict(profile='hand2_left'),
                     dict(source_target_sha256='wrong'),dict(coverage='unknown'),dict(display_q=[float('inf')]*20)):
            result=dict(good,**edit)
            q,mode,meta=pose(self.state['glove'],'hand2','constrained',result)
            self.assertIsNone(q);self.assertEqual(mode,'glove_constrained_unavailable')
            self.assertEqual(meta['collision_preview']['status'],'blocked')
        self.assertIsNone(pose(self.state['glove'],'hand2','constrained')[0])

    def test_automatic_requires_verified_geometry_before_feedback_and_never_projects_feedback(self):
        state=fixture();state['teleoperation_view_mode']='automatic'
        out=self.owner.update(state,now=10.)
        self.assertEqual(out['status'],'ready');self.assertEqual(pose(state['glove'],'hand2','automatic',out)[0],[.05]*20)
        state['glove']['feedback']=dict(device_id='HAND',stale=True)
        self.assertIsNone(self.owner.update(state,now=10.05));self.assertTrue(self.created[0].closed)
        self.assertEqual(pose(state['glove'],'hand2','automatic',out)[1],'glove_live')

    def test_legacy_target_and_constrained_choices_share_the_static_geometry_contract(self):
        for mode in ('automatic','target','constrained'):
            state=fixture();state['teleoperation_view_mode']=mode
            out=self.owner.update(state,now=10.)
            self.assertEqual(pose(state['glove'],'hand2',mode,out)[1],'glove_constrained')
            self.assertTrue(out['geometry_only']);self.assertEqual(out['geometry_pairs_verified'],290)
            self.assertIsNone(pose(state['glove'],'hand2',mode)[0])

    def test_source_short_timeout_is_rechecked_after_model_creation_and_geometry(self):
        with patch('pose_view.time.perf_counter',side_effect=[0.,0.,.3]):
            out=self.owner.update(self.state,now=10.)
        self.assertEqual(out['status'],'blocked');self.assertNotIn('display_q',out)
        self.assertFalse(self.created[0].closed)

    def test_slow_initialization_cannot_publish_old_frame_or_trigger_reinitialization_loop(self):
        with patch('pose_view.time.perf_counter',side_effect=[0.,.6]):
            out=self.owner.update(self.state,now=10.)
        self.assertEqual(out['status'],'blocked');self.assertNotIn('display_q',out)
        self.assertEqual(len(self.created),1);self.assertEqual(self.created[0].calls,[])
        self.assertFalse(self.created[0].closed);self.assertIsNone(self.owner.last_at)
        self.state['glove']['stream']['seq']=2
        result=self.owner.update(self.state,now=11.)
        self.assertEqual(result['status'],'ready');self.assertEqual(len(self.created),1)
        self.assertEqual(self.created[0].calls[0][1]['substeps'],10)
        self.assertAlmostEqual(self.owner.last_at,10.99)

    def test_temporary_source_expiry_reuses_only_the_same_context_and_new_frame_recovers(self):
        self.owner.update(self.state,now=10.);model=self.created[0]
        self.state['glove']['stream'].update(fresh=False,age_ms=300.)
        out=self.owner.update(self.state,now=10.3)
        self.assertEqual(out['status'],'blocked');self.assertFalse(model.closed)
        self.state['glove']['stream'].update(fresh=True,age_ms=1.,seq=2)
        self.assertEqual(self.owner.update(self.state,now=10.4)['status'],'ready')
        self.assertEqual(len(self.created),1)
        self.state['glove']['stream'].update(fresh=False);self.state['glove']['session_id']=8
        self.owner.update(self.state,now=10.5)
        self.assertTrue(model.closed)

    def test_post_render_context_guard_rejects_disconnection_or_feedback_handover(self):
        state=fixture();state['teleoperation_view_mode']='automatic'
        out=self.owner.update(state,now=10.)
        self.assertTrue(self.owner.still_current(state,out))
        state['glove']['stream']['seq']=2
        self.assertTrue(self.owner.still_current(state,out))
        for edit in (lambda g:g.update(session_id=8),lambda g:g.update(connection='disconnected'),
                lambda g:g.update(feedback=dict(device_id='HAND')),lambda g:g['user'].update(user_id='other')):
            changed=copy.deepcopy(state);edit(changed['glove'])
            self.assertFalse(self.owner.still_current(changed,out))


class _StopLoop(BaseException):pass


class RendererContractTests(unittest.TestCase):
    def run_once(self,state,after_render=None):
        class Vector(list):
            def __setitem__(self,key,value):
                if isinstance(key,slice) and isinstance(value,(int,float)):
                    value=[value]*len(range(*key.indices(len(self))))
                super().__setitem__(key,value)
        class Axis:
            def __iter__(self):return iter(range(20))
            def astype(self,_):return list(range(20))
        class Actuators:
            def __getitem__(self,_):return Axis()
        model=types.SimpleNamespace(actuator_trnid=Actuators(),jnt_qposadr=list(range(20)),body_quat=[None,None],
            joint=lambda j:types.SimpleNamespace(name=f'axis{j}'),vis=types.SimpleNamespace(global_=types.SimpleNamespace(),
                headlight=types.SimpleNamespace(ambient=[0]*3,diffuse=[0]*3)))
        data=types.SimpleNamespace(qpos=Vector([0.]*20),xanchor=[[0.,0.,0.]]*20)
        scene=types.SimpleNamespace(ngeom=0,maxgeom=100,geoms=[types.SimpleNamespace() for _ in range(100)])
        renderer=types.SimpleNamespace(scene=scene,update_scene=Mock(),render=Mock(return_value='synthetic-rgb'),close=Mock())
        if after_render is not None:
            def rendered():
                after_render(state)
                return 'synthetic-rgb'
            renderer.render.side_effect=rendered
        mj=types.SimpleNamespace(__version__='3.3.7',MjData=lambda _:data,Renderer=lambda *a,**k:renderer,
            MjvCamera=lambda:types.SimpleNamespace(lookat=[0]*3),MjvOption=lambda:types.SimpleNamespace(geomgroup=[1]*6),
            mj_forward=Mock(),mjtGeom=types.SimpleNamespace(mjGEOM_SPHERE=0),mjv_initGeom=Mock())
        np=types.SimpleNamespace(array=lambda x:x,eye=lambda _:types.SimpleNamespace(ravel=lambda:[]))
        image=types.SimpleNamespace(fromarray=lambda _:types.SimpleNamespace(save=lambda out,**kw:out.write(b'synthetic-constrained-jpeg')))
        obj=PoseView.__new__(PoseView);obj.controller=types.SimpleNamespace(snapshot=lambda:copy.deepcopy(state))
        obj.lock=threading.Lock();obj.jpeg=b'previous-image';obj.meta={}
        calls=[]
        def demand():
            calls.append(1)
            if len(calls)>2:raise _StopLoop()
        obj.wait_for_demand=demand
        created=[]
        def factory(runtime,p,s,assets,**kw):
            preview=FakePreview(p,s);created.append(preview);return preview
        fake_modules={'mujoco':mj,'numpy':np,'PIL':types.SimpleNamespace(Image=image),
                      'demo_player':types.SimpleNamespace(PoseLibrary=lambda *a:None)}
        with (patch.dict(sys.modules,fake_modules),patch('device_profiles.load_native_model',return_value=model),
              patch('collision_preview.create_display_preview',side_effect=factory),patch('pose_view.time.sleep')):
            with self.assertRaises(_StopLoop):obj.run()
        return obj,mj,data,renderer,created

    def test_blocked_renderer_keeps_old_jpeg_and_publishes_unready_error(self):
        state=fixture();state['glove']['stream']['fresh']=False
        obj,mj,data,renderer,created=self.run_once(state)
        self.assertEqual(obj.jpeg,b'previous-image');self.assertFalse(obj.meta['ready'])
        self.assertEqual(obj.meta['mode'],'glove_constrained_unavailable')
        renderer.render.assert_not_called();mj.mj_forward.assert_not_called();self.assertEqual(created,[])

    def test_ready_renderer_maps_only_separate_display_angles_and_keeps_targets(self):
        state=fixture();before=copy.deepcopy(state)
        obj,mj,data,renderer,created=self.run_once(state)
        self.assertTrue(obj.meta['ready']);self.assertEqual(obj.meta['mode'],'glove_constrained')
        self.assertTrue(obj.meta['no_physics_step']);self.assertTrue(obj.meta['no_motion_commands'])
        self.assertTrue(obj.meta['preview_only']);self.assertEqual(obj.meta['active_joints'],0)
        self.assertEqual(data.qpos,[.05]*20);self.assertEqual(state,before)
        self.assertIn('碰撞约束',obj.meta['message']);renderer.render.assert_called_once()
        self.assertTrue(created[0].closed)

    def test_automatic_stale_feedback_preserves_old_jpeg_without_render_or_physics(self):
        state=fixture();state['teleoperation_view_mode']='automatic'
        state['glove']['feedback']=dict(device_id='SYNTHETIC-HAND',stale=True,latest=None)
        obj,mj,data,renderer,created=self.run_once(state)
        self.assertEqual(obj.jpeg,b'previous-image');self.assertFalse(obj.meta['ready'])
        self.assertEqual(obj.meta['mode'],'glove_live');self.assertIsNone(obj.meta['source_seq'])
        renderer.render.assert_not_called();mj.mj_forward.assert_not_called();self.assertEqual(created,[])

    def test_automatic_invalid_target_preserves_old_jpeg_without_render_or_physics(self):
        state=fixture();state['teleoperation_view_mode']='automatic';state['glove']['stream']['q']=[.1]*19
        obj,mj,data,renderer,created=self.run_once(state)
        self.assertEqual(obj.jpeg,b'previous-image');self.assertFalse(obj.meta['ready'])
        self.assertEqual(obj.meta['mode'],'glove_constrained_unavailable');self.assertIsNone(obj.meta['source_seq'])
        renderer.render.assert_not_called();mj.mj_forward.assert_not_called();self.assertEqual(created,[])

    def test_same_sequence_payload_mutated_during_render_cannot_publish_the_old_pose(self):
        state=fixture();state['teleoperation_view_mode']='automatic'
        obj,mj,data,renderer,created=self.run_once(state,lambda current:current['glove']['stream']['q'].__setitem__(0,.2))
        self.assertEqual(obj.jpeg,b'previous-image');self.assertFalse(obj.meta['ready'])
        self.assertEqual(obj.meta['mode'],'glove_constrained_unavailable');renderer.render.assert_called_once()

    def test_glove_profile_conflict_during_render_cannot_publish_a_matching_device_label(self):
        state=fixture();state['teleoperation_view_mode']='automatic'
        obj,mj,data,renderer,created=self.run_once(state,lambda current:current['glove'].update(profile='hand1_left'))
        self.assertEqual(obj.jpeg,b'previous-image');self.assertFalse(obj.meta['ready'])
        self.assertEqual(obj.meta['mode'],'glove_constrained_unavailable');renderer.render.assert_called_once()

    def test_newer_finite_source_frame_does_not_prevent_a_still_fresh_render(self):
        state=fixture();state['teleoperation_view_mode']='automatic'
        def newer(current):current['glove']['stream'].update(seq=2,q=[.2]*20)
        obj,mj,data,renderer,created=self.run_once(state,newer)
        self.assertTrue(obj.meta['ready']);self.assertEqual(obj.meta['mode'],'glove_constrained')
        self.assertEqual(obj.jpeg,b'synthetic-constrained-jpeg');renderer.render.assert_called_once()

    def test_newer_fresh_frame_does_not_extend_an_expired_result_during_late_render(self):
        state=fixture();state['teleoperation_view_mode']='automatic';clock=Mock(return_value=100.)
        def late(current):
            clock.return_value=100.3
            current['glove']['stream'].update(seq=2,age_ms=0.,fresh=True)
        with patch('pose_view.time.monotonic',clock):obj,mj,data,renderer,created=self.run_once(state,late)
        self.assertEqual(obj.jpeg,b'previous-image');self.assertFalse(obj.meta['ready'])
        self.assertEqual(obj.meta['mode'],'glove_constrained_unavailable');renderer.render.assert_called_once()


if __name__=='__main__':unittest.main()
