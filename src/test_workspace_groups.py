"""Focused WorkspaceGroups regressions using in-memory fakes and temp folders (no processes)."""
import copy
import json
import tempfile
import threading
import unittest
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import device_profiles
from workspace_groups import WorkspaceGroups


def ident(value):
    return value['id'] if isinstance(value, dict) else value


def profile_dict(profile):
    found = device_profiles.profile(ident(profile))
    return {key: found[key] for key in ('id', 'side', 'generation')}


def new_state(profile='hand2_left'):
    return {'connection': 'disconnected', 'stale': True, 'device_id': None,
            'device_profile': profile_dict(profile), 'hardware': {'active': False},
            'program': {'active': False}, 'glove': {'busy': False}, 'group': {'active': False}, 'message': ''}


def apply_command(state, command):
    name = command.get('name')
    if name == 'device_profile_select':
        state['device_profile'] = profile_dict(next(v for k, v in command.items() if 'profile' in k and v))
    elif name == 'connect':
        state['connection'] = 'connecting'
    return {'ok': True}  # session_close and every other command


def rows(groups):
    return {w['id']: [ident(m) for m in w['members']] for w in groups.snapshot()['workspaces']}


class FakeController:
    def __init__(self):
        self.factory, self.state = None, new_state('hand2_left')  # factory exists but stays unused

    def snapshot(self):
        return copy.deepcopy(self.state)

    def action(self, command):
        return apply_command(self.state, command)


class FakeFleet:
    def __init__(self):
        self.children, self.lock, self._states = {}, threading.RLock(), {}  # _states: private, by id
        self.created, self.removed, self.refuse_remove = [], [], False
        self.commands=[]

    def create(self, label, profile):
        self.created.append((label, profile))  # recorded first so unvalidated calls stay visible
        sid = f'{0x5EED0000 + len(self.created):016x}'  # distinct 16-hex id, no process
        self._states[sid] = new_state(profile)
        self.children[sid] = {'id': sid, 'label': label, 'profile': profile, 'port': 47000 + len(self.created)}
        return self.children[sid]

    def public(self, record):
        return {key: record[key] for key in ('id', 'label', 'profile', 'port')}

    def get(self, record, path='/api/state'):
        return copy.deepcopy(self._states[ident(record)])

    def post(self, record, command):
        self.commands.append(copy.deepcopy(command))
        return apply_command(self._states[ident(record)], command)

    def remove(self, session_id):
        self.removed.append(session_id)
        if self.refuse_remove or session_id not in self.children:
            return {'removed': False}
        del self.children[session_id], self._states[session_id]
        return {'removed': True}


class WorkspaceGroupsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()  # fresh folder for every test
        self.addCleanup(temp.cleanup)
        self.folder, self.fleet, self.controller = Path(temp.name), FakeFleet(), FakeController()
        self.groups = self.reload()

    def reload(self):
        return WorkspaceGroups(self.fleet, self.controller, self.folder)

    def act(self, name, **fields):
        return self.groups.action(dict(fields, name=name))

    def make_ws(self, label):
        return self.act('workspace_create', label=label)['workspace']['id']

    def add(self, wid, profile):
        return ident(self.act('workspace_add', workspace=wid, preview_profile=profile)['member'])

    def move(self, wid, member):
        return self.act('workspace_move', workspace=wid, member=member)

    def test_membership_persists_across_constructor_reload(self):
        self.assertEqual(rows(self.groups), {'main': ['main']})
        bench, spare = self.make_ws('Bench'), self.make_ws('Spare')
        left, right = self.add(bench, 'hand2_left'), self.add(bench, 'hand2_right')
        self.move(spare, right)
        json.loads((self.folder / 'hand_workspaces.json').read_text(encoding='utf-8'))  # valid JSON on disk
        self.assertEqual(self.reload().snapshot()['workspaces'], self.groups.snapshot()['workspaces'])
        self.assertEqual(rows(self.reload()), {'main': ['main'], bench: [left], spare: [right]})

    def test_each_hand_belongs_to_exactly_one_workspace(self):
        a, b = self.make_ws('A'), self.make_ws('B')
        left, right = self.add(a, 'hand2_left'), self.add(b, 'hand2_right')
        for target, member in ((b, left), (a, left), (a, right)):
            self.move(target, member)
        for target, member in (('no-such-workspace', left), (b, 'f' * 16)):
            with self.assertRaises(ValueError, msg=f'{target}/{member}'):
                self.move(target, member)
        current = rows(self.groups)
        self.assertEqual(sorted(m for ms in current.values() for m in ms), sorted(['main', left, right]))
        self.assertEqual((current['main'], set(current[a]), current[b]), (['main'], {left, right}, []))

    def test_invalid_labels_are_rejected_without_side_effects(self):
        for fields in ({'label': ''}, {'label': '   '}, {'label': '\t\n'}, {'label': None}, {}):
            with self.assertRaises(ValueError, msg=repr(fields)):
                self.act('workspace_create', **fields)
        self.assertEqual(rows(self.groups), {'main': ['main']})
        self.assertEqual(rows(self.reload()), {'main': ['main']})
        created = self.act('workspace_create', label='  Bench  ')['workspace']
        self.assertEqual((created['label'].strip(), created['members']), ('Bench', []))

    def test_invalid_preview_profile_follows_device_profiles_validation(self):
        wid = self.make_ws('A')
        for bad in ('hand9_middle', 'not-a-profile'):
            with self.assertRaises(Exception, msg=f'device_profiles accepted {bad!r}') as oracle:
                device_profiles.profile(bad)
            with self.assertRaises((ValueError, type(oracle.exception)), msg=bad):
                self.act('workspace_add', workspace=wid, preview_profile=bad)
        self.assertEqual((self.fleet.created, rows(self.groups)), ([], {'main': ['main'], wid: []}))
        member = self.add(wid, 'hand2_right')  # a profile device_profiles accepts still works
        self.assertEqual(rows(self.groups)[wid], [member])

    def test_create_empty_then_add_both_hands_without_connecting(self):
        created = self.act('workspace_create', label='Pair')['workspace']
        wid, members = created['id'], []
        self.assertEqual((wid != 'main', created['members'], rows(self.groups)[wid]), (True, [], []))
        for profile in ('hand2_left', 'hand2_right'):
            result = self.act('workspace_add', workspace=wid, preview_profile=profile)
            self.assertEqual((result['connecting'], ident(result['workspace'])), (False, wid))
            members.append(ident(result['member']))
        self.assertEqual(rows(self.groups), {'main': ['main'], wid: members})
        self.assertEqual(sorted(members), sorted(self.fleet.children))
        self.assertEqual([ident(p) for _, p in self.fleet.created], ['hand2_left', 'hand2_right'])
        self.assertEqual(self.controller.state['connection'], 'disconnected')
        self.assertNotIn('connect',[c['name'] for c in self.fleet.commands])
        snap = self.groups.snapshot()
        self.assertLessEqual({'workspaces', 'endpoints', 'actions', 'discovery'}, set(snap))
        endpoints = snap['endpoints']
        for member, side in zip(members, ('left', 'right')):
            self.assertEqual(self.fleet.get(member)['connection'], 'disconnected')  # created only
            found = endpoints[member] if isinstance(endpoints, dict) else next(
                e for e in endpoints if member in e.values())
            self.assertEqual((found['side'], found['generation'], found['connected']), (side, 'hand2', False))
            self.assertIn('serial', found)

    def test_busy_hand_cannot_move_until_idle(self):
        a, b = self.make_ws('A'), self.make_ws('B')
        member = self.add(a, 'hand2_right')
        record, before = copy.deepcopy(self.fleet.children[member]), self.fleet.get(member)
        busy = (('hardware', 'active'), ('program', 'active'), ('glove', 'busy'), ('group', 'active'))
        for section, flag in busy:
            self.fleet._states[member][section][flag] = True
            with self.assertRaises(ValueError, msg=f'{section}.{flag}'):
                self.move(b, member)
            self.fleet._states[member][section][flag] = False
        self.groups.coordinators[a] = SimpleNamespace(snapshot=lambda: {'active': True})  # no timers
        with self.assertRaises(ValueError):
            self.move(b, member)
        self.assertEqual(rows(self.groups)[a], [member])
        self.groups.coordinators.pop(a, None)
        self.move(b, member)
        self.assertEqual(rows(self.groups), {'main': ['main'], a: [], b: [member]})
        self.assertEqual((self.fleet.children[member], self.fleet.get(member)), (record, before))

    def test_remove_refuses_populated_main_and_unknown_workspaces(self):
        full, empty = self.make_ws('Full'), self.make_ws('Empty')
        member = self.add(full, 'hand2_left')
        for wid in ('main', full, 'no-such-workspace'):
            with self.assertRaises(ValueError, msg=wid):
                self.act('workspace_remove', workspace=wid)
        self.assertEqual(rows(self.groups), {'main': ['main'], full: [member], empty: []})
        self.act('workspace_remove', workspace=empty)
        self.assertEqual(rows(self.groups), {'main': ['main'], full: [member]})
        self.assertEqual(rows(self.reload()), {'main': ['main'], full: [member]})

    def test_discovered_serial_cannot_be_added_twice_while_connecting(self):
        import time
        wid=self.make_ws('Discovered pair')
        self.groups.discovery.state.update(updated=time.time(),devices=[dict(serial='mock-serial',generation='hand2',side_hint='left')])
        first=self.act('workspace_add',workspace=wid,serial='mock-serial')
        self.assertTrue(first['connecting'])
        self.assertEqual(self.fleet.get(first['member'])['connection'],'connecting')
        with self.assertRaisesRegex(ValueError,'already connecting'):
            self.act('workspace_add',workspace=wid,serial='mock-serial')
        self.assertEqual(len(self.fleet.created),1)
        self.assertEqual(rows(self.groups)[wid],[first['member']])

    def test_forgotten_child_leaves_no_orphan(self):
        wid = self.make_ws('A')
        keep, gone = self.add(wid, 'hand2_left'), self.add(wid, 'hand2_right')
        with self.assertRaises(ValueError):
            self.act('workspace_forget', workspace='main', member='main')
        self.fleet.refuse_remove = True
        with suppress(ValueError):  # a refused removal may report or raise, but the row must stay
            self.act('workspace_forget', workspace=wid, member=gone)
        self.assertEqual(rows(self.groups)[wid], [keep, gone])
        self.fleet.refuse_remove = False
        self.act('workspace_forget', workspace=wid, member=gone)
        self.assertEqual((set(self.fleet.removed), gone in self.fleet.children), ({gone}, False))
        self.assertEqual(rows(self.groups), {'main': ['main'], wid: [keep]})
        self.assertNotIn(gone, json.dumps(self.groups.snapshot(), default=str))
        self.assertEqual(rows(self.reload()), {'main': ['main'], wid: [keep]})
        self.assertNotIn(gone, (self.folder / 'hand_workspaces.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
