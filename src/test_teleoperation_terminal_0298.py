"""Verify the read-only console source, exact identity, and launch lifecycle."""
import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from console_server import Handler
from teleoperation_terminal import Terminal, sample


def glove_state():
    return dict(session_id=7, busy=True, connection='receiving', profile='hand2_right',
                glove_serial='WG-SYNTHETIC', stream=dict(
                    fresh=True, seq=15, age_ms=12.5, mapping_ms=1.25, retarget_hz=88.0,
                    solver=dict(engine='sdk', applied=True), keypoints=[None]*21, q=[None]*20),
                hardware=dict(active=False), feedback={})


class TerminalTests(TestCase):
    def test_reports_only_actual_fresh_telemetry_in_each_phase(self):
        glove = glove_state()
        preview = sample(glove, 'zh')
        self.assertIn('WG-SYNTHETIC #15', preview['line'])
        self.assertIn('21 点 → 20 关节', preview['line'])
        self.assertIn('耗时 1.25 ms', preview['line'])
        self.assertNotIn('指令发送', preview['line'])
        glove['feedback'] = dict(connection='connected', stale=False, device_id='WH-SYNTHETIC',
                                 identified_device=dict(source='sdk_identity', serial='WH-SYNTHETIC', profile='hand2_right'),
                                 latest=dict(seq=43), metrics=dict(age_ms=11.0, host_hz=200.0))
        glove['hardware'] = dict(active=True, command_timing=dict(host_publish_hz=160.0, requested_hz=500))
        following = sample(glove, 'zh')
        self.assertIn('正在跟随', following['line'])
        self.assertIn('WH-SYNTHETIC #43', following['line'])
        self.assertIn('指令发送 160.0 Hz', following['line'])
        self.assertNotIn('500', following['line'])
        glove['stream']['age_ms'] = 14
        self.assertEqual(following['key'], sample(glove, 'zh')['key'], 'One row per new sample or phase')
        glove['stream']['seq'] = 16
        self.assertNotEqual(following['key'], sample(glove, 'zh')['key'])
        glove['stream']['fresh'] = False
        glove['feedback']['stale'] = True
        stale = sample(glove, 'zh')['line']
        self.assertIn('反馈中断', stale)
        self.assertNotRegex(stale, 'WG-SYNTHETIC|WH-SYNTHETIC|耗时|指令发送')
        glove.update(connection='disconnected', busy=False, hardware=dict(active=False))
        self.assertEqual(sample(glove, 'en')['line'], 'Waiting for a teleoperation session')

    def test_invalid_identity_and_optional_older_worker_fields_are_safe(self):
        glove = glove_state()
        glove['stream'].pop('mapping_ms')
        glove['feedback'] = dict(connection='connected', stale=False, device_id='SECOND',
                                 identified_device=dict(source='sdk_identity', serial='FIRST', profile='hand2_right'),
                                 latest=dict(seq=43))
        glove['hardware'] = dict(active=True, command_timing=dict(host_publish_hz=160.0))
        line = sample(glove, 'en')['line']
        self.assertIn('Feedback interrupted', line)
        self.assertNotRegex(line, 'FIRST|SECOND|mapping time|Command rate')

    @patch.dict(os.environ, {'SystemRoot': r'C:\Windows'})
    @patch('teleoperation_terminal.subprocess.Popen')
    def test_one_window_and_revoked_key_when_closed(self, launch):
        process = Mock()
        process.poll.return_value = None
        launch.return_value = process
        terminal = Terminal()
        snapshot = Mock(return_value=glove_state())
        self.assertFalse(terminal.open('http://127.0.0.1:8781')['already_open'])
        self.assertTrue(terminal.open('http://127.0.0.1:8781')['already_open'])
        self.assertEqual(launch.call_count, 1)
        options = launch.call_args.kwargs
        key = options['env']['WUJI_TELEOP_TERMINAL_KEY']
        self.assertNotIn(key, launch.call_args.args[0])
        self.assertIsNone(terminal.read('invalid', snapshot, 'zh'))
        snapshot.assert_not_called()
        self.assertIn('WG-SYNTHETIC', terminal.read(key, snapshot, 'zh')['line'])
        terminal.close()
        process.terminate.assert_called_once_with()
        self.assertIsNone(terminal.read(key, snapshot, 'zh'))

    def test_feed_requires_owned_terminal_and_local_host(self):
        read = Mock(return_value=dict(key='frame', line='read-only'))
        server = SimpleNamespace(desktop=SimpleNamespace(teleoperation_terminal_snapshot=read))
        reply = Mock()
        request = SimpleNamespace(path='/api/teleoperation-terminal', allowed_host=lambda:False,
                                  headers={'X-Wuji-Teleoperation-Key':'key'}, server=server, reply=reply)
        Handler.do_GET(request)
        self.assertEqual(reply.call_args.args[0], 403)
        read.assert_not_called()
        request.allowed_host = lambda:True
        del server.desktop
        Handler.do_GET(request)
        self.assertEqual(reply.call_args.args[0], 403)
        server.desktop = SimpleNamespace(teleoperation_terminal_snapshot=read)
        Handler.do_GET(request)
        self.assertEqual(reply.call_args.args, (200, {'key':'frame', 'line':'read-only'}))
        read.assert_called_once_with('key')
