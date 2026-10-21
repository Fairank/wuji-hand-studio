"""Pure device inventory tests: no CLI, SDK, device or discovery operation."""
import copy
import unittest

from device_inventory import normalize_devices, build_inventory, merge_inventories


LEFT = 'WH2JA00000000001'
RIGHT = 'WH2KA00000000002'
GLOVE = 'WG1KA00000000003'
NOW = 1000


class InventoryTests(unittest.TestCase):
    def normalize(self, rows, **kwargs):
        return normalize_devices(rows, source=kwargs.pop('source', 'official_cli'),
                                 observed_at=kwargs.pop('observed_at', NOW), now=NOW, **kwargs)

    def test_actual_cli_fields_identify_kind_generation_and_side(self):
        result = self.normalize([
            dict(sn=RIGHT, device_type='wuji_hand_2', handedness='right', address='192.168.1.111:7447', transport='Udp'),
            dict(sn=GLOVE, device_type='wuji_glove', handedness='right', ip='192.168.1.101:50001'),
            dict(serial='FIRST-GENERATION', type='DeviceType.WujiHand', handedness='Handedness.Left', address='usb://test'),
        ])
        rows = {row['serial']: row for row in result['devices']}
        self.assertEqual((rows[RIGHT]['kind'], rows[RIGHT]['generation'], rows[RIGHT]['side']), ('hand', 'hand2', 'right'))
        self.assertEqual((rows[GLOVE]['kind'], rows[GLOVE]['generation'], rows[GLOVE]['side']), ('glove', 'glove1', 'right'))
        self.assertEqual(rows[GLOVE]['generation_source'], 'serial_hint')
        self.assertEqual(rows['FIRST-GENERATION']['generation'], 'hand1')
        self.assertTrue(all(row['side_source'] == 'device' for row in rows.values()))
        self.assertTrue(all(row['official_available'] and row['selectable'] and row['passive'] for row in rows.values()))
        self.assertTrue(all(row['identity_verified'] is False for row in rows.values()))

    def test_serial_hints_are_explicit_and_never_verified(self):
        result = self.normalize([dict(serial=LEFT), dict(sn=GLOVE)])
        rows = {row['serial']: row for row in result['devices']}
        self.assertEqual(rows[LEFT]['side'], 'left')
        self.assertEqual(rows[LEFT]['side_source'], 'serial_hint')
        self.assertEqual(rows[LEFT]['identity_source'], 'serial_hint')
        self.assertFalse(rows[LEFT]['identity_verified'])
        self.assertIsNone(rows[GLOVE]['side'])
        self.assertIsNone(rows[GLOVE]['side_source'])

    def test_explicit_device_side_wins_over_serial_side_hint(self):
        row = self.normalize([dict(sn=LEFT, device_type='wuji_hand_2', handedness='right')])['devices'][0]
        self.assertEqual(row['side'], 'right')
        self.assertEqual(row['side_source'], 'device')
        self.assertFalse(row['identity_verified'])

    def test_duplicate_transports_are_merged_without_losing_addresses(self):
        rows = [dict(sn=RIGHT, model='Wuji Hand 2', side='right', address='zenoh://' + RIGHT),
                dict(serial=RIGHT, type='wuji_hand_2', handedness='right', address='192.168.1.111:7447')]
        result = self.normalize(rows)
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(len(result['devices']), 1)
        self.assertEqual(result['devices'][0]['addresses'], ['192.168.1.111:7447', 'zenoh://' + RIGHT])
        self.assertEqual(result['devices'][0]['sn'], result['devices'][0]['serial'])

    def test_explicit_duplicate_side_kind_or_generation_conflicts_are_excluded(self):
        for other in (dict(sn=RIGHT, type='wuji_hand_2', side='left'),
                      dict(sn=RIGHT, type='wuji_glove', side='right'),
                      dict(sn=RIGHT, type='wuji_hand', side='right')):
            with self.subTest(other=other):
                result = self.normalize([dict(sn=RIGHT, type='wuji_hand_2', side='right'), other])
                self.assertEqual(result['devices'], [])
                self.assertEqual(result['conflicts'][0]['serial'], RIGHT)

    def test_conflicting_row_aliases_are_rejected_without_becoming_two_devices(self):
        result = self.normalize([dict(sn=RIGHT, serial=LEFT), dict(sn=RIGHT, type='wuji_glove', model='Wuji Hand 2'),
                                 dict(sn=GLOVE, handedness='left', side='right')])
        self.assertEqual(result['devices'], [])
        self.assertEqual(len(result['rejected']), 3)

    def test_expired_missing_future_or_invalid_timestamps_are_not_selectable(self):
        for seen in (NOW - 30, NOW - 31, None, NOW + 1, float('inf'), True):
            with self.subTest(seen=seen):
                row = self.normalize([dict(sn=RIGHT)], observed_at=seen)['devices'][0]
                self.assertTrue(row['stale'])
                self.assertFalse(row['selectable'])
                self.assertFalse(row['official_available'])
        row = self.normalize([dict(sn=RIGHT)], observed_at=NOW - 29.99)['devices'][0]
        self.assertFalse(row['stale'])
        self.assertEqual(row['last_seen'], NOW - 29.99)
        self.assertEqual(row['expires_at'], NOW - 29.99 + 30)

    def test_unknown_devices_remain_visible_but_not_selectable(self):
        row = self.normalize([dict(sn='UNKNOWN-SYNTHETIC', device_type='Unknown')])['devices'][0]
        self.assertEqual(row['kind'], 'unknown')
        self.assertIsNone(row['generation'])
        self.assertFalse(row['selectable'])
        self.assertEqual(row['model'], 'Unknown device')

    def test_invalid_records_do_not_coerce_numbers_or_print_raw_values(self):
        result = self.normalize([None, [], dict(sn=5), dict(sn='bad\nserial'), {}, dict(sn='x' * 101)])
        self.assertEqual(result['devices'], [])
        self.assertEqual(len(result['rejected']), 6)
        self.assertTrue(all(set(row) == {'index', 'source', 'reason'} for row in result['rejected']))

    def test_snapshot_sources_merge_and_are_not_mutated(self):
        viz = dict(devices=[dict(sn=RIGHT, type='wuji_hand_2', handedness='right')], scanned_at=NOW)
        controller = dict(devices=[dict(serial=RIGHT, generation='hand2', side_hint='right')], devices_at=NOW - 1)
        glove = dict(devices=[dict(serial=GLOVE, address='192.168.1.101:50001')], devices_at=NOW)
        before = copy.deepcopy((viz, controller, glove))
        result = build_inventory(viz, controller, glove, now=NOW)
        self.assertEqual((viz, controller, glove), before)
        rows = {row['serial']: row for row in result['devices']}
        self.assertEqual(rows[RIGHT]['sources'], ['controller_discovery', 'official_cli'])
        self.assertTrue(rows[RIGHT]['official_available'])
        self.assertFalse(rows[GLOVE]['official_available'])
        self.assertEqual(result['updated_at'], NOW)
        self.assertEqual(result['ttl_s'], 30)

    def test_legacy_lists_without_observation_time_are_stale(self):
        result = build_inventory(controller=dict(devices=[dict(serial=RIGHT, generation='hand2')]),
                                 glove=dict(devices=[dict(serial=GLOVE)]), now=NOW)
        self.assertTrue(all(row['stale'] and not row['selectable'] for row in result['devices']))

    def test_viz_error_does_not_reuse_freshness_of_previous_success(self):
        result = build_inventory(viz=dict(devices=[dict(sn=GLOVE, type='wuji_glove')], scanned_at=NOW, error='Scan failed'), now=NOW)
        row = result['devices'][0]
        self.assertTrue(row['stale'])
        self.assertFalse(row['official_available'])
        self.assertFalse(row['selectable'])
        self.assertEqual(row['last_seen'], NOW)

    def controller(self, stale=False):
        return dict(connection='connected', stale=stale, device_id=RIGHT,
                    device_profile=dict(id='hand2_right', generation='hand2', side='right'),
                    identified_device=dict(serial=RIGHT, profile='hand2_right', source='sdk_identity'))

    def test_exact_connected_sdk_identity_is_verified_in_use(self):
        row = build_inventory(controller=self.controller(), now=NOW)['devices'][0]
        self.assertTrue(row['identity_verified'])
        self.assertTrue(row['in_use'])
        self.assertFalse(row['stale'])
        self.assertFalse(row['official_available'])
        self.assertEqual(row['side_source'], 'device')

    def test_stale_connected_feedback_keeps_ownership_but_not_freshness(self):
        row = build_inventory(controller=self.controller(stale=True), now=NOW)['devices'][0]
        self.assertTrue(row['identity_verified'])
        self.assertTrue(row['in_use'])
        self.assertTrue(row['stale'])
        self.assertFalse(row['selectable'])

    def test_disconnected_mismatched_or_unverified_identity_is_not_reused(self):
        variants = [self.controller(), self.controller(), self.controller(), self.controller()]
        variants[0]['connection'] = 'disconnected'
        variants[1]['device_id'] = LEFT
        variants[2]['identified_device']['profile'] = 'hand2_left'
        variants[3]['identified_device']['source'] = 'serial_hint'
        for state in variants:
            with self.subTest(state=state):
                self.assertEqual(build_inventory(controller=state, now=NOW)['devices'], [])

    def test_actual_glove_snapshot_serial_and_profile_show_owned_side(self):
        glove = dict(busy=True, connection='receiving', glove_serial=GLOVE, profile='hand2_right', stream=dict(fresh=True))
        row = build_inventory(glove=glove, now=NOW)['devices'][0]
        self.assertEqual(row['kind'], 'glove')
        self.assertEqual(row['side'], 'right')
        self.assertTrue(row['identity_verified'] and row['in_use'])
        self.assertEqual(row['generation_source'], 'serial_hint')
        glove['stream']['fresh'] = False
        row = build_inventory(glove=glove, now=NOW)['devices'][0]
        self.assertTrue(row['in_use'] and row['stale'])
        glove['busy'] = False
        self.assertEqual(build_inventory(glove=glove, now=NOW)['devices'], [])

    def paired(self, stale=False):
        return dict(busy=True, connection='receiving', glove_serial=GLOVE, profile='hand2_right',
                    stream=dict(fresh=True), devices=[], hands=[],
                    feedback=dict(device_id=RIGHT, connection='connected', stale=stale,
                        identified_device=dict(serial=RIGHT, profile='hand2_right', source='sdk_identity')))

    def test_paired_hand_exact_sdk_identity_is_present_fresh_and_owned(self):
        paired = self.paired()
        before = copy.deepcopy(paired)
        rows = {row['serial']: row for row in build_inventory(glove=paired, now=NOW)['devices']}
        self.assertEqual(paired, before)
        self.assertEqual(set(rows), {RIGHT, GLOVE})
        hand = rows[RIGHT]
        self.assertEqual((hand['kind'], hand['generation'], hand['side']), ('hand', 'hand2', 'right'))
        self.assertTrue(hand['identity_verified'] and hand['in_use'] and hand['selectable'])
        self.assertFalse(hand['stale'] or hand['official_available'])
        self.assertEqual(hand['last_seen'], NOW)

    def test_paired_stale_hand_keeps_exact_ownership_without_becoming_selectable(self):
        for stale in (True, None):
            with self.subTest(stale=stale):
                paired = self.paired(stale)
                if stale is None:
                    paired['feedback'].pop('stale')
                rows = {row['serial']: row for row in build_inventory(glove=paired, now=NOW)['devices']}
                hand = rows[RIGHT]
                self.assertTrue(hand['identity_verified'] and hand['in_use'] and hand['stale'])
                self.assertFalse(hand['selectable'] or hand['official_available'])
                self.assertIsNone(hand['last_seen'])

    def test_paired_hand_without_matching_identity_is_never_manufactured(self):
        changes = [lambda state: state.update(busy=False),
                   lambda state: state['feedback'].update(connection='disconnected'),
                   lambda state: state['feedback'].pop('identified_device'),
                   lambda state: state['feedback']['identified_device'].update(serial=LEFT),
                   lambda state: state['feedback']['identified_device'].update(source='serial_hint'),
                   lambda state: state['feedback']['identified_device'].update(profile='unknown'),
                   lambda state: state['feedback']['identified_device'].update(profile='hand2_left')]
        for change in changes:
            paired = self.paired()
            change(paired)
            with self.subTest(paired=paired):
                self.assertNotIn(RIGHT, [row['serial'] for row in build_inventory(glove=paired, now=NOW)['devices']])

    def test_running_original_cli_does_not_manufacture_freshness_or_verification(self):
        row = build_inventory(viz=dict(devices=[dict(sn=GLOVE, type='wuji_glove')], scanned_at=NOW - 31,
                                       active=True, phase='running', device=dict(sn=GLOVE)), now=NOW)['devices'][0]
        self.assertTrue(row['in_use'])
        self.assertTrue(row['stale'])
        self.assertFalse(row['identity_verified'])
        self.assertEqual(row['last_seen'], NOW - 31)

    def test_cross_source_explicit_conflict_is_excluded(self):
        result = build_inventory(viz=dict(devices=[dict(sn=RIGHT, type='wuji_hand_2', side='left')], scanned_at=NOW),
                                 controller=self.controller(), now=NOW)
        self.assertEqual(result['devices'], [])
        self.assertEqual(result['conflicts'][0]['fields'], ['side'])

    def test_current_explicit_side_overrides_an_unverified_serial_hint(self):
        controller = self.controller()
        controller.update(device_id=LEFT, identified_device=dict(serial=LEFT, profile='hand2_right', source='sdk_identity'))
        row = build_inventory(viz=dict(devices=[dict(sn=LEFT)], scanned_at=NOW), controller=controller, now=NOW)['devices'][0]
        self.assertEqual(row['side'], 'right')
        self.assertEqual(row['side_source'], 'device')
        self.assertTrue(row['identity_verified'])

    def test_explicit_kind_does_not_inherit_incompatible_hint_side_or_generation(self):
        row = build_inventory(viz=dict(devices=[dict(sn=LEFT)], scanned_at=NOW),
                              glove=dict(devices=[dict(serial=LEFT, device_type='wuji_glove')], devices_at=NOW - 1), now=NOW)['devices'][0]
        self.assertEqual(row['kind'], 'glove')
        self.assertIsNone(row['generation'])
        self.assertIsNone(row['side'])
        self.assertIsNone(row['side_source'])
        self.assertEqual(row['identity_source'], 'glove_discovery')

    def test_claimed_verified_flag_on_official_or_incomplete_records_is_ignored(self):
        row = self.normalize([dict(sn=RIGHT, identity_verified=True)])['devices'][0]
        self.assertFalse(row['identity_verified'])
        row = self.normalize([dict(sn=RIGHT, identity_verified=True)], source='sdk_identity')['devices'][0]
        self.assertFalse(row['identity_verified'])

    def test_inputs_and_limits_are_bounded(self):
        for rows in (None, {}, [dict(sn=RIGHT)] * 513):
            with self.subTest(rows_type=type(rows)), self.assertRaises(ValueError):
                self.normalize(rows)
        for state in ([], '', 1):
            with self.subTest(state=state), self.assertRaises(ValueError):
                build_inventory(viz=state, now=NOW)
        for kwargs in (dict(now=True), dict(ttl=0), dict(ttl=float('inf'))):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_inventory(**kwargs)


class MergeInventoriesTests(unittest.TestCase):
    def official(self, *, seen=NOW, **kwargs):
        return build_inventory(viz=dict(devices=[dict(sn=RIGHT, device_type='wuji_hand_2', side='right', **kwargs)],
                                        scanned_at=seen), now=NOW)

    def test_workspace_directories_merge_without_mutating_or_losing_each_source_time(self):
        first = self.official(seen=NOW - 2, address='192.168.1.111:7447')
        second = build_inventory(controller=dict(devices=[dict(serial=RIGHT, generation='hand2', side='right',
                                                               address='zenoh://' + RIGHT)], devices_at=NOW - 1), now=NOW)
        before = copy.deepcopy([first, second])
        result = merge_inventories([first, second], now=NOW)
        self.assertEqual([first, second], before)
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(len(result['devices']), 1)
        row = result['devices'][0]
        self.assertEqual(row['sources'], ['controller_discovery', 'official_cli'])
        self.assertEqual(row['addresses'], ['192.168.1.111:7447', 'zenoh://' + RIGHT])
        self.assertEqual({item['source']: item['last_seen'] for item in row['observations']},
                         {'official_cli': NOW - 2, 'controller_discovery': NOW - 1})
        self.assertTrue(row['official_available'])
        self.assertFalse(row['identity_verified'])

    def test_source_times_not_directory_timestamp_control_official_expiry(self):
        original = self.official(seen=NOW - 25)
        first = merge_inventories([original], now=NOW + 1)
        second = merge_inventories([first], now=NOW + 5)
        row = second['devices'][0]
        self.assertEqual(row['last_seen'], NOW - 25)
        self.assertEqual(row['observations'][0]['last_seen'], NOW - 25)
        self.assertTrue(row['stale'])
        self.assertFalse(row['official_available'] or row['selectable'])

    def test_stale_official_source_cannot_borrow_fresh_controller_observation(self):
        first = self.official(seen=NOW - 31)
        second = build_inventory(controller=dict(devices=[dict(serial=RIGHT, generation='hand2', side='right')],
                                                  devices_at=NOW), now=NOW)
        row = merge_inventories([first, second], now=NOW)['devices'][0]
        self.assertFalse(row['stale'])
        self.assertTrue(row['selectable'])
        self.assertFalse(row['official_available'])
        observations = {item['source']: item for item in row['observations']}
        self.assertTrue(observations['official_cli']['stale'])
        self.assertEqual(observations['official_cli']['last_seen'], NOW - 31)
        self.assertEqual(observations['controller_discovery']['last_seen'], NOW)

    def test_failed_scan_cannot_become_fresh_during_directory_merge(self):
        failed = build_inventory(viz=dict(devices=[dict(sn=RIGHT, type='wuji_hand_2', side='right')],
                                         scanned_at=NOW, error='scan failed'), now=NOW)
        row = merge_inventories([failed], now=NOW)['devices'][0]
        self.assertTrue(row['stale'])
        self.assertFalse(row['official_available'] or row['selectable'])
        self.assertEqual(row['last_seen'], NOW)

    def test_conflicting_explicit_sides_across_workspaces_are_excluded(self):
        left = build_inventory(controller=dict(devices=[dict(sn=RIGHT, generation='hand2', side='left')], devices_at=NOW), now=NOW)
        result = merge_inventories([self.official(), left], now=NOW)
        self.assertEqual(result['devices'], [])
        self.assertEqual(result['conflicts'][0]['fields'], ['side'])
        self.assertEqual(result['conflicts'][0]['sources'], ['controller_discovery', 'official_cli'])

    def test_conflict_already_excluded_by_one_directory_is_not_resurrected(self):
        conflicted = build_inventory(viz=dict(devices=[dict(sn=RIGHT, type='wuji_hand_2', side=side)
                                                      for side in ('left', 'right')], scanned_at=NOW), now=NOW)
        result = merge_inventories([conflicted, self.official()], now=NOW)
        self.assertEqual(result['devices'], [])
        self.assertEqual(result['conflicts'][0]['serial'], RIGHT)

    def test_hint_provenance_does_not_become_explicit_or_verified_after_multiple_merges(self):
        hinted = build_inventory(viz=dict(devices=[dict(sn=LEFT)], scanned_at=NOW), now=NOW)
        other = build_inventory(glove=dict(devices=[dict(serial=LEFT, device_type='wuji_glove')], devices_at=NOW), now=NOW)
        result = merge_inventories([merge_inventories([hinted], now=NOW), other], now=NOW)
        self.assertEqual(result['conflicts'], [])
        row = result['devices'][0]
        self.assertEqual(row['kind'], 'glove')
        self.assertIsNone(row['generation'])
        self.assertIsNone(row['side'])
        self.assertFalse(row['identity_verified'])
        hinted_leaf = next(item for item in row['observations'] if item['source'] == 'official_cli')
        self.assertEqual(hinted_leaf['kind_source'], 'serial_hint')
        self.assertEqual(hinted_leaf['side_source'], 'serial_hint')

    def test_exact_owned_identity_survives_merge_but_does_not_refresh_official_source(self):
        controller = InventoryTests().controller(stale=True)
        current = build_inventory(controller=controller, now=NOW)
        row = merge_inventories([self.official(seen=NOW - 31), current], now=NOW)['devices'][0]
        self.assertTrue(row['in_use'] and row['identity_verified'] and row['stale'])
        self.assertFalse(row['official_available'] or row['selectable'])

    def test_duplicate_directories_do_not_multiply_identical_observations(self):
        original = self.official()
        result = merge_inventories([original, original], now=NOW)
        for _ in range(3):
            result = merge_inventories([result, original], now=NOW)
        self.assertEqual(len(result['devices'][0]['observations']), 1)

    def test_empty_directories_and_bounded_invalid_input(self):
        self.assertEqual(merge_inventories([], now=NOW)['devices'], [])
        for value in (None, {}, [None], [{}] * 65, [dict(devices={})]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_inventories(value, now=NOW)
        result = merge_inventories([dict(devices=[None, dict(serial=RIGHT, observations=[])])], now=NOW)
        self.assertEqual(result['devices'], [])
        self.assertEqual(len(result['rejected']), 2)


if __name__ == '__main__':
    unittest.main()
