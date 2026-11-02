"""Actual Controller feedback/sync/reconnect with synthetic line transports."""
from contextlib import contextmanager, ExitStack
import copy
import io
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

from console_server import Controller
from parameter_file import parse_parameters
from task_sessions import context_digest

SERIAL='WH2-SYNTHETIC-SYNC'
PROFILE='hand2_right'


class FeedbackClient:
    parameters_in_launch=True
    agent_command='SYNTHETIC-READ-ONLY-FEEDBACK'

    def __init__(self,source,*,failed=False,ack=True,bad_readback=False):
        self.source=source;self.failed=failed;self.ack=ack;self.bad_readback=bad_readback
        self.channel=N(closed=False);self.lines=queue.Queue();self.commands=[]
        self.loaded=None;self.closed=False;self.exec_count=0

    def emit(self,event):
        self.lines.put('WUJI_JSON:'+json.dumps(event)+'\n')

    def write(self,text):
        for raw in text.splitlines():
            command=json.loads(raw);self.commands.append(command)
            if command['name']=='connect':
                reported=copy.deepcopy(self.loaded)
                if self.bad_readback:reported['KP']=99
                self.emit(dict(type='identity',profile=PROFILE,device_id=SERIAL))
                self.emit(dict(type='state',connection='error' if self.failed else 'connected',
                    message='Synthetic feedback reopen failed' if self.failed else '',
                    latest=None,metrics=dict(age_ms=0,host_hz=100,device_hz=100),
                    recording=dict(active=False,frames=0),device_id=SERIAL,joint_rates=[],
                    hardware=dict(active=False,stop_confirmed=True,ready=False,
                        commissioning_policy=dict(editable_parameters=reported))))
            elif command['name']=='disconnect':
                if self.ack:self.emit(dict(type='closed',device_id=SERIAL,
                    hardware=dict(active=False,stop_confirmed=True)))
                self.lines.put(None)
            else:
                raise AssertionError('No motor/other commands allowed: '+command['name'])
        return len(text)

    def flush(self):pass

    def __iter__(self):
        while True:
            value=self.lines.get(timeout=3)
            if value is None:return
            yield value

    def exec_command(self,command,timeout=None):
        if command!=self.agent_command:raise AssertionError('Unexpected transport command')
        self.exec_count+=1;self.loaded=parse_parameters(self.source())
        return self,self,io.StringIO()

    def close(self):
        self.closed=True;self.channel.closed=True;self.lines.put(None)
        return True


class ImmediateTimer:
    def __init__(self,interval,function,args=(),kwargs=None):
        self.function=function;self.args=args;self.kwargs=kwargs or {}
    def start(self):self.function(*self.args,**self.kwargs)


def wait_for(predicate,timeout=2):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.005)
    raise AssertionError('Synthetic operation did not reach expected state')


class ParameterSyncTransactionTests(unittest.TestCase):
    @contextmanager
    def environment(self,*,connected=True,failed_reconnect=False,ack=True,bad_readback=False):
        with tempfile.TemporaryDirectory(prefix='parameter-transaction-') as directory,ExitStack() as stack:
            root=Path(directory);source=root/'motion_parameters.py'
            source.write_bytes(Path(__file__).with_name('motion_parameters.py').read_bytes())
            stack.enter_context(patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=str(root/'cli')))
            for module in ('console_server','doctor','runtime_paths'):
                stack.enter_context(patch(module+'.DATA',root))
            stack.enter_context(patch('bridge_config.load_config',return_value=dict(mode='wsl',cli='wuji')))
            stack.enter_context(patch('managed_runtime.status',return_value=dict(ready=True,busy=False)))
            stack.enter_context(patch('console_server.threading.Timer',ImmediateTimer))
            clients=[];transfers=[]
            c=Controller(factory=Mock(side_effect=AssertionError('Factory not configured')),
                         reports=root/'reports',parameter_path=source)
            c.state['device_profile']=dict(id=PROFILE,generation='hand2',side='right')
            old=FeedbackClient(c.parameters.source,ack=ack)
            transfer=N(parameters_in_launch=True,close=Mock(return_value=True))
            new=FeedbackClient(c.parameters.source,failed=failed_reconnect,bad_readback=bad_readback)
            planned=[old,transfer,new] if connected else [transfer]
            def factory():
                if not planned:raise AssertionError('Unexpected automatic transport retry')
                value=planned.pop(0);clients.append(value);return value
            c.factory=Mock(side_effect=factory)
            original_transfer=c.parameters.transfer
            def transferring(client,text):
                transfers.append(dict(client=client,source=text))
                return original_transfer(client,text)
            c.parameters.transfer=Mock(side_effect=transferring)
            c.device_inventory=Mock(return_value=dict(devices=[dict(sn=SERIAL,kind='hand',
                generation='hand2',side='right',stale=False,conflict=False)],workspace_errors=[]))
            try:
                if connected:
                    c.action(dict(name='connect',serial=SERIAL,auto_detect=True))
                    wait_for(lambda:c.state['connection']=='connected')
                draft=copy.deepcopy(c.parameters.snapshot()['values']);draft['KP']=7
                yield N(c=c,root=root,source=source,old=old,new=new,transfer=transfer,
                        clients=clients,transfers=transfers,draft=draft)
            finally:
                for value in clients:
                    if isinstance(value,FeedbackClient):value.close()
                for record in list(c.feedback_sessions.values()):
                    thread=record.get('thread')
                    if thread and thread is not threading.current_thread():thread.join(2)

    def command(self,e,nonce='b'):
        state=e.c.snapshot()
        return dict(name='parameters_sync_reconnect',request_id=nonce*32,
                    expected_owner=context_digest(state),
                    revision=e.c.parameters.snapshot()['revision'],values=e.draft)

    def test_readonly_hand_saves_syncs_and_reconnects_same_identity_with_new_values(self):
        with self.environment() as e:
            before=e.c.generation
            try:result=e.c.action(self.command(e))
            except ValueError as error:self.fail('Read-only parameter transaction rejected: '+str(error))
            wait_for(lambda:e.c.state['connection']=='connected' and e.c.generation>before)
            state=e.c.snapshot();self.assertEqual(e.c.state['device_id'],SERIAL)
            self.assertEqual(state['device_profile']['id'],PROFILE)
            self.assertEqual(e.new.loaded['KP'],7)
            self.assertEqual(state['hardware']['commissioning_policy']['editable_parameters'],e.draft)
            self.assertEqual(e.c.parameters.sync_status['state'],'synced')
            self.assertEqual(e.c.parameters.sync_status['reconnect_state'],'connected')
            self.assertEqual(e.c.factory.call_count,3)
            self.assertEqual([v['name'] for v in e.old.commands],['connect','disconnect'])
            self.assertEqual([v['name'] for v in e.new.commands],['connect'])
            self.assertEqual(len(e.transfers),1);e.transfer.close.assert_called_once()
            self.assertIn('parameters',result);self.assertIn('connection_switch',result)
            self.assertIs(state['hardware']['active'],False)

    def test_disconnected_save_sync_never_opens_feedback(self):
        with self.environment(connected=False) as e:
            try:result=e.c.action(self.command(e))
            except ValueError as error:self.fail('Disconnected parameter transaction rejected: '+str(error))
            self.assertEqual(e.c.parameters.sync_status['state'],'synced')
            self.assertEqual(e.c.parameters.sync_status['reconnect_state'],'not_requested')
            self.assertEqual(e.c.parameters.snapshot()['values']['KP'],7)
            self.assertEqual(e.c.factory.call_count,1)
            self.assertEqual(e.c.state['connection'],'disconnected')
            self.assertEqual(e.old.commands+e.new.commands,[])
            self.assertIn('parameters',result)

    def test_stale_owner_is_rejected_before_save_release_or_transfer(self):
        with self.environment() as e:
            before=e.source.read_bytes();command=self.command(e);command['expected_owner']='0'*64
            with self.assertRaises(ValueError):e.c.action(command)
            self.assertEqual(e.source.read_bytes(),before)
            self.assertEqual([v['name'] for v in e.old.commands],['connect'])
            self.assertEqual(len(e.transfers),0);self.assertEqual(e.c.factory.call_count,1)

    def test_invalid_draft_or_stale_file_revision_keeps_existing_feedback(self):
        for failure in ('invalid_value','stale_revision'):
            with self.subTest(failure=failure),self.environment() as e:
                before=e.source.read_bytes();command=self.command(e)
                if failure=='invalid_value':command['values']['KP']=float('nan')
                else:command['revision']='0'*64
                with self.assertRaises(ValueError):e.c.action(command)
                self.assertEqual(e.source.read_bytes(),before)
                self.assertEqual(e.c.factory.call_count,1)
                self.assertEqual([v['name'] for v in e.old.commands],['connect'])
                self.assertEqual(e.transfers,[])

    def test_synced_values_survive_failed_reconnection_without_automatic_retry(self):
        with self.environment(failed_reconnect=True) as e:
            with self.assertRaises(ValueError):e.c.action(self.command(e))
            self.assertEqual(e.c.parameters.sync_status['state'],'synced')
            self.assertEqual(e.c.parameters.sync_status['reconnect_state'],'failed')
            self.assertEqual(e.c.parameters.snapshot()['values']['KP'],7)
            self.assertEqual(e.c.factory.call_count,3);self.assertEqual(len(e.transfers),1)
            self.assertFalse(any(v['name'].startswith('hardware_') for client in (e.old,e.new) for v in client.commands))

    def test_missing_old_closed_ack_blocks_save_transfer_and_new_open(self):
        with self.environment(ack=False) as e:
            before=e.source.read_bytes()
            with self.assertRaises(ValueError):e.c.action(self.command(e))
            self.assertEqual(e.source.read_bytes(),before)
            self.assertEqual(len(e.transfers),0);self.assertEqual(e.c.factory.call_count,1)
            self.assertEqual(e.new.commands,[])

    def test_active_hand_is_not_automatically_stopped_by_parameter_sync(self):
        with self.environment() as e:
            e.c.state['hardware'].update(active=True,stop_confirmed=False)
            before=e.source.read_bytes()
            with self.assertRaises(ValueError):e.c.action(self.command(e))
            self.assertEqual(e.source.read_bytes(),before)
            self.assertEqual([v['name'] for v in e.old.commands],['connect'])
            self.assertEqual(len(e.transfers),0)

    def test_wrong_reconnected_parameter_readback_never_claims_applied(self):
        with self.environment(bad_readback=True) as e:
            with self.assertRaises(ValueError):e.c.action(self.command(e))
            self.assertEqual(e.c.parameters.sync_status['state'],'synced')
            self.assertEqual(e.c.parameters.sync_status['reconnect_state'],'failed')
            self.assertEqual(e.c.parameters.snapshot()['values']['KP'],7)
            self.assertEqual(e.c.factory.call_count,3)
            self.assertIs(e.c.state['hardware'].get('active'),False)

    def test_replaying_consumed_request_cannot_resave_or_open_again(self):
        with self.environment() as e:
            try:e.c.action(self.command(e))
            except ValueError as error:self.fail('Initial transaction rejected: '+str(error))
            before=e.source.read_bytes();calls=e.c.factory.call_count
            # Fresh owner/revision do not turn the consumed request nonce into
            # another command or a retry of its side effects.
            with self.assertRaises(ValueError):e.c.action(self.command(e))
            self.assertEqual(e.source.read_bytes(),before)
            self.assertEqual(e.c.factory.call_count,calls)
            self.assertEqual(len(e.transfers),1)

    def test_exact_cancel_after_transfer_return_prevents_new_feedback_open(self):
        with self.environment() as e:
            entered=threading.Event();release=threading.Event();errors=[]
            original=e.c.parameters.transfer.side_effect
            def transfer(client,text):
                result=original(client,text);entered.set()
                if not release.wait(3):raise AssertionError('Synthetic transfer barrier timed out')
                return result
            e.c.parameters.transfer.side_effect=transfer
            command=self.command(e)
            def work():
                try:e.c.action(command)
                except BaseException as error:errors.append(error)
            thread=threading.Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(2),'Transaction never reached transfer')
                with e.c.connection_switch.lock:state=copy.deepcopy(e.c.connection_switch.state)
                e.c.action(dict(name='connection_switch_cancel',request_id=state['request_id'],
                                attempt_id=state['attempt_id']))
            finally:release.set();thread.join(3)
            self.assertFalse(thread.is_alive());self.assertEqual(len(errors),1)
            self.assertIsInstance(errors[0],ValueError)
            self.assertEqual(e.c.parameters.sync_status['state'],'synced')
            self.assertEqual(e.c.factory.call_count,2)
            self.assertEqual(e.new.commands,[])

    def test_explicit_exact_feedback_disconnect_does_not_sync_or_reconnect(self):
        with self.environment() as e:
            state=e.c.snapshot();before=e.source.read_bytes()
            e.c.action(dict(name='hand_feedback_disconnect',expected_owner=context_digest(state),
                            serial=SERIAL,expected_session=state['session_generation']))
            self.assertEqual(e.c.state['connection'],'disconnected')
            self.assertEqual([v['name'] for v in e.old.commands],['connect','disconnect'])
            self.assertEqual(e.c.factory.call_count,1);self.assertEqual(e.transfers,[])
            self.assertEqual(e.source.read_bytes(),before)

    def test_old_feedback_disconnect_cannot_close_successor_same_hand_session(self):
        with self.environment() as e:
            state=e.c.snapshot()
            old=dict(name='hand_feedback_disconnect',expected_owner=context_digest(state),
                     serial=SERIAL,expected_session=state['session_generation'])
            e.c.action(old)
            e.c.factory.side_effect=lambda:e.new
            e.c.action(dict(name='connect',serial=SERIAL,auto_detect=True))
            wait_for(lambda:e.c.state['connection']=='connected')
            with self.assertRaises(ValueError):e.c.action(old)
            self.assertEqual([v['name'] for v in e.new.commands],['connect'])
            self.assertEqual(e.c.state['connection'],'connected')
            self.assertEqual(e.c.parameters.sync_status['state'],'not_synced')

    def test_error_session_can_be_explicitly_ended_then_sync_has_no_reconnect_target(self):
        with self.environment() as e:
            generation=e.c.generation
            e.c.event(dict(type='error',message='Synthetic three-second feedback loss'),generation)
            state=e.c.snapshot();self.assertEqual(state['connection'],'error')
            self.assertIsNone(state['identified_device'])
            retained=state.get('hand_feedback_session') or {}
            self.assertEqual(retained.get('serial'),SERIAL)
            self.assertEqual(retained.get('session'),generation)
            self.assertEqual(retained.get('profile'),PROFILE)
            self.assertIs(retained.get('can_disconnect'),True)
            e.c.action(dict(name='hand_feedback_disconnect',expected_owner=context_digest(state),
                            serial=SERIAL,expected_session=generation))
            self.assertEqual(e.c.state['connection'],'disconnected')
            self.assertEqual([v['name'] for v in e.old.commands],['connect','disconnect'])
            e.c.factory.side_effect=lambda:e.transfer
            result=e.c.action(self.command(e,'c'))
            self.assertEqual(e.c.parameters.sync_status['state'],'synced')
            self.assertEqual(e.c.parameters.sync_status['reconnect_state'],'not_requested')
            self.assertEqual(e.c.factory.call_count,2)
            self.assertEqual(e.new.commands,[])
            self.assertEqual(result['parameters']['values']['KP'],7)


if __name__=='__main__':unittest.main()
