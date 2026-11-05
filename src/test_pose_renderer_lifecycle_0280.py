"""Existing view workers serialize native context creation and destruction."""
import copy
import threading
import unittest
from types import SimpleNamespace as N
from unittest.mock import patch

import numpy as np

from pose_view import PoseView


class FrameComplete(BaseException):
    pass


class RendererLifecycleTests(unittest.TestCase):
    def exercise(self, blocked_operation, *, fail_first=False):
        entered = threading.Event()
        release = threading.Event()
        second_loaded = threading.Event()
        attempted_overlap = threading.Event()
        active_lock = threading.Lock()
        active = 0
        operations = []
        errors = []
        models = []
        demands = {}

        def operation(kind):
            nonlocal active
            with active_lock:
                if active:
                    attempted_overlap.set()
                    raise RuntimeError('Synthetic GLFW lifecycle overlap')
                active += 1
                operations.append((threading.current_thread().name, kind))
            try:
                if threading.current_thread().name == 'first-view' and kind == blocked_operation:
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('Test lifecycle release timed out')
                    if fail_first:
                        raise RuntimeError('Synthetic context construction failed')
            finally:
                with active_lock:
                    active -= 1

        class Renderer:
            def __init__(self, model, **kwargs):
                operation('create')
                self.scene = N(ngeom=0, maxgeom=0)

            def update_scene(self, *args, **kwargs):
                pass

            def render(self):
                return np.zeros((2, 2, 3), dtype=np.uint8)

            def close(self):
                operation('close')

        def load_model(profile_id):
            model = N(body_quat=np.zeros((2, 4)),
                      vis=N(global_=N(), headlight=N(ambient=np.zeros(3), diffuse=np.zeros(3))),
                      actuator_trnid=np.column_stack((np.arange(20), np.zeros(20))),
                      joint=lambda index: N(name='native_joint_' + str(index)))
            models.append(profile_id)
            if threading.current_thread().name == 'second-view':
                second_loaded.set()
            return model

        def state(profile_id):
            return dict(device_profile={'id': profile_id}, view_source='feedback',
                        latest=None, stale=True, metrics={}, joint_rates=[], mapping=[])

        class Source:
            def __init__(self, change):
                self.reads = 0
                self.change = change

            def snapshot(self):
                self.reads += 1
                return state('hand1_right' if self.change and self.reads > 1 else 'hand2_left')

        def worker(view):
            try:
                view.run()
            except FrameComplete:
                pass
            except BaseException as error:
                errors.append(error)

        def demand(view):
            demands[id(view)] = demands.get(id(view), 0) + 1
            if demands[id(view)] > 2:
                raise FrameComplete

        fake_mj = N(Renderer=Renderer,
                    MjData=lambda model: N(qpos=np.zeros(20)),
                    MjvCamera=lambda: N(lookat=np.zeros(3)),
                    MjvOption=lambda: N(geomgroup=np.zeros(6)),
                    mj_forward=lambda *args: None)
        markers = [dict(status='unmapped', hz=None) for _ in range(20)]
        with patch('pose_view.threading.Thread'):
            first = PoseView(Source(change=blocked_operation == 'switch-close'))
            second = PoseView(Source(change=False))
        if blocked_operation == 'switch-close':
            # The first close belongs to profile replacement rather than cleanup.
            blocked_operation = 'close'
        first_thread = threading.Thread(target=worker, args=(first,), name='first-view')
        second_thread = threading.Thread(target=worker, args=(second,), name='second-view')
        with patch.dict('sys.modules', {'mujoco': fake_mj}), \
                patch('device_profiles.load_native_model', side_effect=load_model), \
                patch('device_profiles.FirstGenerationPreview', return_value=N()), \
                patch('demo_player.PoseLibrary', return_value=N()), \
                patch.object(PoseView, 'wait_for_demand', demand), \
                patch('pose_view.apply_measured_pose', side_effect=lambda *args: (0, copy.deepcopy(markers))):
            try:
                first_thread.start()
                self.assertTrue(entered.wait(3), 'First worker did not reach the native lifecycle')
                second_thread.start()
                self.assertTrue(second_loaded.wait(3), 'Second worker did not reach context setup')
                # While the first native lifecycle is held, the second source
                # has loaded and can only wait before entering its constructor.
                attempted_overlap.wait(.1)
            finally:
                release.set()
                first_thread.join(3)
                if second_thread.ident is not None:
                    second_thread.join(3)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertFalse(attempted_overlap.is_set(), operations)
        if fail_first:
            self.assertFalse(first.meta['ready'])
            self.assertIn('Synthetic context construction failed', first.meta['message'])
        else:
            self.assertTrue(first.meta['ready'], first.meta)
            self.assertIsNotNone(first.jpeg)
            self.assertIn(('first-view', 'close'), operations)
        self.assertTrue(second.meta['ready'], second.meta)
        self.assertIsNotNone(second.jpeg)
        self.assertIn(('second-view', 'close'), operations)
        return models

    def test_cold_views_cannot_overlap_native_context_construction(self):
        self.exercise('create')

    def test_profile_replacement_close_cannot_overlap_another_constructor(self):
        self.assertIn('hand1_right', self.exercise('switch-close'))

    def test_final_close_cannot_overlap_another_constructor(self):
        self.exercise('close')

    def test_failed_constructor_releases_lifecycle_for_other_views(self):
        self.exercise('create', fail_first=True)


if __name__ == '__main__':
    unittest.main()
