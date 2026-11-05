"""Actual Controller/GloveBridge lifecycles retire only a confirmed old config."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from calibration_portability import model_provenance
from console_server import Controller
from device_profiles import profile
from local_controller import LocalFiles
from test_sdk_lifecycle import release_fixture, proof_fixture


class ClosedTransport:
    agent_command = 'synthetic-sdk-agent'

    def __init__(self, folder, *, ack=True, hold=None, entered=None):
        self.agent_directory = str(folder)
        self.ack, self.hold = ack, hold
        self.entered = entered
        self.sent = io.StringIO()

    def open_sftp(self):
        return LocalFiles(self.agent_directory)

    def exec_command(self, command, timeout):
        def output():
            yield 'WUJI_JSON:' + json.dumps(dict(type='ready', teleop_version=1,
                calibration_continuation_version=1, sdk_proof=proof_fixture())) + '\n'
            request = json.loads(self.sent.getvalue().splitlines()[0])
            yield 'WUJI_JSON:' + json.dumps(dict(type='sdk_ready', sdk_proof=proof_fixture())) + '\n'
            context = request.get('continuation')
            if context:
                yield 'WUJI_JSON:' + json.dumps(dict(type='calibrated_ready',
                    receipt=model_provenance(context, context['source_user_id'], False))) + '\n'
            if self.hold is not None:
                self.entered.set()
                if not self.hold.wait(3):
                    raise AssertionError('Synthetic reader was not released')
            if self.ack:
                cleanup = dict(version=1, glove_serial=context['serial'] if context else '',
                    sdk_user_id=context['source_user_id'] if context else '', profile='hand2_right',
                    glove_was_open=bool(context), subscription_closed=True, glove_disconnected=True,
                    solver_cleanup='sdk_no_close_api' if context else 'not_open', user_restored=True,
                    ownership_released=True, profile_lease_released=True, selection_lease_released=True,
                    hand_released=True, retryable=False)
                yield 'WUJI_JSON:' + json.dumps(dict(type='glove_closed', hardware={}, device_id=None,
                    hand_cleanup_confirmed=True, session_cleanup=cleanup)) + '\n'
        return self.sent, output(), io.StringIO()

    def close(self):
        return None


class SDKConfigRetirementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.agent = self.root / 'agent'
        (self.agent / 'calibration-transfer').mkdir(parents=True)
        self.ack, self.hold = True, None
        self.entered = threading.Event()
        self.controller = Controller(factory=lambda: ClosedTransport(self.agent,
            ack=self.ack, hold=self.hold, entered=self.entered), reports=self.root / 'reports')
        self.controller.glove.release_resolver = release_fixture
        self.controller.state['device_profile'] = profile('hand2_right')
        self.controller._continuation_profile = 'hand2_right'
        native = patch('official_device_discovery.use_native', return_value=False)
        native.start(); self.addCleanup(native.stop)

    def finish(self):
        bridge = self.controller.glove
        bridge.thread.join(3)
        self.assertFalse(bridge.busy)
        return bridge.snapshot()

    def scan(self):
        self.controller.action(dict(name='glove_scan'))
        return self.finish()

    def bundle(self, saved=False):
        archive = self.root / 'synthetic.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('models/right_hand.urdf', b'synthetic-model')
        result = dict(source_run_id='a' * 32, source_workspace='main', serial='WG-SYNTHETIC',
            side='right', source_profile='Alice', source_user_id='owner-1',
            model_sha256=hashlib.sha256(b'synthetic-model').hexdigest(), model_bytes=15,
            bundle_path=str(archive), bundle_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            bundle_bytes=archive.stat().st_size)
        if saved:
            result.update(source_kind='saved_profile', source_run_id=None, transfer_id='c' * 32)
        return result

    def test_confirmed_scan_cycles_keep_current_config_until_the_next_start(self):
        for _ in range(20):
            state = self.scan()
            self.assertTrue(state['session_cleanup']['cleanup_confirmed'])
            current = self.controller.glove.session
            self.assertEqual(set(self.controller.sdk_session_configs), {current})
            self.controller.glove.snapshot()
            self.assertEqual(set(self.controller.sdk_session_configs), {current})

    def test_unknown_exit_preserves_original_config_and_blocks_replacement(self):
        self.ack = False
        state = self.scan()
        self.assertFalse(state['session_cleanup']['cleanup_confirmed'])
        before = dict(self.controller.sdk_session_configs)
        with self.assertRaises(ValueError):
            self.controller.action(dict(name='glove_scan'))
        self.assertEqual(self.controller.sdk_session_configs, before)

    def test_busy_current_session_is_not_retired_by_another_scan(self):
        self.hold = threading.Event()
        try:
            self.controller.action(dict(name='glove_scan'))
            self.assertTrue(self.entered.wait(1), 'Initial reader must reach its held SDK-ready state')
            current = self.controller.glove.session
            self.controller.action(dict(name='glove_scan'))
            self.assertEqual(set(self.controller.sdk_session_configs), {current})
        finally:
            self.hold.set(); self.finish()

    def test_only_exact_cleanup_identity_can_retire_that_one_config(self):
        state = self.scan()
        current = self.controller.glove.session
        self.controller.sdk_session_configs[current + 100] = 'older-unconfirmed-owner'
        before = dict(self.controller.sdk_session_configs)
        for wrong in (current - 1, None, True):
            with self.subTest(cleanup_session=wrong):
                invalid = dict(state, session_cleanup=dict(state['session_cleanup'], session_id=wrong))
                with patch.object(self.controller.glove, 'snapshot', return_value=invalid):
                    self.controller._retire_sdk_session_config()
                self.assertEqual(self.controller.sdk_session_configs, before)
        self.controller._retire_sdk_session_config()
        self.assertEqual(self.controller.sdk_session_configs, {current + 100: 'older-unconfirmed-owner'})

    def test_private_calibrated_start_retires_only_its_closed_predecessor(self):
        self.scan(); old = self.controller.glove.session
        continuation = self.controller.calibrated_teleoperation
        continuation.state.update(run_id='a' * 32, attempt_id='b' * 32)
        with patch.object(self.controller, '_continuation_model_ready', return_value={'timeout_ms': 250}):
            session = self.controller._start_calibrated_session(self.bundle())
            state = self.finish()
        self.assertTrue(state['session_cleanup']['cleanup_confirmed'], state)
        self.assertNotEqual(session, old)
        self.assertEqual(set(self.controller.sdk_session_configs), {session})

    def test_private_saved_start_retires_only_its_closed_predecessor(self):
        self.scan(); old = self.controller.glove.session
        switch = self.controller.connection_switch
        switch.state.update(busy=True, phase='connecting', attempt_id='d' * 32, request_id='e' * 32)
        switch._thread = threading.get_ident()
        proof = self.bundle(saved=True)
        with patch.dict('os.environ', WUJI_FLEET_ID='main'), patch('saved_calibration.verify_saved_snapshot', return_value=True):
            session = self.controller._start_saved_profile_session(proof, proof, expected_profile='hand2_right')
            state = self.finish()
        self.assertTrue(state['session_cleanup']['cleanup_confirmed'], state)
        self.assertNotEqual(session, old)
        self.assertEqual(set(self.controller.sdk_session_configs), {session})


if __name__ == '__main__':
    unittest.main()
