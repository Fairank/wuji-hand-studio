"""Late UI controls cannot pause, resume or stop a replacement preview run."""
import unittest
from unittest.mock import patch
from demo_player import DemoPlayer


class PreviewIdentityTests(unittest.TestCase):
    def player(self):
        player=DemoPlayer()
        player.command(dict(name='demo_start',action='fist',speed=.5,cycles=0))
        return player

    def test_same_run_pause_resume_and_stop_use_actual_state(self):
        player=self.player();run=player.run_id
        player.command(dict(name='demo_pause',expected_run=run))
        self.assertTrue(player.active);self.assertFalse(player.running)
        elapsed=player.elapsed
        with patch('demo_player.time.monotonic',return_value=player.tick+2):
            self.assertEqual(player.snapshot()['elapsed_s'],elapsed)
        player.command(dict(name='demo_resume',expected_run=run))
        self.assertTrue(player.running);self.assertEqual(player.run_id,run)
        player.command(dict(name='demo_stop',expected_run=run))
        self.assertFalse(player.active);self.assertFalse(player.running)

    def test_late_controls_for_old_run_never_modify_replacement(self):
        for name in ('demo_pause','demo_resume','demo_stop'):
            with self.subTest(name=name):
                player=self.player();old=player.run_id
                player.command(dict(name='demo_start',action='open',speed=1,cycles=3))
                self.assertNotEqual(player.run_id,old)
                before=(player.action,player.clock_at,player.active,player.running,player.elapsed,player.tick)
                with self.assertRaisesRegex(ValueError,'Preview changed'):
                    player.command(dict(name=name,expected_run=old))
                self.assertEqual(before,(player.action,player.clock_at,player.active,player.running,player.elapsed,player.tick))

    def test_expected_run_wrong_types_and_empty_values_fail_before_advance(self):
        for value in (None,'',False,0,[],{},'another run'):
            with self.subTest(value=value):
                player=self.player()
                with patch.object(player,'advance',side_effect=AssertionError('Must reject before touching the run')):
                    with self.assertRaisesRegex(ValueError,'Preview changed'):
                        player.command(dict(name='demo_pause',expected_run=value))

    def test_existing_callers_without_expected_run_remain_compatible(self):
        player=self.player()
        player.command(dict(name='demo_pause'));self.assertFalse(player.running)
        player.command(dict(name='demo_resume'));self.assertTrue(player.running)
        player.command(dict(name='demo_stop'));self.assertFalse(player.active)

    def test_equal_wall_clock_does_not_reuse_the_preview_identity(self):
        with patch('demo_player.datetime') as clock:
            clock.now.return_value.astimezone.return_value.isoformat.return_value='2026-10-28T14:00:00+08:00'
            player=self.player();old=player.run_id;stamp=player.clock_at
            player.command(dict(name='demo_start',action='open',speed=1,cycles=0))
            self.assertEqual(player.clock_at,stamp);self.assertNotEqual(player.run_id,old)
            self.assertEqual(player.snapshot()['run_id'],player.run_id)
            with self.assertRaisesRegex(ValueError,'Preview changed'):
                player.command(dict(name='demo_stop',expected_run=old))
            self.assertTrue(player.active)


if __name__=='__main__':unittest.main()
