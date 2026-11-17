"""Preparation and finite SDK read have independent clocks; no vendor I/O."""
import io
import json
import threading
import unittest
from unittest.mock import Mock,patch

import official_device_discovery as discovery


class Clock:
    def __init__(self):
        self.value=0.;self.lock=threading.Lock();self.checked=threading.Event()
    def set(self,value):
        with self.lock:self.value=value
    def __call__(self):
        with self.lock:value=self.value
        if value and threading.current_thread().name=='discovery-case':self.checked.set()
        return value


class Client:
    agent_command='SYNTHETIC-DISCOVERY'
    def __init__(self,*,blocked=False):
        self.input=io.StringIO();self.release=threading.Event();self.exec_entered=threading.Event()
        if not blocked:self.release.set()
        self.exec_calls=0;self.close_calls=0;self.close_ok=True;self.exec_error=None
    def exec_command(self,command,timeout):
        self.exec_calls+=1;self.exec_entered.set()
        if self.exec_error:raise self.exec_error
        def lines():
            if not self.release.wait(3):raise RuntimeError('Fixture read was not released')
            yield 'WUJI_JSON:'+json.dumps({'type':'discovery','devices':[]})+'\n'
        return self.input,lines(),io.StringIO()
    def close(self):
        self.close_calls+=1;self.release.set();return self.close_ok


class DiscoveryPreparationTests(unittest.TestCase):
    def run_call(self,factory,**kwargs):
        result={};waiting=threading.Event();made=[];original=discovery._SDKDiscoveryRead
        def make(*args):
            owned=original(*args);made.append(owned);wait=owned.finished.wait
            def observed(timeout=None):
                if timeout==.05:waiting.set()
                return wait(timeout)
            owned.finished.wait=observed
            return owned
        def call():
            try:result['value']=discovery.sdk_devices(factory,**kwargs)
            except BaseException as error:result['error']=error
        with patch.object(discovery,'_SDKDiscoveryRead',side_effect=make):
            thread=threading.Thread(target=call,name='discovery-case');thread.start()
            self.assertTrue(waiting.wait(2),'Caller did not enter the finite wait')
        self.addCleanup(lambda:thread.join(3))
        return thread,result,made[0]

    def test_preparation_longer_than_read_budget_still_discovers_once_ready(self):
        clock=Clock();factory_entered=threading.Event();factory_release=threading.Event();client=Client(blocked=True)
        def factory():
            factory_entered.set()
            if not factory_release.wait(3):raise RuntimeError('Fixture factory was not released')
            return client
        with patch('official_device_discovery.time.monotonic',clock):
            thread,result,owned=self.run_call(factory,timeout=1)
            self.assertTrue(factory_entered.is_set());clock.set(5.)
            self.assertTrue(clock.checked.wait(2));factory_release.set()
            self.assertTrue(client.exec_entered.wait(2),'Preparation incorrectly consumed the SDK read budget')
            client.release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertNotIn('error',result)
        self.assertEqual(result['value']['devices'],[]);self.assertEqual(client.exec_calls,1)
        self.assertEqual(json.loads(client.input.getvalue()),{'name':'discover','include_gloves':True})
        self.assertTrue(owned.finished.is_set());self.assertTrue(owned.transport_closed)

    def test_late_factory_cannot_launch_after_preparation_deadline_even_before_parent_poll(self):
        clock=Clock();client=Client();factory_release=threading.Event()
        def factory():
            factory_release.wait(3);clock.set(61.);return client
        with patch('official_device_discovery.time.monotonic',clock):
            thread,result,owned=self.run_call(factory)
            factory_release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertIn('preparation',str(result.get('error')).lower())
        self.assertEqual(client.exec_calls,0);self.assertTrue(owned.transport_closed)

    def test_preparation_expiry_retains_exact_owner_until_late_factory_closes(self):
        clock=Clock();client=Client();release=threading.Event()
        def factory():release.wait(5);return client
        with patch('official_device_discovery.time.monotonic',clock):
            thread,result,owned=self.run_call(factory,preparation_timeout=1)
            clock.set(2.);thread.join(3)
            self.assertFalse(thread.is_alive());self.assertIsInstance(result.get('error'),discovery.DiscoveryCleanupUnconfirmed)
            self.assertIs(result['error'].owned,owned);self.assertFalse(owned.finished.is_set())
            release.set();self.assertTrue(owned.close(timeout=1))
        self.assertEqual(client.exec_calls,0);self.assertTrue(owned.finished.is_set());self.assertTrue(owned.transport_closed)

    def test_cancellation_during_preparation_prevents_late_exec(self):
        client=Client();release=threading.Event();cancel=threading.Event()
        def factory():release.wait(3);return client
        thread,result,owned=self.run_call(factory,cancelled=cancel.is_set)
        cancel.set();release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertIsInstance(result.get('error'),discovery.DiscoveryDeferred)
        self.assertEqual(client.exec_calls,0);self.assertTrue(owned.transport_closed)

    def test_sdk_read_budget_remains_bounded_after_preparation(self):
        clock=Clock();client=Client(blocked=True);release=threading.Event()
        def factory():release.wait(3);return client
        with patch('official_device_discovery.time.monotonic',clock):
            thread,result,owned=self.run_call(factory,timeout=1)
            clock.set(.5);release.set();self.assertTrue(client.exec_entered.wait(2))
            clock.set(2.);thread.join(2)
        self.assertFalse(thread.is_alive());self.assertIn('timed out',str(result.get('error')).lower())
        self.assertEqual(client.exec_calls,1);self.assertTrue(owned.finished.is_set());self.assertTrue(owned.transport_closed)

    def test_factory_error_propagates_without_launch(self):
        with self.assertRaisesRegex(RuntimeError,'synthetic preparation failure'):
            discovery.sdk_devices(lambda:(_ for _ in ()).throw(RuntimeError('synthetic preparation failure')))

    def test_cancellation_during_admission_prevents_exec(self):
        client=Client();cancel=threading.Event()
        def clock():
            if threading.current_thread().name=='SDK read-only discovery':cancel.set()
            return 0.
        with patch('official_device_discovery.time.monotonic',clock):
            with self.assertRaises(discovery.DiscoveryDeferred):discovery.sdk_devices(lambda:client,cancelled=cancel.is_set)
        self.assertEqual(client.exec_calls,0);self.assertGreaterEqual(client.close_calls,1)

    def test_cancellation_during_exec_prevents_discover_write(self):
        client=Client();cancel=threading.Event();original=client.exec_command
        def execute(*args,**kwargs):
            channels=original(*args,**kwargs);cancel.set();return channels
        with patch.object(client,'exec_command',side_effect=execute):
            with self.assertRaises(discovery.DiscoveryDeferred):discovery.sdk_devices(lambda:client,cancelled=cancel.is_set)
        self.assertEqual(client.exec_calls,1);self.assertEqual(client.input.getvalue(),'')

    def test_thread_start_failure_closes_unstarted_owner_without_factory(self):
        factory=Mock(side_effect=AssertionError('No factory after start failure'))
        with patch('official_device_discovery.threading.Thread.start',side_effect=RuntimeError('synthetic start failure')):
            with self.assertRaisesRegex(RuntimeError,'synthetic start failure'):discovery.sdk_devices(factory)
        factory.assert_not_called()

    def test_exec_failure_retains_failed_close_for_explicit_retry(self):
        client=Client();client.exec_error=RuntimeError('synthetic exec failure');client.close_ok=False
        with self.assertRaises(discovery.DiscoveryCleanupUnconfirmed) as caught:discovery.sdk_devices(lambda:client)
        owned=caught.exception.owned;self.assertIs(owned.client,client);self.assertFalse(owned.transport_closed)
        client.close_ok=True;self.assertTrue(owned.close(timeout=1));self.assertEqual(client.exec_calls,1)


if __name__=='__main__':unittest.main()
