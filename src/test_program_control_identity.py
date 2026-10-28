"""Playlist controls bind to the visible run under the controller owner lock."""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from console_server import Controller
from demo_player import DemoPlayer
from program_runner import ProgramRunner


class PlaylistControlIdentityTests(unittest.TestCase):
    def owner(self):
        c=SimpleNamespace(lock=threading.RLock(),desktop_closing=False,
            group=SimpleNamespace(active=False,internal=False,stop=lambda *_:self.fail('Unexpected group stop')),
            calibration=SimpleNamespace(run={'running':False}),hardware_lease=None,
            state={'hardware':{'active':False}},player=DemoPlayer())
        c._action=lambda command:Controller._action(c,command)
        c.action=lambda command:Controller._coordinated_action(c,command)
        c.program=ProgramRunner(c)
        c.player.command(dict(name='demo_start',action='fist',speed=1,cycles=0))
        c.program.update(active=True,mode='preview',run_id='a'*32)
        return c

    def setUp(self):
        p=patch('studio_component.enforce_session_owner');p.start();self.addCleanup(p.stop)

    def test_same_run_controls_pause_resume_stop_and_keep_clock(self):
        c=self.owner()
        c.action(dict(name='program_pause',expected_run='a'*32));self.assertTrue(c.program.snapshot()['paused'])
        self.assertFalse(c.player.running)
        elapsed=c.player.elapsed
        with patch('demo_player.time.monotonic',return_value=c.player.tick+3):self.assertEqual(c.player.snapshot()['elapsed_s'],elapsed)
        c.action(dict(name='program_resume',expected_run='a'*32));self.assertTrue(c.player.running)
        c.action(dict(name='program_stop',expected_run='a'*32));self.assertTrue(c.program.cancel.is_set())

    def test_late_controls_do_not_touch_replacement_or_new_group(self):
        for name in ('program_pause','program_resume','program_stop'):
            with self.subTest(name=name):
                c=self.owner();c.program.update(run_id='b'*32);c.group.active=True
                with self.assertRaisesRegex(ValueError,'Playlist changed'):c.action(dict(name=name,expected_run='a'*32))
                self.assertFalse(c.program.snapshot()['paused']);self.assertFalse(c.program.cancel.is_set());self.assertTrue(c.player.running)

    def test_invalid_expected_run_cannot_touch_active_playlist(self):
        for value in (None,'',False,0,[],{},'another run','A'*32):
            with self.subTest(value=value):
                c=self.owner()
                with self.assertRaisesRegex(ValueError,'Playlist changed'):c.action(dict(name='program_stop',expected_run=value))
                self.assertFalse(c.program.cancel.is_set())

    def test_unscoped_internal_safe_stop_remains_compatible(self):
        c=self.owner();c.action(dict(name='program_pause'));c.action(dict(name='program_resume'))
        c.action(dict(name='program_stop'));self.assertTrue(c.program.cancel.is_set())

    def test_owner_lock_prevents_replacement_between_identity_check_and_dispatch(self):
        c=self.owner();entered=threading.Event();release=threading.Event();replaced=threading.Event();errors=[]
        snapshot=c.program.snapshot
        def held_snapshot():
            if threading.current_thread().name=='old-playlist-control' and not entered.is_set():
                entered.set();release.wait(2)
            return snapshot()
        c.program.snapshot=held_snapshot
        def control():
            try:c.action(dict(name='program_pause',expected_run='a'*32))
            except Exception as error:errors.append(error)
        def replacement():
            with c.lock:c.program.update(run_id='b'*32,paused=False);replaced.set()
        a=threading.Thread(target=control,name='old-playlist-control');b=threading.Thread(target=replacement)
        a.start();self.assertTrue(entered.wait(1));b.start()
        try:self.assertFalse(replaced.wait(.05))
        finally:release.set();a.join(2);b.join(2)
        self.assertFalse(a.is_alive());self.assertFalse(b.is_alive());self.assertEqual(errors,[])
        self.assertEqual(snapshot()['run_id'],'b'*32);self.assertFalse(snapshot()['paused'])
        with self.assertRaisesRegex(ValueError,'Playlist changed'):c.action(dict(name='program_stop',expected_run='a'*32))
        self.assertFalse(c.program.cancel.is_set())

    def test_stop_during_pause_prevents_late_resume(self):
        c=self.owner();c.action(dict(name='program_pause',expected_run='a'*32));c.action(dict(name='program_stop',expected_run='a'*32))
        with self.assertRaisesRegex(ValueError,'No active playlist'):c.action(dict(name='program_resume',expected_run='a'*32))
        self.assertTrue(c.program.cancel.is_set());self.assertFalse(c.player.running)


if __name__=='__main__':unittest.main()
