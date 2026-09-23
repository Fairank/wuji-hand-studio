"""The playlist boundary must reject malformed motion requests before execution."""
import itertools
import unittest

from playlist_plan import iter_plan, validate_plan


class PlaylistPlanTests(unittest.TestCase):
    def test_rejects_unavailable_speed_and_action(self):
        for action, speed in [('dance', 1.1), ('unknown', 1)]:
            with self.subTest(action=action, speed=speed), self.assertRaises(ValueError):
                validate_plan(dict(entries=[dict(action=action, speed=speed)]), {'dance'})

    def test_shuffle_contains_each_entry_once_per_round(self):
        plan=validate_plan(dict(entries=[dict(action=x) for x in ('a','b','c')],
                                order='shuffle', repeats=10, seed=42), {'a','b','c'})
        steps=list(iter_plan(plan))
        self.assertEqual(len(steps),30)
        for i in range(10):
            self.assertEqual({x['action'] for x in steps[i*3:(i+1)*3]}, {'a','b','c'})
        self.assertEqual(steps,list(iter_plan(plan)))

    def test_endless_plan_is_lazy_and_snapshotted(self):
        plan=validate_plan(dict(entries=[dict(action='a')],repeats=0), {'a'})
        stream=iter_plan(plan)
        plan['entries'][0]['action']='changed'
        self.assertEqual([row['action'] for row in itertools.islice(stream,50)],['a']*50)


if __name__=='__main__':unittest.main()
