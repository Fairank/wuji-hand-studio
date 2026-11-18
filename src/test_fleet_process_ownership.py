"""Private Windows process ownership without replacing session ACK semantics."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from device_fleet import DeviceFleet


class Process:
    def __init__(self):self.code=None;self.events=[]
    def poll(self):return self.code
    def terminate(self):self.events.append('terminate')
    def kill(self):self.events.append('kill')
    def wait(self,timeout):self.events.append(('wait',timeout));self.code=0;return 0


class Owner:
    def __init__(self,process):self.process=process;self.results=[True];self.calls=[];self.confirm_primary=True
    def close(self,timeout=5):
        self.calls.append(timeout);result=self.results.pop(0) if self.results else True
        if isinstance(result,Exception):raise result
        if result and self.confirm_primary:self.process.code=0
        return result


class FleetProcessOwnershipTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        self.proc=Process();self.owner=Owner(self.proc)
        # Explicit OS boundary: fake Popen handles never enter AssignProcessToJobObject.
        self.windows=patch('device_fleet.sys.platform','win32');self.windows.start();self.addCleanup(self.windows.stop)
        factory=patch('device_fleet.OwnedProcess',return_value=self.owner,create=True);self.factory=factory.start();self.addCleanup(factory.stop)
        fallback=patch('device_fleet.subprocess.Popen',return_value=self.proc);self.fallback=fallback.start();self.addCleanup(fallback.stop)
        gate=patch('studio_component.lease_state',return_value={'reserved':False});gate.start();self.addCleanup(gate.stop)
        self.fleet=DeviceFleet(self.root,self.root,'http://127.0.0.1:9999');self.addCleanup(self.fleet._beat_pool.shutdown)
        self.fleet.get=Mock(return_value={});self.fleet.post=Mock(return_value={'ok':True})

    def create(self):return self.fleet.create('Synthetic preview','hand2_left')['id']

    def test_windows_create_pins_private_owner_and_forget_closes_tree_after_ack(self):
        ident=self.create();record=self.fleet.children[ident]
        self.factory.assert_called_once();self.fallback.assert_not_called()
        self.assertIs(record['process_owner'],self.owner);self.assertIs(record['process'],self.proc)
        self.assertEqual(self.fleet.remove(ident),{'removed':True})
        self.assertEqual(self.owner.calls,[5]);self.assertEqual(self.proc.events,[])
        self.assertEqual(json.loads(self.fleet.records.read_text('utf8')),[])

    def test_startup_failure_closes_owned_tree_and_preserves_diagnostics(self):
        self.proc.code=1
        with self.assertRaisesRegex(ValueError,'failed to start'):self.create()
        self.assertEqual(self.owner.calls,[5]);self.assertEqual(self.fleet.children,{})
        self.assertEqual(len(list((self.root/'devices').glob('*/console.log'))),1)

    def test_owner_construction_failure_never_falls_back_to_unowned_spawn(self):
        self.factory.side_effect=OSError('Synthetic assignment failure')
        with self.assertRaises(OSError):self.create()
        self.fallback.assert_not_called();self.assertEqual(self.fleet.children,{})

    def test_startup_exit_unknown_retains_owner_for_explicit_close_retry(self):
        self.proc.code=1;self.owner.results=[False,True]
        with self.assertRaisesRegex(ValueError,'cleanup.*unconfirmed'):self.create()
        record=next(iter(self.fleet.children.values()));self.assertIs(record['process_owner'],self.owner)
        self.assertTrue(self.fleet.close()['ok']);self.assertEqual(self.fleet.children,{})
        self.assertEqual(self.owner.calls,[5,5])

    def test_dead_primary_with_unconfirmed_tree_retains_catalog_and_exact_ack_on_retry(self):
        ident=self.create();before=self.fleet.records.read_bytes();self.owner.results=[False,True]
        self.proc.code=1  # A dead root is insufficient while owned descendants may remain.
        with self.assertRaisesRegex(ValueError,'exit.*not confirmed'):self.fleet.remove(ident)
        self.assertIs(self.fleet.children[ident]['process_owner'],self.owner);self.assertEqual(self.fleet.records.read_bytes(),before)
        self.assertEqual(self.fleet.remove(ident),{'removed':True});self.assertEqual(self.fleet.post.call_count,1)
        self.assertEqual(self.owner.calls,[5,5])

    def test_rejected_session_close_never_uses_os_exit_as_disable_ack(self):
        ident=self.create();self.fleet.post.return_value={'ok':False,'error':'SDK cleanup unknown'}
        self.assertFalse(self.fleet.remove(ident).get('ok'));self.assertFalse(self.fleet.close()['ok'])
        self.assertEqual(self.owner.calls,[]);self.assertIn(ident,self.fleet.children)

    def test_replaced_primary_cannot_reuse_or_close_an_old_process_owner(self):
        ident=self.create();newer=Process();self.fleet.children[ident]['process']=newer
        with self.assertRaisesRegex(ValueError,'owner changed'):self.fleet.remove(ident)
        self.assertEqual(self.owner.calls,[]);self.assertEqual(newer.events,[]);self.assertIn(ident,self.fleet.children)

    def test_close_failure_or_raise_keeps_owner_and_allows_retry(self):
        ident=self.create();self.owner.results=[OSError('Synthetic Job failure'),False,True]
        for _ in range(2):
            self.assertFalse(self.fleet.close()['ok']);self.assertIs(self.fleet.children[ident]['process_owner'],self.owner);self.assertFalse(self.fleet._closing)
        self.assertTrue(self.fleet.close()['ok']);self.assertEqual(self.owner.calls,[5,5,5]);self.assertEqual(self.proc.events,[])

    def test_false_primary_exit_and_expired_budget_cannot_report_close(self):
        ident=self.create();self.owner.confirm_primary=False
        self.assertFalse(self.fleet.close()['ok']);self.assertIn(ident,self.fleet.children)
        self.owner.calls.clear();self.assertFalse(self.fleet.close(deadline=10,clock=lambda:11)['ok']);self.assertEqual(self.owner.calls,[])
        self.owner.confirm_primary=True;self.assertTrue(self.fleet.close(deadline=13,clock=lambda:11)['ok']);self.assertEqual(self.owner.calls,[2])

    def test_nonwindows_retains_existing_popen_lifecycle(self):
        with patch('device_fleet.sys.platform','linux'):
            ident=self.create();self.factory.assert_not_called();self.fallback.assert_called_once()
            self.assertEqual(self.fleet.remove(ident),{'removed':True})
        self.assertEqual(self.proc.events,['terminate',('wait',5)])


if __name__=='__main__':unittest.main()
