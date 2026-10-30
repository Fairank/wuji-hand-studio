"""Official SDK targets reach the simulated renderer unchanged; all runtimes fake."""
import copy
import unittest
from unittest.mock import patch

from glove_view import pose
from pose_view import PoseView
import test_pose_collision_preview as graphics_fixture
fixture=graphics_fixture.fixture


def sdk_state():
    state=fixture();state['teleoperation_view_mode']='automatic'
    state['glove']['stream']['solver'].update(applied=True)
    return state


class SDKTargetDisplayTests(unittest.TestCase):
    def render(self,state,after=None):
        return graphics_fixture.RendererContractTests().run_once(state,after)

    def test_official_target_renders_exactly_and_never_creates_a_projection(self):
        state=sdk_state();before=copy.deepcopy(state)
        obj,mj,data,renderer,created=self.render(state)
        self.assertTrue(obj.meta['ready']);self.assertEqual(obj.meta['mode'],'glove_preview')
        self.assertEqual(data.qpos,state['glove']['stream']['q']);self.assertEqual(state,before)
        self.assertEqual(created,[]);self.assertNotIn('collision_preview',obj.meta)
        self.assertEqual(obj.meta['target_frame']['source'],'official_sdk')
        self.assertTrue(obj.meta['no_physics_step']);self.assertTrue(obj.meta['no_motion_commands'])
        renderer.render.assert_called_once()

    def test_legacy_target_and_constrained_choices_ignore_any_custom_projection(self):
        state=sdk_state();glove=state['glove'];before=copy.deepcopy(glove)
        for mode in (None,'automatic','target','constrained'):
            with self.subTest(mode=mode):
                q,kind,info=pose(glove,'hand2',mode,collision={'display_q':[0.]*20})
                self.assertEqual(q,glove['stream']['q']);self.assertEqual(kind,'glove_preview')
                self.assertEqual(info['target_frame']['source'],'official_sdk')
        self.assertEqual(glove,before)

    def test_large_valid_successive_targets_are_not_display_filtered_or_chased(self):
        values=((1,.05),(90,.7),(91,.02),(500,.4));state=sdk_state();at=[0]
        expected=[[value if i%4!=1 else .02 for i in range(20)] for _,value in values]
        state['glove']['stream'].update(seq=1,q=expected[0])
        def next_frame(current):
            at[0]+=1
            if at[0]<len(values):current['glove']['stream'].update(seq=values[at[0]][0],q=expected[at[0]])
        obj,_,data,renderer,created=graphics_fixture.RendererContractTests().run_once(state,next_frame,frames=len(values))
        self.assertTrue(obj.meta['ready']);self.assertEqual(renderer.poses,expected)
        self.assertEqual(data.qpos,expected[-1]);self.assertEqual(obj.meta['target_frame']['source_seq'],500)
        self.assertEqual(created,[])

    def test_stale_wrong_side_owner_unknown_sdk_and_invalid_limits_never_render(self):
        mutations=[lambda s:s['glove']['stream'].update(fresh=False),
            lambda s:s['glove']['stream'].update(age_ms=251),
            lambda s:s['glove'].update(profile='hand2_left'),
            lambda s:s['glove'].update(user={}),
            lambda s:s['glove']['stream']['solver'].update(engine='official_open'),
            lambda s:s['glove']['stream']['solver'].update(applied=False),
            lambda s:s['glove']['stream'].update(q=[float('nan')]*20),
            lambda s:s['glove']['stream'].update(q=[100.]*20)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                state=sdk_state();mutation(state);obj,mj,_,renderer,created=self.render(state)
                self.assertFalse(obj.meta['ready']);self.assertEqual(obj.jpeg,b'previous-image')
                renderer.render.assert_not_called();mj.mj_forward.assert_not_called();self.assertEqual(created,[])

    def test_post_render_user_or_same_sequence_payload_change_cannot_publish_old_target(self):
        for edit in (lambda s:s['glove']['user'].update(user_id='another-wearer'),
                     lambda s:s['glove']['stream']['q'].__setitem__(0,.2)):
            state=sdk_state();obj,_,_,renderer,_=self.render(state,edit)
            self.assertFalse(obj.meta['ready']);self.assertEqual(obj.jpeg,b'previous-image')
            renderer.render.assert_called_once()

    def test_newer_timely_sequence_does_not_starve_the_exact_original_target(self):
        state=sdk_state();raw=list(state['glove']['stream']['q'])
        def newer(s):s['glove']['stream'].update(seq=40,q=[.2]*20)
        obj,_,data,_,created=self.render(state,newer)
        self.assertTrue(obj.meta['ready']);self.assertEqual(data.qpos,raw)
        self.assertEqual(obj.meta['target_frame']['source_seq'],1);self.assertEqual(created,[])

    def test_runtime_render_function_has_no_custom_collision_dependency(self):
        import inspect
        source=inspect.getsource(PoseView.run)
        for forbidden in ('GloveCollisionPreview','create_display_preview','collision_preview','mapping_collision'):
            self.assertNotIn(forbidden,source)


if __name__=='__main__':unittest.main()
