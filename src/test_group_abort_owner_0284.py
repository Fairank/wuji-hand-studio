"""Late group cleanup must retain its original worker/lease ownership.

Public Controller actions and reader threads are real. The queued transport
only supplies synthetic worker status and stop ACKs; no SDK is imported.
"""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from console_server import Controller
import test_cold_stop_journal_guard as status_fixture


class DeferredWatch:
    def __init__(self, **_):pass
    def start(self):pass
    def join(self, timeout=None):pass
    def is_alive(self):return False


class Transport(status_fixture.StatusTransport):
    def __init__(self, serial):
        super().__init__(serial)
        self.stop_mode='ack'
        self.fail_start=None

    def exec_command(self, command, timeout=None):
        stream,output,errors=super().exec_command(command,timeout)
        transport=self
        class Input:
            pending_flush_failure=False
            def write(self,text):
                commands=[json.loads(line) for line in text.splitlines() if line]
                if commands and commands[0]['name']=='hardware_stop' and transport.stop_mode!='ack':
                    transport.sent.extend(commands);transport.disable_receipts+=1
                    transport.ready(active=None,confirmed=False)
                    if transport.stop_mode=='raise':raise OSError('Synthetic stop receipt lost')
                    return len(text)
                result=stream.write(text)
                if commands and commands[0]['name'] in ('hardware_trial','hardware_probe'):
                    if transport.fail_start=='write':raise OSError('Synthetic start write receipt lost')
                    if transport.fail_start=='flush':self.pending_flush_failure=True
                if commands and commands[0]['name']=='disconnect':transport.close()
                return result
            def flush(self):
                if self.pending_flush_failure:
                    self.pending_flush_failure=False
                    raise OSError('Synthetic start flush receipt lost')
                return stream.flush()
        return Input(),output,errors


class GroupAbortOwnerTests(unittest.TestCase):
    def setUp(self):
        metadata=patch('managed_runtime.registered',return_value=None)
        metadata.start();self.addCleanup(metadata.stop)
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.transports=[]
        def factory():
            transport=Transport(self.next_serial);self.transports.append(transport)
            return transport
        self.c=Controller(factory=factory,reports=Path(self.folder.name)/'reports')
        self.addCleanup(self.cleanup)
        self.connect('SYNTHETIC-GROUP-ORIGINAL')

    def cleanup(self):
        for transport in self.transports:transport.close()
        for record in self.c.feedback_sessions.values():record['thread'].join(3)

    def wait(self,predicate):
        deadline=time.monotonic()+3
        while True:
            with self.c.lock:
                if predicate():return
            if time.monotonic()>=deadline:self.fail('Synthetic reader status timeout')
            time.sleep(.001)

    def connect(self,serial):
        self.next_serial=serial
        self.c.action(dict(name='connect',serial=serial))
        self.wait(lambda:self.c.state['connection']=='connected' and self.c.state['device_id']==serial)

    def prepare(self,token='a'*32,mode='hardware'):
        with patch('group_participant.threading.Thread',DeferredWatch):
            self.c.action(dict(name='group_prepare',token=token,mode=mode,kind='pair',
                action='pair_wave',speed=1,cycles=1,amplitude=.5,workspace_clear=True))
        return token

    def arm(self,token='a'*32):
        self.c.action(dict(name='group_arm',token=token))
        self.wait(lambda:self.c.state['hardware'].get('active') is True)
        return self.c.hardware_lease

    def stop(self):
        self.c.action(dict(name='hardware_stop'))
        self.wait(lambda:self.c.hardware_lease is None and self.c.state['hardware'].get('active') is False)

    def probe(self):
        result=self.c.action(dict(name='hardware_probe',index=6,direction=1,workspace_clear=True))
        self.wait(lambda:self.c.state['hardware'].get('active') is True)
        return result['lease']

    def assert_late_abort_does_not_stop(self,token,lease):
        transport=self.transports[-1];before=list(transport.sent)
        self.c.action(dict(name='group_abort',token=token))
        self.assertEqual(transport.sent,before)
        self.assertEqual(self.c.hardware_lease,lease)
        self.assertIs(self.c.state['hardware']['active'],True)

    def test_old_abort_does_not_stop_reconnected_different_serial(self):
        token=self.prepare();old=self.arm();self.stop()
        generation=self.c.generation
        self.c.action(dict(name='disconnect'));self.connect('SYNTHETIC-REPLACEMENT')
        lease=self.probe()
        self.assertNotEqual(self.c.generation,generation);self.assertNotEqual(lease,old)
        self.assert_late_abort_does_not_stop(token,lease)

    def test_old_abort_does_not_stop_reconnected_same_serial(self):
        serial=self.c.state['device_id'];token=self.prepare();old=self.arm();self.stop()
        generation=self.c.generation
        self.c.action(dict(name='disconnect'));self.connect(serial)
        lease=self.probe()
        self.assertNotEqual(self.c.generation,generation);self.assertNotEqual(lease,old)
        self.assert_late_abort_does_not_stop(token,lease)

    def test_old_abort_does_not_stop_new_lease_on_same_connection(self):
        token=self.prepare();old=self.arm();self.stop()
        generation=self.c.generation;lease=self.probe()
        self.assertEqual(self.c.generation,generation);self.assertNotEqual(lease,old)
        self.assert_late_abort_does_not_stop(token,lease)

    def test_readonly_prepare_abort_sends_no_hardware_stop(self):
        token=self.prepare();transport=self.transports[-1];before=list(transport.sent)
        hardware=dict(self.c.state['hardware']);journal=self.c.termination.snapshot()
        result=self.c.action(dict(name='group_abort',token=token))['group']
        self.assertEqual(result,dict(stopping=True,token=token,mode='hardware',read_only_released=True))
        self.assertEqual(transport.sent,before)
        self.assertEqual(self.c.state['hardware'],hardware)
        self.assertEqual(self.c.termination.snapshot(),journal)
        self.assertIsNone(self.c.hardware_lease);self.assertFalse(self.c.group.active)
        self.assert_late_abort_does_not_stop(token,self.probe())

    def test_inactive_original_unknown_lease_can_retry_stop(self):
        token=self.prepare();lease=self.arm();transport=self.transports[-1]
        transport.stop_mode='unknown'
        self.c.action(dict(name='group_abort',token=token))
        self.wait(lambda:self.c.state['hardware'].get('active') is None)
        self.assertFalse(self.c.group.active);self.assertEqual(self.c.hardware_lease,lease)
        self.assertTrue(self.c.termination.snapshot())
        before=transport.disable_receipts;transport.stop_mode='ack'
        self.c.action(dict(name='group_abort',token=token))
        self.wait(lambda:self.c.hardware_lease is None)
        self.assertEqual(transport.disable_receipts,before+1)
        self.assertEqual(self.c.termination.snapshot(),[])

    def test_stop_send_failure_keeps_original_identity_for_explicit_retry(self):
        token=self.prepare();lease=self.arm();transport=self.transports[-1]
        transport.stop_mode='raise'
        with self.assertRaisesRegex(OSError,'receipt lost'):
            self.c.action(dict(name='group_abort',token=token))
        self.assertFalse(self.c.group.active);self.assertEqual(self.c.hardware_lease,lease)
        transport.stop_mode='ack'
        self.c.action(dict(name='group_abort',token=token))
        self.wait(lambda:self.c.hardware_lease is None)
        self.assertEqual(self.c.termination.snapshot(),[])

    def test_arm_write_or_flush_error_still_binds_actual_pending_lease(self):
        for position in ('write','flush'):
            with self.subTest(position=position):
                token=self.prepare(('a' if position=='write' else 'b')*32)
                transport=self.transports[-1];transport.fail_start=position
                with self.assertRaisesRegex(OSError,'receipt lost'):
                    self.c.action(dict(name='group_arm',token=token))
                lease=self.c.hardware_lease;self.assertIsNotNone(lease)
                self.assertTrue(self.c.termination.snapshot())
                result=self.c.action(dict(name='group_abort',token=token))['group']
                self.assertNotIn('read_only_released',result)
                self.wait(lambda:self.c.hardware_lease is None)
                self.assertEqual(self.c.termination.snapshot(),[])
                transport.fail_start=None

    def test_old_token_cannot_abort_new_group(self):
        old=self.prepare();self.arm();self.stop()
        new=self.prepare('b'*32);lease=self.arm(new);transport=self.transports[-1]
        before=list(transport.sent)
        with self.assertRaisesRegex(ValueError,'Expired group token'):
            self.c.action(dict(name='group_abort',token=old))
        self.assertEqual(transport.sent,before);self.assertEqual(self.c.hardware_lease,lease)
        self.c.action(dict(name='group_abort',token=new))
        self.wait(lambda:self.c.hardware_lease is None)

    def test_top_level_explicit_stop_still_stops_current_replacement(self):
        self.prepare();self.arm();self.stop();lease=self.probe()
        self.assertEqual(self.c.hardware_lease,lease)
        self.stop()
        self.assertEqual(self.c.termination.snapshot(),[])

    def test_old_preview_abort_does_not_stop_replacement_run(self):
        token=self.prepare(mode='preview');old=self.c.player.run_id
        self.c.action(dict(name='group_abort',token=token))
        self.c.action(dict(name='demo_start',action='open',speed=.5,cycles=3))
        new=self.c.player.run_id;self.assertNotEqual(old,new)
        with self.assertRaisesRegex(ValueError,'Preview changed'):
            self.c.action(dict(name='group_abort',token=token))
        self.assertEqual(self.c.player.run_id,new);self.assertTrue(self.c.player.active)
        self.assertEqual(self.c.state['view_source'],'preview')
        self.c.action(dict(name='demo_stop'))
        self.assertFalse(self.c.player.active)

    def test_original_preview_stop_error_can_retry_without_changing_run(self):
        token=self.prepare(mode='preview');run=self.c.player.run_id
        original=self.c.player.command
        def failed(command):
            if command['name']=='demo_stop':raise RuntimeError('Synthetic preview stop failed')
            return original(command)
        with patch.object(self.c.player,'command',side_effect=failed):
            with self.assertRaisesRegex(RuntimeError,'stop failed'):
                self.c.action(dict(name='group_abort',token=token))
        self.assertFalse(self.c.group.active);self.assertTrue(self.c.player.active)
        self.assertEqual(self.c.player.run_id,run)
        self.c.action(dict(name='group_abort',token=token))
        self.assertFalse(self.c.player.active)
        self.c.action(dict(name='group_abort',token=token))
        self.assertEqual(self.c.player.run_id,run)

    def test_ever_armed_reservation_never_claims_readonly_after_stop_ack(self):
        token=self.prepare();self.arm();self.stop()
        before=list(self.transports[-1].sent)
        result=self.c.action(dict(name='group_abort',token=token))['group']
        self.assertNotIn('read_only_released',result)
        self.assertEqual(self.transports[-1].sent,before)

    def test_new_readonly_prepare_resets_only_its_reservation_receipt(self):
        old=self.prepare();self.arm();self.stop()
        new=self.prepare('b'*32)
        before=list(self.transports[-1].sent)
        with self.assertRaisesRegex(ValueError,'Expired group token'):
            self.c.action(dict(name='group_abort',token=old))
        self.assertTrue(self.c.group.active);self.assertEqual(self.transports[-1].sent,before)
        result=self.c.action(dict(name='group_abort',token=new))['group']
        self.assertEqual(result,dict(stopping=True,token=new,mode='hardware',read_only_released=True))

    def test_watch_start_failure_still_releases_only_readonly_reservation(self):
        token='a'*32;before=list(self.transports[-1].sent)
        with patch('group_participant.threading.Thread',side_effect=RuntimeError('Synthetic watcher unavailable')):
            with self.assertRaisesRegex(RuntimeError,'watcher unavailable'):
                self.c.action(dict(name='group_prepare',token=token,mode='hardware',kind='pair',
                    action='pair_wave',speed=1,cycles=1,amplitude=.5,workspace_clear=True))
        self.assertFalse(self.c.group.active);self.assertIsNone(self.c.group.mode)
        result=self.c.action(dict(name='group_abort',token=token))['group']
        self.assertEqual(result,dict(stopping=True,token=token,mode='hardware',read_only_released=True))
        self.assertEqual(self.transports[-1].sent,before)

    def test_unprepared_or_preview_abort_has_no_hardware_readonly_receipt(self):
        result=self.c.action(dict(name='group_abort'))['group']
        self.assertNotIn('read_only_released',result)
        token=self.prepare(mode='preview')
        result=self.c.action(dict(name='group_abort',token=token))['group']
        self.assertNotIn('read_only_released',result)


if __name__=='__main__':unittest.main()
