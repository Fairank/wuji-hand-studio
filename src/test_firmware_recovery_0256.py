"""New recovery sequences; all CLI/network/device boundaries are synthetic."""
import copy
import json
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from firmware_service import FirmwareReservationHeld,ReadOnlyQueryUnconfirmed
from test_firmware_direct_route_0255 import DirectHarness,SN,OTHER


class FirmwareRecovery0256Tests(unittest.TestCase):
    def setUp(self):self.h=DirectHarness(self)

    def failed_read_acquire(self):
        h=self.h;h.release_fail=True
        h.controller.official_cli.idle_check.side_effect=[None,ValueError('Synthetic competing session after reservation')]
        with self.assertRaises(FirmwareReservationHeld):h.service.start_check(SN)
        state=h.service.snapshot()
        self.assertEqual(state['phase'],'unknown');self.assertTrue(state['locked'])
        self.assertEqual(state['device']['sn'],SN);self.assertEqual(h.marker['kind'],'firmware_check')
        self.assertEqual(h.resource_calls,[]);self.assertEqual(h.spawn_calls,[])
        return h.marker['token']

    def unknown_writer(self,*,read_error=True):
        h=self.h;ready=h.prepared();original=h.host.spawn
        def spawn(*args,**kw):
            process=original(*args,**kw)
            if read_error:process.read_error=RuntimeError('Synthetic PTY output failure after writer exit')
            else:process.status=None
            return process
        h.host.spawn=spawn
        state=h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'unknown');self.assertTrue(state['locked'])
        self.assertIsNotNone(h.host.process)
        return h.host.process

    def test_never_started_read_recovers_exact_owner_without_reading_device(self):
        h=self.h;owner=self.failed_read_acquire();h.release_fail=False
        recovered=h.service.verify_recovery()
        self.assertEqual(recovered['phase'],'checked');self.assertEqual(recovered['result']['verdict'],'readonly_query_not_started')
        self.assertFalse(recovered['locked']);self.assertFalse(recovered['can_upgrade'])
        self.assertEqual(h.marker,{});self.assertIsNone(h.host._read_not_started)
        self.assertEqual(h.resource_calls,[]);self.assertEqual(h.spawn_calls,[])
        self.assertIn(owner,h.release_calls)

    def test_never_started_read_release_failure_remains_retryable(self):
        h=self.h;owner=self.failed_read_acquire()
        first=h.service.verify_recovery();self.assertEqual(first['phase'],'unknown');self.assertTrue(first['locked'])
        self.assertEqual(h.host._read_not_started,(owner,SN))
        h.release_fail=False;second=h.service.verify_recovery()
        self.assertEqual(second['phase'],'checked');self.assertEqual(h.marker,{})
        self.assertEqual(h.resource_calls,[]);self.assertEqual(h.spawn_calls,[])

    def test_never_started_proof_cannot_clear_wrong_token_serial_or_marker_kind(self):
        h=self.h;owner=self.failed_read_acquire();original=copy.deepcopy(h.marker)
        for serial,token,changes in [(OTHER,owner,{}),(SN,'old-token',{}),(SN,owner,{'kind':'firmware'}),(SN,owner,{'serial':OTHER})]:
            with self.subTest(serial=serial,token=token,changes=changes):
                h.marker=dict(original,**changes)
                with self.assertRaises(ValueError):h.host.recovery_check(serial,reservation_token=token,read_only=True)
                self.assertEqual(h.resource_calls,[]);self.assertEqual(h.spawn_calls,[])
        h.marker=original;h.release_fail=False
        self.assertEqual(h.service.verify_recovery()['phase'],'checked')

    def test_retained_query_or_writer_reference_blocks_never_started_shortcut(self):
        h=self.h;self.failed_read_acquire()
        for field in ('process','query_process','query_job','query_cancel'):
            with self.subTest(field=field):
                setattr(h.host,field,object())
                try:
                    with self.assertRaises(ValueError):h.service.verify_recovery()
                    self.assertTrue(h.service.snapshot()['locked']);self.assertEqual(h.resource_calls,[])
                finally:setattr(h.host,field,None)
        h.release_fail=False;self.assertEqual(h.service.verify_recovery()['phase'],'checked')

    def test_starting_any_query_invalidates_the_never_started_proof(self):
        h=self.h;owner=self.failed_read_acquire();h.release_fail=False
        h.host.runner=None
        with patch.object(h.host,'_spawn_background',side_effect=RuntimeError('Synthetic unknown child startup')):
            with self.assertRaises(ReadOnlyQueryUnconfirmed):h.host(['upgrade','--sn',SN,'--check','--json'])
        self.assertIsNone(h.host._read_not_started);self.assertFalse(h.host.query_exit_confirmed)
        with self.assertRaises(ValueError):h.host.recovery_check(SN,reservation_token=owner,read_only=True)
        self.assertEqual(h.marker['token'],owner)

    def test_invalid_read_recovery_resources_cannot_release_a_reservation(self):
        h=self.h;owner=self.failed_read_acquire();h.release_fail=False;original=h.service.recovery_check
        valid=dict(read_only=True,process_exited=True,sn=SN,reservation_token=owner,no_read_started=True)
        for changes in ({'read_only':1},{'process_exited':False},{'sn':OTHER},{'reservation_token':'old-token'}):
            with self.subTest(changes=changes):
                h.service.recovery_check=Mock(return_value=dict(valid,**changes))
                with self.assertRaises(ValueError):h.service.verify_recovery()
                self.assertTrue(h.service.snapshot()['locked']);self.assertEqual(h.marker['token'],owner)
        h.service.recovery_check=original;self.assertEqual(h.service.verify_recovery()['phase'],'checked')

    def test_legacy_missing_identity_is_a_guarded_error_without_releasing(self):
        h=self.h;owner=self.failed_read_acquire();h.release_fail=False
        device=copy.deepcopy(h.service.state['device']);h.service.state['device']=None
        with self.assertRaisesRegex(ValueError,'Original device identity'):h.service.verify_recovery()
        self.assertEqual(h.marker['token'],owner);self.assertTrue(h.service.snapshot()['locked'])
        h.service.state['device']=device;self.assertEqual(h.service.verify_recovery()['phase'],'checked')

    def test_next_file_task_runs_after_never_started_read_recovery(self):
        h=self.h;self.failed_read_acquire();h.release_fail=False;h.service.verify_recovery()
        h.controller.official_cli.idle_check.side_effect=None
        ready=h.prepared();state=h.install(ready['package']['token'])
        self.assertEqual(state['phase'],'verified');self.assertEqual(len(h.spawn_calls),1);self.assertEqual(len(h.resource_calls),2)

    def test_dead_unknown_writer_recovery_retires_transport_then_next_task_runs(self):
        h=self.h;process=self.unknown_writer();old_id=h.host.run_snapshot()['id']
        recovered=h.service.verify_recovery();self.assertEqual(recovered['phase'],'verified')
        self.assertIsNone(h.host.process);self.assertFalse(h.host.run_snapshot()['running'])
        self.assertEqual(h.host.run_snapshot()['status'],'recovered');self.assertFalse(process.closed)
        h.host.spawn=h.spawn;ready=h.prepared();second=h.install(ready['package']['token'])
        self.assertEqual(second['phase'],'verified');self.assertEqual(len(h.spawn_calls),2);self.assertEqual(len(h.resource_calls),4)
        self.assertEqual(h.marker,{});self.assertEqual(h.catalog._claims,{})
        h.host._append('STALE OLD OUTPUT',old_id)
        self.assertNotIn('STALE OLD OUTPUT',''.join(row['text'] for row in h.host.chunks))

    def test_live_or_unconfirmed_original_writer_is_never_retired(self):
        h=self.h;process=self.unknown_writer();owner=h.marker['token']
        process.alive=True
        with self.assertRaises(ValueError):h.service.verify_recovery()
        self.assertIs(h.host.process,process);self.assertEqual(h.resource_calls,[])
        process.alive=False
        for status in ('live','unknown'):
            with self.subTest(status=status),patch('firmware_host.process_state',return_value=status):
                with self.assertRaises(ValueError):h.service.verify_recovery()
                self.assertIs(h.host.process,process);self.assertEqual(h.marker['token'],owner)
                self.assertEqual(h.resource_calls,[])
        self.assertEqual(h.service.verify_recovery()['phase'],'verified');self.assertIsNone(h.host.process)

    def test_pending_output_reader_must_finish_before_transport_is_retired(self):
        h=self.h;process=self.unknown_writer();owner=h.marker['token'];done=threading.Event();h.host._reader_done=done
        with self.assertRaises(ValueError):h.service.verify_recovery()
        self.assertIs(h.host.process,process);self.assertEqual(h.marker['token'],owner)
        self.assertTrue(h.service.snapshot()['locked']);process.pty.cancel_io.assert_called_once()
        done.set();self.assertEqual(h.service.verify_recovery()['phase'],'verified');self.assertIsNone(h.host.process)

    def test_wrong_readback_or_transport_owner_keeps_dead_writer_for_retry(self):
        h=self.h;process=self.unknown_writer();owner=h.marker['token']
        h.reported_sn=OTHER
        with self.assertRaises(ValueError):h.service.verify_recovery()
        self.assertIs(h.host.process,process);self.assertEqual(h.marker['token'],owner)
        h.reported_sn=SN;h.host.session_token='another-owner'
        with self.assertRaises(ValueError):h.service.verify_recovery()
        self.assertIs(h.host.process,process);self.assertTrue(h.service.snapshot()['locked'])
        h.host.session_token=owner;self.assertEqual(h.service.verify_recovery()['phase'],'verified')
        self.assertIsNone(h.host.process)

    def test_writer_PID_mismatch_cannot_retire_or_clear_the_exact_reservation(self):
        h=self.h;process=self.unknown_writer();pid=h.marker['child_pid'];owner=h.marker['token']
        h.marker['child_pid']=pid+1
        with self.assertRaises(ValueError):h.service.verify_recovery()
        self.assertIs(h.host.process,process);self.assertEqual(h.marker['token'],owner)
        h.marker['child_pid']=pid;self.assertEqual(h.service.verify_recovery()['phase'],'verified')

    def test_legacy_writer_recovery_also_retires_only_proven_dead_transport(self):
        h=self.h;h.host.device(SN);owner=h.host.acquire('firmware');original=h.host.spawn
        def spawn(*args,**kw):
            process=original(*args,**kw);process.read_error=RuntimeError('Synthetic legacy output failure');return process
        h.host.spawn=spawn;result=h.host(['upgrade','--sn',SN],mutating=True,interactive=True)
        self.assertFalse(result['exited']);self.assertIsNotNone(h.host.process)
        report={'devices':[dict(sn=SN,device_type='wuji_glove',current_version='1.0.4',latest_version='1.0.4',status='up to date',error=None)]}
        h.host.runner=Mock(return_value=N(returncode=0,stdout=json.dumps(report),stderr=''))
        recovered=h.host.recovery_check(SN,reservation_token=owner)
        self.assertTrue(recovered['identity_verified']);self.assertIsNone(h.host.process)
        self.assertEqual(h.host.run_snapshot()['status'],'recovered');h.host.release(owner);self.assertEqual(h.marker,{})


if __name__=='__main__':unittest.main()
