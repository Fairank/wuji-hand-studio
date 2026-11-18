"""Real profile/catalog/snapshot producers; local replay data, no SDK or motion."""
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace as N
import unittest
from unittest.mock import patch

from console_server import Handler
from demonstration_scope import DemonstrationScope
from device_profiles import PROFILES, profile
from gesture_library import CATALOG, catalog
from official_replay import COMMIT, SOURCE_URL, load_replay, preview_pose
from demo_player import DemoPlayer


class DemonstrationProvenanceTests(unittest.TestCase):
    def request(self, pid):
        handler = object.__new__(Handler)
        handler.path = '/api/catalog?profile=' + pid
        handler.allowed_host = lambda: True
        handler.server = N(controller=N(state={'device_profile': profile('hand2_left')}))
        replies = []
        handler.reply = lambda status, body, **kw: replies.append((status, body))
        handler.do_GET()
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0][0], 200)
        return replies[0][1]

    def assert_recording(self, value, side):
        path = Path(__file__).with_name('official_data') / (side + '.replay')
        self.assertEqual(value['recording_side'], side)
        self.assertEqual(value['source_file'], path.name)
        self.assertEqual(value['source_url'], SOURCE_URL)
        self.assertEqual(value['source_commit'], COMMIT)
        self.assertEqual(value['source_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_actual_catalog_handler_labels_each_hand2_side_and_exact_recording(self):
        for side, zh in [('left', '左'), ('right', '右')]:
            with self.subTest(side=side):
                value = self.request('hand2_' + side)
                self.assertEqual(value['device_profile']['id'], 'hand2_' + side)
                action = next(a for a in value['actions'] if a['id'] == 'official_opposition')
                self.assertEqual(action['zh'], '官方' + zh + '手对指录制')
                self.assertEqual(action['en'], 'Official ' + side + ' opposition replay')
                self.assert_recording(action, side)
                self.assertIn(side.title() + ' recording', value['official_inventory'][0]['detail'])

    def test_hand1_catalog_only_exposes_backend_supported_hardware_actions(self):
        for pid in ('hand1_left', 'hand1_right'):
            with self.subTest(profile=pid):
                value = self.request(pid)
                self.assertEqual({a['id'] for a in value['actions'] if a['hardware']}, {'open', 'fist'})
                action = next(a for a in value['actions'] if a['id'] == 'official_opposition')
                self.assertIn('候选', action['zh'])
                self.assertIn('preview', action['en'])
                self.assertNotEqual(action['source'], 'official')
                self.assertNotIn('source_sha256', action)
                self.assertNotIn('recording_side', action)
                self.assertEqual(value['official_inventory'][0]['status'], 'preview_only')

    def test_scoped_snapshots_match_all_four_actual_preview_producers(self):
        scope = DemonstrationScope()
        for pid in PROFILES:
            scope.command(dict(name='demo_start', display_profile=pid,
                               action='official_opposition', speed=.5, cycles=1))
        for pid, value in scope.snapshot().items():
            with self.subTest(profile=pid):
                self.assertEqual(value['display_profile'], pid)
                self.assertFalse(value['hardware_motion'])
                if pid.startswith('hand2'):
                    side = pid.split('_')[1]
                    self.assertEqual(value['source'], 'official_' + side + '_recording_model_preview')
                    self.assertIn('左' if side == 'left' else '右', value['label'])
                    self.assert_recording(value, side)
                else:
                    self.assertEqual(value['source'], 'project_hand1_geometry_candidate_preview')
                    self.assertIn('候选', value['label'])
                    self.assertNotIn('source_sha256', value)
                    self.assertNotIn('recording_side', value)

    def test_profile_catalog_reads_do_not_mutate_other_or_legacy_catalogs(self):
        before = copy.deepcopy(CATALOG)
        right = self.request('hand2_right')
        right['actions'][0]['zh'] = 'caller edit'
        first = self.request('hand1_right')
        first['actions'][1]['hardware'] = False
        self.assertEqual(CATALOG, before)
        self.assertEqual(catalog()['actions'], before)
        self.assertNotIn('device_profile', catalog())
        self.assertEqual(self.request('hand2_left')['actions'][0]['zh'], '官方左手对指录制')
        self.assertEqual(self.request('hand2_right')['actions'][0]['zh'], '官方右手对指录制')

    def test_right_preview_keeps_its_own_data_and_not_a_left_mirror(self):
        right, left = load_replay('right'), load_replay('left')
        self.assertEqual(preview_pose(5., 'right'), list(right[5000]))
        self.assertNotEqual(preview_pose(5., 'right'), list(left[5000]))

    def test_same_legacy_player_read_uses_current_profile_without_restarting_or_retaining_source(self):
        player = DemoPlayer()
        player.command(dict(name='demo_start', action='official_opposition', speed=.5, cycles=1))
        run = player.run_id
        for pid in ('hand2_right', 'hand1_left', 'hand2_left', 'hand1_right'):
            with self.subTest(profile=pid):
                value = player.snapshot(profile_id=pid)
                self.assertEqual(value['run_id'], run)
                self.assertTrue(value['active'])
                if pid.startswith('hand1'):
                    self.assertNotIn('source_sha256', value)
                else:
                    self.assert_recording(value, pid.split('_')[1])

    def test_right_hardware_plan_labels_and_provenance_match_its_existing_side_path(self):
        from hardware_trial import make_trial
        points = [dict(t=0., q=[0.]*20, label='start'), dict(t=1., q=[0.]*20, label='return')]
        with patch('device_profiles.controller_profile', return_value=profile('hand2_right')), \
                patch('official_replay.build_points', return_value=points) as build:
            plan = make_trial([0.]*20, 'official_opposition', .25, 1)
        build.assert_called_once()
        self.assertIn('右', plan['label'])
        self.assertEqual(plan['source'], 'official_wuji_hand2_right_recording_retimed')
        self.assert_recording(plan, 'right')


if __name__ == '__main__':
    unittest.main()
