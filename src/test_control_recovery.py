"""Independent fault injection. No app/SDK/WSL or device starts."""
import ast
import collections
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import queue
import re
import socket
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
QA = Path(os.environ.get('WUJI_CONTROL_QA_ROOT', str(ROOT.parents[1]/'outputs/workbench-upgrade-0219/control-review')))
sys.path.insert(0, str(ROOT/'src'))
from device_termination import TerminationJournal, unconfirmed
from glove_bridge import GloveBridge
from glove_agent import prepare_permission
from desktop_lifecycle import close_sessions
from calibration_handover import CalibrationHandover


def extracted(filename, name, context):
    path = ROOT/'src'/filename
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if getattr(n, 'name', None) == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), context)
    return context[name]


def idle_state():
    return dict(connection='disconnected', device_id='X', hardware=dict(active=False, stop_confirmed=True),
                glove=dict(busy=False, hardware={}, feedback={}), unconfirmed_devices=[],
                parameter_sync={}, calibration={}, recording={}, program={}, group={})


class JournalCrosscheck(unittest.TestCase):
    def setUp(self):
        (QA/'temp').mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=QA/'temp')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'termination.json'
        self.journal = TerminationJournal(self.path)
        self.fallback_epoch = 0

    def epoch(self, journal=None):
        journal = journal or self.journal
        if hasattr(journal, 'new_session'):
            return journal.new_session()
        self.fallback_epoch += 1
        return self.fallback_epoch

    def test_same_serial_old_ack_cannot_erase_newer_uncertainty(self):
        old, new = self.epoch(), self.epoch()
        self.journal.observe('hand', 'X', dict(active=None), old)
        self.journal.observe('hand', 'X', dict(active=None), new)
        self.journal.observe('hand', 'X', dict(active=False, stop_confirmed=True), old)
        self.assertTrue(self.journal.snapshot(), 'Old session acknowledged a newer session')
        self.journal.observe('hand', 'X', dict(active=False, stop_confirmed=True), new)
        self.assertEqual(self.journal.snapshot(), [])

    def test_restart_epoch_must_not_reuse_an_earlier_session(self):
        old = self.epoch()
        self.journal.observe('hand', 'X', dict(active=None), old)
        reopened = TerminationJournal(self.path)
        self.assertTrue(hasattr(reopened, 'new_session'), 'Session epochs must be persisted across restart')
        new = self.epoch(reopened)
        self.assertNotEqual(new, old)
        reopened.observe('hand', 'X', dict(active=None), new)
        reopened.observe('hand', 'X', dict(active=False, stop_confirmed=True), old)
        self.assertTrue(reopened.snapshot())

    def test_different_device_ack_never_clears_unknown_device(self):
        self.journal.observe('glove', 'X', dict(active=None), self.epoch())
        self.journal.observe('hand', 'Y', dict(active=False, stop_confirmed=True), self.epoch())
        self.assertTrue(self.journal.snapshot())

    def test_fresh_read_only_hand_status_cannot_acknowledge_earlier_disable_unknown(self):
        old = self.epoch()
        self.journal.observe('hand', 'X', dict(active=None), old)
        HardwareShowcase = extracted('hardware_showcase.py', 'HardwareShowcase', dict(
            PROFILE=QA/'synthetic-profile.json', load_profile=lambda *args: (None, 'synthetic read-only'),
            PublishTiming=lambda: NS(snapshot=lambda: {}), settings=lambda: {}, EXECUTION_VERSION=1))
        calls = []
        hand = NS(disable=lambda: calls.append('disable'))
        motion = HardwareShowcase(hand, 'X', lambda **kwargs: None)
        modules = {'group_timing': NS(clock_id=lambda: 'synthetic-clock'),
                   'group_hardware': NS(status=lambda driver: None)}
        with patch.dict(sys.modules, modules):
            hardware = motion.status()
        self.assertEqual(calls, [], 'Read-only construction unexpectedly issued a disable')
        self.journal.observe('hand', 'X', hardware, self.epoch())
        self.assertTrue(self.journal.snapshot(), 'A never-enabled read-only driver cleared a prior unknown')

    def test_first_generation_read_only_close_cannot_acknowledge_earlier_disable_unknown(self):
        self.journal.observe('hand', 'X', dict(active=None), self.epoch())
        calls = []
        hand = NS(serial_number='X', joint_states=lambda: NS(subscribe=lambda: NS(recv=lambda: None, close=lambda: None)),
                  disable=lambda: calls.append('disable'), disconnect=lambda: None)
        recorder = lambda: NS(active=False, finish=lambda *args: None)
        modules = {
            'wuji_sdk': NS(SdkManager=NS(instance=lambda: NS()), DeviceType=NS(WujiHand='hand1'),
                           JointCommand=lambda *args: None, LowPass=lambda *args: None),
            'console_agent': NS(Recorder=recorder),
            'device_profiles': NS(native_ranges=lambda profile: [(-1., 1.)]*20),
            'motion_parameters': NS(COMMAND_SPEED_RAD_S=1., PATH_SPEED_RAD_S=1.),
        }
        worker = extracted('first_generation.py', 'worker_first', dict(
            controller_profile=lambda: dict(id='hand1_left', generation='hand1', side='left'),
            require_identity=lambda *args: None, collections=collections, queue=queue, math=math,
            time=NS(monotonic=lambda: 1., sleep=lambda duration: None), PUB_HZ=200, CUTOFF_HZ=5.,
            SOURCE='synthetic', CURRENT_LIMIT_A=.1))
        commands, events = queue.Queue(), queue.Queue()
        commands.put(dict(name='disconnect'))
        with patch.dict(sys.modules, modules):
            worker('', commands, events, prepared_hand=hand)
        event = events.get_nowait()
        self.assertEqual(event['type'], 'closed')
        self.assertEqual(calls, [], 'Read-only close unexpectedly issued a disable')
        self.journal.observe('hand', 'X', event['hardware'], self.epoch())
        self.assertTrue(self.journal.snapshot(), 'Read-only Hand 1 close cleared a prior unknown')

    def test_snapshot_cannot_revive_glove_unknown_after_newer_device_confirmation(self):
        bridge = GloveBridge(lambda: None, self.journal)
        direct_epoch = self.epoch()
        bridge.state.update(hardware=dict(active=None, stop_confirmed=False), feedback=dict(device_id='X'))
        bridge.remember_termination()
        Controller = extracted('console_server.py', 'Controller', dict(vm_client=lambda: None,
            REPORTS=Path(self.temp.name)/'reports', copy=copy, json=json, re=re, time=time,
            threading=threading, Path=Path, socket=socket))
        c = Controller.__new__(Controller)
        c.calibration_handover = CalibrationHandover(c)
        c.lock, c.termination, c.glove = threading.RLock(), self.journal, bridge
        c.session_devices, c.session_epochs = {3: 'X'}, {3: direct_epoch}
        c.hardware_generation = c.generation = 3
        c.state = idle_state()
        c.event(dict(type='closed', hardware=dict(active=False, stop_confirmed=True)), 3)
        state = idle_state()
        state['glove'] = bridge.snapshot()
        state['unconfirmed_devices'] = self.journal.snapshot()
        self.assertEqual(unconfirmed(state), [], 'A stale dead glove revived acknowledged history')

    def test_late_old_active_cannot_revive_history_after_a_newer_stop(self):
        old, new = self.epoch(), self.epoch()
        self.journal.observe('hand', 'X', dict(active=None), old)
        self.journal.observe('hand', 'X', dict(active=False, stop_confirmed=True), new)
        self.journal.observe('hand', 'X', dict(active=True), old)
        self.assertEqual(self.journal.snapshot(), [], 'Late old state revived newer acknowledged history')

    def test_structurally_corrupt_journal_stays_explicitly_unknown_without_crashing(self):
        for saved in (dict(rows=[]),
                      dict(next_session=1, rows=[dict(source='hand', session='bad')]),
                      dict(next_session=1, rows=[dict(source='hand', session=None)])):
            with self.subTest(saved=saved):
                self.path.write_text(json.dumps(saved), encoding='utf-8')
                broken = TerminationJournal(self.path)
                self.assertTrue(broken.snapshot(), 'Unreadable stop history was silently discarded')

    def test_failed_save_must_be_retried_for_unchanged_observation(self):
        epoch = self.epoch()
        with patch.object(self.journal, 'save', side_effect=OSError('injected journal publish failure')):
            with self.assertRaises(OSError):
                self.journal.observe('hand', 'X', dict(active=None), epoch)
        self.journal.observe('hand', 'X', dict(active=None), epoch)
        self.assertTrue(self.path.exists(), 'An unchanged later observation never retried persistence')
        self.assertTrue(TerminationJournal(self.path).snapshot())

    def test_controller_old_close_cannot_replace_newer_hardware_state(self):
        Controller = extracted('console_server.py', 'Controller', dict(
            vm_client=lambda: None, REPORTS=Path(self.temp.name)/'reports', copy=copy, json=json,
            re=re, time=time, threading=threading, Path=Path, socket=socket))
        c = Controller.__new__(Controller)
        c.calibration_handover = CalibrationHandover(c)
        old, new = self.epoch(), self.epoch()
        c.lock = threading.RLock()
        c.termination = self.journal
        c.glove = GloveBridge(lambda: None, self.journal)
        c.session_devices = {1: 'X', 3: 'X'}
        c.session_epochs = {1: old, 3: new}
        c.hardware_generation = 3
        c.generation = 4
        c.state = idle_state()
        c.state['hardware'] = dict(active=None, stop_confirmed=False)
        c.state['motion_enabled'] = None
        self.journal.observe('hand', 'X', c.state['hardware'], new)
        c.event(dict(type='closed', hardware=dict(active=False, stop_confirmed=True)), 1)
        self.assertIsNone(c.state['hardware']['active'], 'Old close overwrote newer hardware generation')
        self.assertTrue(self.journal.snapshot())

    def recovery_controller(self, current_serial):
        bridge = GloveBridge(lambda: None, self.journal)
        bridge.state.update(hardware=dict(active=None, stop_confirmed=False), feedback=dict(device_id='X'))
        bridge.remember_termination()
        direct_epoch = self.epoch()
        Controller = extracted('console_server.py', 'Controller', dict(vm_client=lambda: None,
            REPORTS=Path(self.temp.name)/'reports', copy=copy, json=json, re=re, time=time,
            threading=threading, Path=Path, socket=socket))
        c = Controller.__new__(Controller)
        c.calibration_handover = CalibrationHandover(c)
        c.lock, c.termination, c.glove = threading.RLock(), self.journal, bridge
        c.session_devices, c.session_epochs = {3: current_serial}, {3: direct_epoch}
        c.hardware_generation = c.generation = 3
        c.state = idle_state()
        c.state.update(connection='connected', device_id=current_serial,
                       hardware=dict(active=False, stop_confirmed=False))
        c.doctor = NS(snapshot=lambda: {})
        commands = []
        c.player = NS(command=lambda command: commands.append(command['name']))
        def snapshot():
            with c.lock:
                state = copy.deepcopy(c.state)
                state.update(glove=bridge.snapshot(), unconfirmed_devices=self.journal.snapshot())
                return state
        c.snapshot = snapshot
        def action(command):
            commands.append(command['name'])
            if command['name'] == 'hardware_stop':
                c.event(dict(type='closed', hardware=dict(active=False, stop_confirmed=True)), 3)
            elif command['name'] == 'disconnect':
                c.state['connection'] = 'disconnected'
        c.action = action
        return c, commands

    def test_close_recovery_stops_matching_read_only_serial_before_clearing_old_glove(self):
        controller, commands = self.recovery_controller('X')
        self.assertTrue(unconfirmed(controller.snapshot()))
        self.assertTrue(close_sessions(controller, sleep=lambda duration: None)['ok'])
        self.assertEqual(commands, ['hardware_stop', 'demo_stop', 'disconnect'])
        self.assertEqual(self.journal.snapshot(), [])
        self.assertIs(controller.glove.snapshot()['hardware']['stop_confirmed'], True)

    def test_close_recovery_does_not_stop_unrelated_read_only_serial(self):
        controller, commands = self.recovery_controller('Y')
        self.assertFalse(close_sessions(controller, sleep=lambda duration: None)['ok'])
        self.assertEqual(commands, [])
        self.assertTrue(self.journal.snapshot())


class ShutdownCrosscheck(unittest.TestCase):
    def test_closing_guard_rejects_early_return_start_paths(self):
        Controller = extracted('console_server.py', 'Controller', dict(vm_client=lambda: None,
            REPORTS=QA/'unused-reports', copy=copy, json=json, re=re, time=time,
            threading=threading, Path=Path, socket=socket, HARDWARE_COMMANDS={'hardware_start'},
            validate_command=lambda command: None))
        starts = []
        def started(kind):
            return lambda *args, **kwargs: starts.append(kind) or dict(started=True)
        modules = {
            'studio_component': NS(enforce_session_owner=lambda command: None),
            'managed_runtime': NS(status=lambda: dict(busy=False), start_install=started('runtime'), select=started('runtime_select')),
        }
        for name in ('program_start', 'group_prepare', 'runtime_install', 'runtime_select', 'hardware_start', 'connect', 'calibration_start'):
            with self.subTest(command=name), patch.dict(sys.modules, modules):
                c = Controller.__new__(Controller)
                c.calibration_handover = CalibrationHandover(c)
                c.lock, c.desktop_closing, c.state = threading.RLock(), True, idle_state()
                c.group = NS(active=False, internal=False, handle=started('group'))
                c.program = NS(snapshot=lambda: dict(active=False), start=started('program'))
                c.calibration = NS(run=dict(running=False), start=started('calibration'))
                c.glove = NS(busy=False)
                c.doctor = NS(snapshot=lambda: dict(running=False))
                c.parameters = NS(sync_status=dict(busy=False))
                c.official_tools = NS(snapshot=lambda: dict(check=dict(busy=False, status='ready', compatible=True)))
                with self.assertRaises(ValueError):
                    c.action(dict(name=name, side='left', serial='SYNTHETIC', replace=False))
        self.assertEqual(starts, [], 'A closing session allowed a queued start')

    def test_workspace_close_allows_hand_ack_reader_to_acquire_controller_lock(self):
        Controller = extracted('console_server.py', 'Controller', dict(vm_client=lambda: None,
            REPORTS=QA/'unused-reports', copy=copy, json=json, re=re, time=time,
            threading=threading, Path=Path, socket=socket, HARDWARE_COMMANDS={'hardware_stop'},
            validate_command=lambda command: None))
        c = Controller.__new__(Controller)
        c.calibration_handover = CalibrationHandover(c)
        c.lock, c.generation = threading.RLock(), 1
        c.session_devices, c.session_epochs = {}, {}
        c.hardware_generation = 1
        c.state = idle_state()
        c.state.update(connection='connected', hardware=dict(active=True, stop_confirmed=False))
        c.glove = NS(busy=False, lease=None)
        c.doctor = NS(snapshot=lambda: dict(running=False))
        c.program = NS(snapshot=lambda: dict(active=False), stop=lambda: None)
        c.group = NS(active=False, internal=False)
        c.calibration = NS(run=dict(running=False), cancel=lambda: None)
        c.player = NS(command=lambda command: None)
        c.termination = NS(observe=lambda *args: None)
        c.client, c.hardware_lease = None, None
        c.log = lambda message: None
        threads = []
        def snapshot():
            with c.lock:return copy.deepcopy(c.state)
        c.snapshot = snapshot
        def write(line):
            if json.loads(line)['name'] != 'hardware_stop':return
            def ack():
                with c.lock:
                    c.state['hardware'] = dict(active=False, stop_confirmed=True)
            thread = threading.Thread(target=ack, daemon=True)
            threads.append(thread)
            thread.start()
        c.stdin = NS(write=write, flush=lambda: None)
        modules = {'studio_component': NS(enforce_session_owner=lambda command: None)}
        bounded_close = lambda controller: close_sessions(controller, timeout=.15, sleep=lambda duration: time.sleep(.005))
        with patch.dict(sys.modules, modules), patch('desktop_lifecycle.close_sessions', side_effect=bounded_close):
            result = c.action(dict(name='session_close'))
        for thread in threads:
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.assertTrue(result['ok'], 'session_close waited for an acknowledgement while holding its reader lock')

    def test_unknown_generated_during_glove_shutdown_prevents_exit(self):
        state = idle_state()
        state['glove'] = dict(busy=True, hardware=dict(active=False, stop_confirmed=True), feedback=dict(device_id='X'))
        commands = []
        controller = NS(doctor=NS(snapshot=lambda: {}), glove=NS(busy=True),
                        snapshot=lambda: copy.deepcopy(state), player=NS(command=lambda c: commands.append(c)))
        def action(c):
            commands.append(c)
            if c['name'] == 'glove_disconnect':
                state['glove'].update(busy=False, hardware=dict(active=None, stop_confirmed=False))
                controller.glove.busy = False
                state['unconfirmed_devices'] = [dict(source='glove', device_id='X')]
        controller.action = action
        result = close_sessions(controller, sleep=lambda duration: None)
        self.assertFalse(result['ok'], 'Exit used the pre-cleanup snapshot despite a final unknown state')
        self.assertNotIn('disconnect', [c['name'] for c in commands])


def glove_worker_fixture(disconnect_error=False, solver_close_error=False, wrong_side=False, subscribe_error=False):
    calls = []
    leases = []
    manager = NS(active='A', connected=False)
    users = [dict(user_id='A'), dict(user_id='B')]
    manager.current_user = lambda: dict(user_id=manager.active)
    manager.list_users = lambda: users
    manager.scan = lambda: []
    def switch(user):
        calls.append(dict(call='switch', user=user, connected=manager.connected,
                          profile_held=any(not l.selection and not l.closed for l in leases),
                          selection_held=any(l.selection and not l.closed for l in leases)))
        manager.active = user
    manager.switch_user = switch
    class Lease:
        def __init__(self, exclusive=False, selection=False):
            self.selection, self.closed = selection, False
            leases.append(self)
        def close(self):
            self.closed = True
            calls.append(dict(call='lease_close', selection=self.selection))
        def __enter__(self):return self
        def __exit__(self, *args):self.close()
    def disconnect():
        calls.append(dict(call='glove_disconnect'))
        if disconnect_error:raise OSError('injected glove disconnect error')
        manager.connected = False
    def connect(*args):
        manager.connected = True
        def subscribe():
            if subscribe_error:raise OSError('injected subscribe failure')
            return NS(close=lambda: None)
        return NS(disconnect=disconnect, hand_side=lambda: NS(get=lambda: 'left' if wrong_side else 'right'),
                  hand_skeleton=lambda: NS(subscribe=subscribe))
    def close_solver():
        calls.append(dict(call='solver_close'))
        if solver_close_error:raise OSError('injected solver cleanup error')
    def solver(*args, **kwargs):
        calls.append(dict(call='solver', user=manager.active))
        return NS(close=close_solver)
    class Source:
        def __init__(self, *args):pass
        def invalidate(self, message):pass
        def snapshot(self, now):return dict(fresh=False)
    class Mapping:
        def __init__(self, settings):self.settings = settings
    def module(**attrs):
        m = ModuleType('synthetic_only')
        m.__dict__.update(attrs)
        return m
    modules = {
        'numpy': module(),
        'wuji_sdk': module(SdkManager=NS(instance=lambda: manager), RetargetSession=NS(for_hand=solver),
                           HandModel=NS(WujiHand2='hand2', WujiHand='hand1'), Handedness=NS(Right='right', Left='left')),
        'device_profiles': module(controller_profile=lambda: dict(id='hand2_right', generation='hand2', side='right')),
        'retarget_settings': module(OutputMapping=Mapping, defaults=lambda: {}),
        'device_discovery': module(candidates=lambda items: []),
        'glove_discovery': module(scan_gloves=lambda *args, **kwargs: [dict(serial='SYNTHETIC')], connect_glove=connect),
        'zenoh_route': module(managed_wsl=lambda: False),
        'sdk_session': module(ProfileLease=Lease),
        'device_ownership': module(DeviceOwnership=lambda *args: NS(close=lambda: None)),
        'official_policy': module(settings=lambda: {}),
    }
    worker = extracted('glove_agent.py', 'worker_glove', dict(
        copy=copy, queue=queue, sys=NS(platform='linux'), threading=threading,
        time=NS(monotonic=lambda: 1., time=lambda: 1000., sleep=lambda duration: None),
        GloveSource=Source, read_latest=lambda sub: None,prepare_permission=prepare_permission))
    commands, events = queue.Queue(), queue.Queue()
    commands.put(dict(name='glove_open', serial='SYNTHETIC', user_id='B', timeout_ms=250, solver=dict(engine='sdk')))
    commands.put(dict(name='glove_solver', solver=dict(engine='sdk')))
    commands.put(dict(name='glove_disconnect'))
    raised = None
    with patch.dict(sys.modules, modules):
        try:worker(commands, events)
        except Exception as error:raised = repr(error)
    recorded = []
    while not events.empty():recorded.append(events.get_nowait())
    return NS(calls=calls, leases=leases, manager=manager, events=recorded, raised=raised)


class GloveProfileCrosscheck(unittest.TestCase):
    def test_normal_session_keeps_selected_user_until_disconnected_then_restores_under_leases(self):
        result = glove_worker_fixture()
        self.assertIsNone(result.raised)
        self.assertEqual([c['user'] for c in result.calls if c['call'] == 'solver'], ['B', 'B'])
        restore = [c for c in result.calls if c['call'] == 'switch' and c['user'] == 'A']
        self.assertEqual(len(restore), 1)
        self.assertFalse(restore[0]['connected'])
        self.assertTrue(restore[0]['profile_held'])
        self.assertTrue(restore[0]['selection_held'])
        self.assertTrue(all(l.closed for l in result.leases))
        self.assertTrue(any(e['type'] == 'glove_closed' for e in result.events))

    def test_failed_glove_disconnect_never_restores_old_user_while_still_connected(self):
        result = glove_worker_fixture(disconnect_error=True)
        unsafe = [c for c in result.calls if c['call'] == 'switch' and c['user'] == 'A' and c['connected']]
        self.assertEqual(unsafe, [], 'Restored old parameters after a failed disconnect')

    def test_solver_cleanup_error_cannot_skip_profile_release_and_terminal_status(self):
        result = glove_worker_fixture(solver_close_error=True)
        self.assertTrue(all(l.closed for l in result.leases), result.raised)
        self.assertTrue(any(e['type'] == 'glove_closed' for e in result.events), result.raised)

    def test_failed_subscription_and_solver_cleanup_do_not_lose_live_candidate(self):
        result = glove_worker_fixture(subscribe_error=True, solver_close_error=True)
        unsafe = [c for c in result.calls if c['call'] == 'switch' and c['user'] == 'A' and c['connected']]
        self.assertEqual(unsafe, [], 'Open failure lost the connected candidate before user restoration')
        self.assertFalse(result.manager.connected, 'Open failure did not disconnect its owned candidate')
        self.assertTrue(all(l.closed for l in result.leases), result.raised)

    def test_failed_open_restores_only_after_candidate_disconnect(self):
        result = glove_worker_fixture(wrong_side=True)
        restore = [c for c in result.calls if c['call'] == 'switch' and c['user'] == 'A']
        self.assertEqual(len(restore), 1)
        self.assertFalse(restore[0]['connected'])
        self.assertTrue(restore[0]['profile_held'])
        self.assertTrue(restore[0]['selection_held'])
        self.assertTrue(all(l.closed for l in result.leases))


if __name__ == '__main__':
    QA.mkdir(parents=True, exist_ok=True)
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    with (QA/'independent-tests.log').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    files = ['device_termination.py', 'console_server.py', 'glove_bridge.py', 'desktop_lifecycle.py', 'desktop_tools.py',
             'glove_agent.py', 'hardware_showcase.py', 'first_generation.py', 'console_agent.py', 'test_control_recovery.py']
    report = dict(timestamp=datetime.now(timezone.utc).isoformat(), python=sys.version,
                  tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                  failed_tests=[str(t) for t, trace in result.failures],
                  error_tests=[str(t) for t, trace in result.errors],
                  source_sha256={p: hashlib.sha256((ROOT/'src'/p).read_bytes()).hexdigest() for p in files},
                  real_sdk_calls=0, wsl_calls=0, real_app_starts=0, network_calls=0)
    (QA/'independent-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0 if result.wasSuccessful() else 1)
