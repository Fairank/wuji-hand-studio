"""Metadata-reader EOF must retire only its own positively ended lifecycle."""
from contextlib import contextmanager
import copy
import io
import json
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from connection_switch import ConnectionSwitch
from device_profiles import profile


REPORT=dict(version=1,users=[dict(user_id='wearer-fixture',name='Fixture wearer',is_default=False,
                                models=dict(left=True,right=True))])


class MetadataClient:
    agent_command='fixture-only-metadata'

    def __init__(self,held=False):
        self.entered=threading.Event();self.release=threading.Event()
        if not held:self.release.set()
        self.stdin=io.StringIO();self.closed=0;self.close_error=None

    def exec_command(self,command,timeout=None):
        if command!=self.agent_command:raise AssertionError('Unexpected controller command')
        return self.stdin,self.rows(),io.StringIO()

    def rows(self):
        self.entered.set()
        if not self.release.wait(3):raise AssertionError('Test reader was not released')
        yield 'WUJI_JSON:'+json.dumps(dict(type='sdk_user_catalog',catalog=REPORT))+'\n'

    def close(self):
        self.closed+=1
        if isinstance(self.close_error,Exception):raise self.close_error
        return self.close_error is not False


class CatalogCleanupTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with patch('bridge_config.load_config',return_value={'mode':'wsl'}),patch('official_device_discovery.use_native',return_value=True):
            c=N(lock=threading.RLock(),calibration_handover=N(workflow_lock=threading.RLock()),
                teleoperation_handover=N(_runtime=Mock()),factory=Mock(),session_epochs={1:1},action=Mock())
            service=ConnectionSwitch(c)
            state=dict(connection='disconnected',hardware={'active':False},session_generation=1,
                device_profile=profile('hand2_left'),device_id=None,identified_device=None,unconfirmed_devices=[],
                program={'active':False},group={'active':False},calibration={},calibration_connection={},
                teleoperation_connection={},calibrated_teleoperation={},maintenance={},official_studio={},
                glove=dict(busy=False,connection='disconnected',session_id=1,hardware={},feedback={}),official_viz={})
            def snapshot():
                value=copy.deepcopy(state);value['connection_switch']=service.snapshot(value);return value
            c.snapshot=snapshot;c.glove=N(snapshot=lambda:copy.deepcopy(state['glove']))
            try:yield N(c=c,service=service,state=state,snapshot=snapshot)
            finally:
                client=service.catalog_client
                if client:client.release.set()
                thread=service.catalog_thread
                if thread is not None and thread.ident is not None:thread.join(3)
                if thread is not None:self.assertFalse(thread.is_alive())

    def time_out(self,e,client):
        original=threading.Event.wait;caller=threading.get_ident()
        def wait(event,timeout=None):
            if threading.get_ident()==caller and timeout in (12,2):
                self.assertTrue(original(client.entered,2))
                return False
            return original(event,timeout)
        e.c.factory.return_value=client
        with patch.object(threading.Event,'wait',wait):
            with self.assertRaisesRegex(ValueError,'still closing'):e.service.users()

    def end(self,e,client):
        client.release.set();e.service.catalog_thread.join(3)
        self.assertFalse(e.service.catalog_thread.is_alive())
        self.assertTrue(e.service.catalog_finished.is_set())

    def test_confirmed_late_eof_retires_busy_without_accepting_late_directory(self):
        with self.environment() as e:
            first=MetadataClient(held=True);self.time_out(e,first)
            failed=e.snapshot()['connection_switch']['user_catalog']
            self.assertTrue(failed['busy']);self.end(e,first)
            calls=e.c.factory.call_count;closes=first.closed
            switch=e.snapshot()['connection_switch']
            self.assertFalse(switch['user_catalog']['busy'])
            self.assertTrue(switch['can_switch'])
            self.assertEqual(switch['user_catalog']['status'],'failed')
            self.assertEqual(switch['user_catalog']['error'],failed['error'])
            self.assertEqual(switch['user_catalog']['users'],[])
            self.assertIsNone(switch['user_catalog']['checked_at'])
            self.assertEqual(e.c.factory.call_count,calls);self.assertEqual(first.closed,closes)
            e.c.action.assert_not_called()
            second=MetadataClient();e.c.factory.return_value=second;e.service.users()
            self.assertFalse(e.service.catalog['busy'])
            self.assertEqual(e.service.catalog['users'],REPORT['users'])
            self.assertEqual(second.stdin.getvalue(),'{"name":"sdk_user_catalog"}\n')

    def test_finished_signal_without_reader_exit_cannot_retire_busy(self):
        with self.environment() as e:
            client=MetadataClient(held=True);self.time_out(e,client)
            # Finished can be observed just before the reader actually returns.
            e.service.catalog_finished.set()
            self.assertTrue(e.service.catalog_thread.is_alive())
            self.assertTrue(e.snapshot()['connection_switch']['user_catalog']['busy'])
            self.assertFalse(e.service._blocked(e.state) is None)
            self.end(e,client)
            self.assertFalse(e.snapshot()['connection_switch']['user_catalog']['busy'])

    def test_unknown_close_keeps_original_owner_until_explicit_retry(self):
        for error in (False,OSError('fixture close unconfirmed')):
            with self.subTest(error=repr(error)),self.environment() as e:
                first=MetadataClient(held=True)
                # Initial timeout close may fail; the final reader close also
                # fails, so no passive snapshot can assert transport cleanup.
                first.close_error=error
                original=threading.Event.wait;caller=threading.get_ident()
                def wait(event,timeout=None):
                    if threading.get_ident()==caller and timeout in (12,2):
                        self.assertTrue(original(first.entered,2));return False
                    return original(event,timeout)
                e.c.factory.return_value=first
                with patch.object(threading.Event,'wait',wait),self.assertRaises((ValueError,OSError)):
                    e.service.users()
                self.end(e,first)
                calls=e.c.factory.call_count;closes=first.closed
                for _ in range(2):self.assertTrue(e.snapshot()['connection_switch']['user_catalog']['busy'])
                self.assertEqual(e.c.factory.call_count,calls);self.assertEqual(first.closed,closes)
                self.assertIs(e.service.catalog_client,first)
                first.close_error=None;second=MetadataClient();e.c.factory.return_value=second
                e.service.users()
                self.assertFalse(e.service.catalog['busy']);self.assertEqual(first.closed,closes+1)
                self.assertIs(e.service.catalog_client,second)

    def test_old_ended_reader_proof_cannot_retire_successor_during_thread_construction(self):
        with self.environment() as e:
            first=MetadataClient(held=True);self.time_out(e,first);self.end(e,first)
            # This GET should retire A; B is then an explicit new request.
            e.snapshot()
            second=MetadataClient(held=True);e.c.factory.return_value=second
            entered=threading.Event();release=threading.Event();original=threading.Thread;errors=[]
            def construct(*args,**kwargs):
                if kwargs.get('name')=='SDK wearer directory':
                    entered.set()
                    if not release.wait(3):raise AssertionError('Test did not release thread constructor')
                return original(*args,**kwargs)
            def run():
                try:e.service.users()
                except Exception as error:errors.append(error)
            caller=original(target=run)
            with patch('connection_switch.threading.Thread',side_effect=construct):
                caller.start()
                try:
                    self.assertTrue(entered.wait(2))
                    view=e.snapshot()['connection_switch']
                    self.assertTrue(view['user_catalog']['busy'])
                    self.assertFalse(view['can_switch'])
                    self.assertEqual(view['user_catalog']['status'],'loading')
                finally:release.set();second.release.set();caller.join(3)
            self.assertFalse(caller.is_alive());self.assertEqual(errors,[])
            self.assertFalse(e.snapshot()['connection_switch']['user_catalog']['busy'])

    def test_absent_or_unfinished_reader_proof_does_not_clear_unknown_busy(self):
        with self.environment() as e:
            e.service.catalog.update(busy=True,status='failed',error='unknown original reader')
            for finished in (None,threading.Event()):
                e.service.catalog_finished=finished
                self.assertTrue(e.snapshot()['connection_switch']['user_catalog']['busy'])
                self.assertIsNotNone(e.service._blocked(e.state))
            e.c.factory.assert_not_called();e.c.action.assert_not_called()

    def test_thread_constructor_rejection_before_transport_allows_explicit_retry(self):
        with self.environment() as e:
            client=MetadataClient();e.c.factory.return_value=client
            with patch('connection_switch.threading.Thread',side_effect=RuntimeError('fixture thread creation unavailable')):
                with self.assertRaisesRegex(RuntimeError,'thread creation unavailable'):e.service.users()
            e.c.factory.assert_not_called()
            switch=e.snapshot()['connection_switch']
            self.assertFalse(switch['user_catalog']['busy'])
            self.assertTrue(switch['can_switch'])
            self.assertEqual(switch['user_catalog']['status'],'failed')
            self.assertEqual(switch['user_catalog']['users'],[])
            self.assertIn('thread creation unavailable',switch['user_catalog']['error'])
            e.service.users()
            self.assertFalse(e.snapshot()['connection_switch']['user_catalog']['busy'])
            e.c.factory.assert_called_once();e.c.action.assert_not_called()


if __name__=='__main__':unittest.main()
