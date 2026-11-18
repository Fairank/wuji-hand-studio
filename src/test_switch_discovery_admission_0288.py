"""An explicit fresh target yields only ordinary discovery, without motor actions."""
from contextlib import contextmanager
import copy
import unittest
from unittest.mock import Mock

import test_connection_switch as fixture
from task_sessions import context_digest


class DiscoverySwitchTests(unittest.TestCase):
    environment=fixture.SwitchTests.environment
    command=fixture.SwitchTests.command

    def scanning(self,e):
        e.vs.update(active=False,busy=True,scanning=True,reserved=False,session_id=None,phase='idle',
            scan_cleanup_required=False,error=None)

    def test_ordinary_readonly_discovery_is_not_an_unconfirmed_device_owner(self):
        with self.environment(mode='none') as e:
            self.scanning(e)
            self.assertTrue(e.c.snapshot()['connection_switch']['can_switch'])
            self.assertIsNone(e.c.snapshot()['connection_switch']['current_owner'])

    def test_explicit_target_yields_scan_after_publishing_cancellable_attempt(self):
        with self.environment(mode='none') as e:
            self.scanning(e);calls=[]
            @contextmanager
            def priority(**kwargs):
                self.assertTrue(e.service.state['busy'])
                self.assertEqual(e.service.state['phase'],'preflight')
                self.assertTrue(e.service.state['attempt_id'])
                calls.append('yield')
                e.vs.update(busy=False,scanning=False)
                try:yield
                finally:calls.append('release')
            e.c.official_viz.discovery_priority=Mock(side_effect=priority)
            result=e.service.start(self.command(e,'hand',fixture.HAND))
            self.assertEqual(result['connection_switch']['phase'],'connected')
            self.assertEqual(calls,['yield','release'])
            self.assertEqual([c['name'] for c in e.calls],['connect'])
            self.assertFalse(e.state['hardware']['active'])

    def test_unknown_cleanup_or_viewer_start_is_not_exempt(self):
        for changed in ({'scan_cleanup_required':True},{'error':'unconfirmed'},
                        {'reserved':True},{'session_id':'a'*32},{'active':True},
                        {'busy':False},{'scanning':False},{'phase':'starting'},
                        {'phase':'running'},{'phase':'stopping'},{'phase':'error'}):
            with self.subTest(changed=changed),self.environment(mode='none') as e:
                self.scanning(e);e.vs.update(changed)
                e.c.official_viz.discovery_priority=Mock()
                self.assertFalse(e.c.snapshot()['connection_switch']['can_switch'])
                with self.assertRaises(ValueError):e.service.start(self.command(e,'hand',fixture.HAND))
                e.c.official_viz.discovery_priority.assert_not_called()
                self.assertEqual(e.calls,[])

    def test_target_expiring_while_scan_yields_is_rejected(self):
        with self.environment(mode='none') as e:
            self.scanning(e)
            @contextmanager
            def priority(**kwargs):
                e.vs.update(busy=False,scanning=False)
                for row in e.rows:row['stale']=True
                yield
            e.c.official_viz.discovery_priority=priority
            with self.assertRaisesRegex(ValueError,'Find and select'):
                e.service.start(self.command(e,'hand',fixture.HAND))
            self.assertEqual(e.calls,[])

    def test_cancellation_while_scan_yields_never_reads_or_opens_target(self):
        with self.environment(mode='none') as e:
            self.scanning(e)
            @contextmanager
            def priority(**kwargs):
                e.service.cancel(dict(name='connection_switch_cancel',request_id=e.service.state['request_id'],
                    attempt_id=e.service.state['attempt_id']))
                e.vs.update(busy=False,scanning=False)
                yield
            e.c.official_viz.discovery_priority=priority
            with self.assertRaisesRegex(ValueError,'cancellation|Cancellation'):
                e.service.start(self.command(e,'hand',fixture.HAND))
            e.c.device_inventory.assert_not_called();self.assertEqual(e.calls,[])

    def test_owner_change_during_discovery_yield_is_rejected_before_open(self):
        with self.environment(mode='none') as e:
            self.scanning(e)
            @contextmanager
            def priority(**kwargs):
                e.vs.update(busy=False,scanning=False);e.state['session_generation']+=1
                yield
            e.c.official_viz.discovery_priority=priority
            with self.assertRaisesRegex(ValueError,'owner changed|session changed'):
                e.service.start(self.command(e,'hand',fixture.HAND))
            self.assertEqual(e.calls,[])

    def test_unconfirmed_scan_exit_does_not_open_or_claim_success(self):
        with self.environment(mode='none') as e:
            self.scanning(e)
            @contextmanager
            def priority(**kwargs):
                raise ValueError('Synthetic scan exit is unconfirmed')
                yield
            e.c.official_viz.discovery_priority=priority
            with self.assertRaisesRegex(ValueError,'unconfirmed'):
                e.service.start(self.command(e,'hand',fixture.HAND))
            self.assertEqual(e.calls,[]);self.assertTrue(e.vs['scanning'])
            self.assertFalse(e.service.state['busy'])


if __name__=='__main__':unittest.main()
