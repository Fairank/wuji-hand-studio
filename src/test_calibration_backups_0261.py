"""Official archive preservation and explicit native recovery, all isolated."""
from contextlib import contextmanager,redirect_stdout
import copy,hashlib,io,json,os
from pathlib import Path
import socket,subprocess,tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import patch,Mock
import zipfile

import calibration_backups as backups
import native_calibration as native
import cli_coordination as coordination
import test_calibration_guided as fixture

BASE=Path(__file__).resolve().parents[2]/'update-flow-0261/calibration-audit/test-temp'

@contextmanager
def temporary_root():
    BASE.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BASE) as name:yield Path(name)

def forbidden(*a,**kw):raise AssertionError('No real network or vendor process')

class BackupTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with patch.object(fixture,'temporary_root',temporary_root),fixture.native_environment() as e:
            data={'left_hand.urdf':b'<robot name="left-original"/>','right_hand.urdf':b'<robot name="right-original"/>'}
            profile=fixture.profile(calibrated=True);profile['right_hand']['calibrated']=True
            def command(*args):
                if args==('user','show'):return copy.deepcopy(profile)
                if args[:2]==('user','export'):
                    with zipfile.ZipFile(args[2],'w') as archive:
                        for name,value in data.items():archive.writestr(name,value)
                    return {'exported':True}
                raise AssertionError('Unexpected metadata command '+repr(args))
            e.query.side_effect=command
            e.data=data;e.profile=profile;e.store=backups.CalibrationBackups();e.command=command
            with patch.object(socket,'socket',forbidden),patch.object(socket,'create_connection',forbidden),patch.object(subprocess,'Popen',forbidden):yield e

    def create(self,e):
        return e.store.create(fixture.USER,'left',fixture.SN,copy.deepcopy(fixture.SDK_USER),'a'*32,command=e.command)

    def test_export_is_original_complete_archive_and_remains_after_new_models_change(self):
        with self.environment() as e:
            result=self.create(e);record,path=e.store.load(result['id'],result['sha256'])
            original=path.read_bytes();e.data['left_hand.urdf']=b'<robot name="new-left"/>'
            self.assertEqual(path.read_bytes(),original);self.assertEqual(set(record['members']),set(e.data))
            self.assertFalse(e.store.verify_restored(result['id'],command=e.command))
            e.data['left_hand.urdf']=b'<robot name="left-original"/>'
            self.assertTrue(e.store.verify_restored(result['id'],command=e.command))

    def test_wrong_owner_rejects_before_export_and_other_side_stays_in_original_zip(self):
        with self.environment() as e:
            with patch('calibration_identity.read_current_sdk_user',return_value={'user_id':'another-owner','display_name':fixture.USER}):
                with self.assertRaisesRegex(ValueError,'identity changed'):self.create(e)
            self.assertEqual(list(e.store.folder.glob('*.zip')),[])
            saved=self.create(e);_,path=e.store.load(saved['id']);self.assertEqual(zipfile.ZipFile(path).read('right_hand.urdf'),e.data['right_hand.urdf'])

    def test_corrupt_crc_and_change_during_verification_are_unverified(self):
        with self.environment() as e:
            saved=self.create(e)
            with patch.object(backups,'_archive_signature',side_effect=zipfile.BadZipFile('synthetic CRC')):
                with self.assertRaisesRegex(ValueError,'CRC'):e.store.load(saved['id'])
            actual=backups.archive_signature
            def mutate(path):
                result=actual(path)
                with Path(path).open('ab') as stream:stream.write(b'changed-after-member-read')
                return result
            with patch.object(backups,'archive_signature',side_effect=mutate),self.assertRaisesRegex(ValueError,'changed during verification'):e.store.load(saved['id'])

    def test_failed_export_stops_before_any_native_process(self):
        with self.environment() as e:
            e.query.side_effect=lambda *args:copy.deepcopy(e.profile)
            with self.assertRaisesRegex(ValueError,'previous profile'):e.service.start_guided(fixture.SN,'left',fixture.USER,True)
            e.spawn.assert_not_called();self.assertIsNone(e.service.token)

    def test_backup_is_visible_if_cancel_arrives_after_export_before_native_launch(self):
        with self.environment() as e:
            def cancelled():
                if list(e.store.folder.glob('*.zip')):raise ValueError('synthetic start cancelled')
            with self.assertRaisesRegex(ValueError,'start cancelled'):e.service.start_guided(fixture.SN,'left',fixture.USER,True,cancel_check=cancelled)
            e.spawn.assert_not_called();self.assertFalse(e.owner.run['running']);self.assertTrue(e.owner.run['cleanup_confirmed'])
            saved=e.owner.run['backup'];self.assertTrue(e.store.load(saved['id'])[0]['verified'])
            self.assertEqual(e.owner.run['status'],'cancelled');self.assertIsNone(e.owner.run['error'])
            self.assertIn('start cancelled',e.owner.run['cancel_reason'])
            native.NativeCalibration(e.owner).start_guided(fixture.SN,'left',fixture.USER,True)
            self.assertTrue(e.owner.run['running']);self.assertEqual(e.spawn.call_count,1)

    def test_thread_constructor_and_start_failure_close_exact_job_and_retain_backup(self):
        for stage in ('constructor','start'):
            with self.subTest(stage=stage),self.environment() as e:
                thread=Mock(side_effect=RuntimeError('synthetic thread constructor')) if stage=='constructor' else Mock(return_value=N(start=Mock(side_effect=RuntimeError('synthetic thread start'))))
                with patch.object(native.threading,'Thread',thread),self.assertRaisesRegex(RuntimeError,'synthetic thread'):
                    e.service.start_guided(fixture.SN,'left',fixture.USER,True)
                self.assertFalse(e.owner.run['running']);self.assertTrue(e.owner.run['cleanup_confirmed']);self.assertIsNone(e.service.token)
                self.assertTrue(e.store.load(e.owner.run['backup']['id'])[0]['verified']);e.job.close.assert_called_once()
                next_service=native.NativeCalibration(e.owner);next_service.start_guided(fixture.SN,'left',fixture.USER,True)
                self.assertTrue(e.owner.run['running'])

    def test_constructor_unknown_job_and_release_error_remain_until_explicit_exact_retry(self):
        with self.environment() as e:
            e.spawn.side_effect=RuntimeError('synthetic constructor uncertain')
            with self.assertRaisesRegex(RuntimeError,'constructor uncertain'):e.service.start_guided(fixture.SN,'left',fixture.USER,True)
            token=e.service.token;backup=copy.deepcopy(e.owner.run['backup']);self.assertTrue(e.owner.run['running'])
            with patch('windows_process_job.recover_viz_job',return_value=False) as recover,self.assertRaisesRegex(ValueError,'cleanup remains unconfirmed'):e.service.cancel()
            recover.assert_called_once_with('Local\\HandWorkbench-OfficialCalibration-'+token,timeout=7)
            self.assertEqual(e.service.token,token)
            with patch('windows_process_job.recover_viz_job',return_value=True),patch.object(native,'release',side_effect=PermissionError('synthetic retained lease')):
                with self.assertRaises(PermissionError):e.service.cancel()
            self.assertEqual(e.service.token,token)
            with patch('windows_process_job.recover_viz_job',return_value=True):e.service.cancel()
            self.assertIsNone(e.service.token);self.assertFalse(e.owner.run['running']);self.assertTrue(e.owner.run['cleanup_confirmed'])
            self.assertEqual(e.owner.run['backup'],backup);self.assertTrue(e.store.load(backup['id'])[0]['verified'])
            e.spawn.side_effect=None;native.NativeCalibration(e.owner).start_guided(fixture.SN,'left',fixture.USER,True)
            self.assertTrue(e.owner.run['running'])

    def test_explicit_cleanup_never_releases_a_replacement_owner(self):
        with self.environment() as e:
            e.spawn.side_effect=RuntimeError('synthetic constructor uncertain')
            with self.assertRaises(RuntimeError):e.service.start_guided(fixture.SN,'left',fixture.USER,True)
            marker=coordination.reservation(e.folder);marker['token']='f'*32;coordination.atomic(e.folder/'session.json',marker)
            with patch('windows_process_job.recover_viz_job') as recover,self.assertRaisesRegex(ValueError,'owner changed'):e.service.cancel()
            recover.assert_not_called();self.assertEqual(coordination.reservation(e.folder)['token'],'f'*32)

    def test_restore_marker_cannot_be_replayed_as_calibration_and_restore_never_queries_device(self):
        with self.environment() as e:
            saved=self.create(e);e.service.start_restore(saved['id'],saved['sha256']);token=e.service.token
            prefix=[token,fixture.SN,'left','900']
            with patch('windows_process_job.current_in_named_job',return_value=True),patch('native_terminal_worker.run',return_value=0) as run,redirect_stdout(io.StringIO()):
                self.assertEqual(native.worker_main(prefix+['restore',fixture.USER,saved['id']]),0)
                argv=run.call_args.args[0];self.assertEqual(argv[1:3],['user','import']);self.assertNotIn('--yes',argv);self.assertNotIn('--sn',argv)
                run.reset_mock();self.assertEqual(native.worker_main(prefix),1);self.assertEqual(native.worker_main(prefix+['guided',fixture.USER,'keep']),1);run.assert_not_called()
            self.assertFalse(any(c.args and c.args[0]=='get' for c in e.query.call_args_list))

    def test_restore_cleanup_is_required_by_actual_controller_and_desktop_exit(self):
        from console_server import Controller
        from desktop_lifecycle import calibration_cleanup_required,needs_confirmation,close_sessions
        from calibration_portability import calibration_proof
        with self.environment() as e:
            with patch('managed_runtime.status',return_value={'busy':False}):
                controller=Controller(reports=e.folder/'controller-reports')
                controller.official_cli.snapshot=Mock(return_value={})
                controller.calibration.run.update(id='e'*32,serial=fixture.SN,user=fixture.USER,side='left',
                    source='official_user_import_tty',interface='restore',running=False,status='error',cleanup_confirmed=False)
                state=controller.snapshot()
                self.assertTrue(state['calibration']['cleanup_required'])
                self.assertTrue(controller._continuation_calibration()['cleanup_required'])
                self.assertTrue(calibration_cleanup_required(state));self.assertTrue(needs_confirmation(state))
                controller.action=Mock(return_value={})
                result=close_sessions(controller,sleep=lambda _:None)
                self.assertFalse(result['ok'])
                self.assertEqual([call.args[0]['name'] for call in controller.action.call_args_list],['calibration_cancel'])
                with self.assertRaises(ValueError):calibration_proof(controller._continuation_calibration())
                controller.calibration.run['cleanup_confirmed']=True
                self.assertFalse(controller.snapshot()['calibration']['cleanup_required'])
                self.assertFalse(controller._continuation_calibration()['cleanup_required'])

    def test_collector_reader_start_failure_still_cleans_owned_job_and_retains_backup(self):
        for stage,clean in [('constructor',True),('start',False)]:
            with self.subTest(stage=stage),self.environment() as e:
                e.service.start_guided(fixture.SN,'left',fixture.USER,True)
                saved=copy.deepcopy(e.owner.run['backup']);e.job.close.return_value=clean
                thread=Mock(side_effect=RuntimeError('synthetic reader constructor')) if stage=='constructor' else Mock(return_value=N(start=Mock(side_effect=RuntimeError('synthetic reader start'))))
                with patch.object(native.threading,'Thread',thread):e.service._collect()
                self.assertEqual(e.owner.run['status'],'error');self.assertEqual(e.owner.run['cleanup_confirmed'],clean)
                self.assertEqual(e.owner.run['running'],not clean)
                self.assertIn('synthetic reader',e.owner.run['error']);self.assertTrue(e.store.load(saved['id'])[0]['verified'])
                e.job.close.assert_called_once()
                if not clean:
                    e.job.close.return_value=True;e.owner.thread=N(is_alive=lambda:False)
                    e.service.cancel()
                    self.assertTrue(e.owner.run['cleanup_confirmed']);self.assertFalse(e.owner.run['running'])
                native.NativeCalibration(e.owner).start_guided(fixture.SN,'left',fixture.USER,True)
                self.assertTrue(e.owner.run['running'])

    def test_actual_download_handler_returns_original_zip_and_rejects_crc_or_changed_archive(self):
        from console_server import Handler
        with self.environment() as e:
            saved=self.create(e);_,path=e.store.load(saved['id'])
            handler=object.__new__(Handler);handler.allowed_host=Mock(return_value=True);handler.reply=Mock()
            handler.path='/api/calibration-backup?backup_id='+saved['id']
            handler.server=N()
            handler.do_GET()
            self.assertEqual(handler.reply.call_args.args[:2],(200,path.read_bytes()))
            self.assertEqual(handler.reply.call_args.kwargs['mime'],'application/zip')
            handler.reply.reset_mock()
            with patch.object(backups,'_archive_signature',side_effect=zipfile.BadZipFile('synthetic CRC')):handler.do_GET()
            self.assertEqual(handler.reply.call_args.args[0],404)
            handler.reply.reset_mock();path.write_bytes(b'synthetic archive changed')
            handler.do_GET();self.assertEqual(handler.reply.call_args.args[0],404)

    def test_invalid_home_is_pre_spawn_failure_with_backup_and_confirmed_release_then_retry(self):
        with self.environment() as e:
            with patch.dict(os.environ,HOME='relative-sdk-home'),self.assertRaisesRegex(ValueError,'absolute path'):
                e.service.start_guided(fixture.SN,'left',fixture.USER,True)
            e.spawn.assert_not_called();self.assertIsNone(e.service.token)
            self.assertEqual(coordination.reservation(e.folder),{})
            self.assertFalse(e.owner.run['running']);self.assertTrue(e.owner.run['cleanup_confirmed'])
            self.assertTrue(e.store.load(e.owner.run['backup']['id'])[0]['verified'])
            native.NativeCalibration(e.owner).start_guided(fixture.SN,'left',fixture.USER,True)
            self.assertTrue(e.owner.run['running']);self.assertEqual(e.spawn.call_count,1)

if __name__=='__main__':unittest.main()
