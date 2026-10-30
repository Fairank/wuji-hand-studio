"""Profile state locks, partial mutation and late metadata: all vendor I/O fake."""
from contextlib import contextmanager
import copy,os,threading,time
from pathlib import Path
import unittest
from unittest.mock import patch
import test_calibration_backups_0261 as old
from calibration_cli import CalibrationCLI
from console_server import Controller
import cli_coordination

BASE=Path(__file__).resolve().parents[2]/'update-flow-0263/calibration-audit/test-temp'
REAL_THREAD=threading.Thread

def row(name):return dict(name=name,is_default=False,left_hand={'calibrated':False},right_hand={'calibrated':False})

class ProfileStateTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with patch.object(old,'BASE',BASE),old.BackupTests().environment() as e,patch('calibration_cli.load_config',return_value={'mode':'local'}),patch('official_device_discovery.use_native',return_value=True):
            e.selected='Alice';e.users=['Alice','Bob'];e.partial=False;e.hook=lambda *args:None
            def cli(cfg,*args):
                if args==('user','list'):return {'users':[row(n) for n in e.users]}
                if args==('user','show'):
                    captured=row(e.selected);e.hook(*args);return captured
                if args[:2]==('user','show'):return row(args[2])
                if args[:2] in [('user','create'),('user','switch')]:
                    if args[1]=='create':e.users.append(args[2])
                    e.selected=args[2]
                    if e.partial:e.partial=False;raise ValueError('synthetic reply lost after user files were changed')
                    return row(e.selected)
                raise AssertionError(args)
            e.service=CalibrationCLI(device_provider=lambda fresh:[])
            with patch('calibration_cli.cli_json',side_effect=cli):yield e

    def test_actual_controller_metadata_callback_never_holds_profile_run_lock(self):
        for operation in ('switch','create'):
            with self.subTest(operation=operation),self.environment() as e:
                c=Controller(factory=old.forbidden,reports=e.folder/'reports');c.official_cli.snapshot=lambda:{}
                c.calibration=e.service;entered=threading.Event();done=[]
                def metadata():entered.set();c.snapshot()
                e.service.metadata_check=metadata;e.service.profile_read_check=metadata
                def run():
                    try:done.append(e.service.profile(operation,'Bob' if operation=='switch' else 'Carol'))
                    except BaseException as error:done.append(error)
                with c.lock:
                    worker=REAL_THREAD(target=run);worker.start();self.assertTrue(entered.wait(2))
                    acquired=e.service.lock.acquire(timeout=.3)
                    if acquired:e.service.lock.release()
                    self.assertTrue(acquired,'State reader must not enter a controller/run lock cycle')
                worker.join(4);self.assertFalse(worker.is_alive());self.assertIsInstance(done[0],dict)

    def test_partial_create_and_switch_failure_invalidate_old_selected_user(self):
        for operation,name in [('switch','Bob'),('create','Carol')]:
            with self.subTest(operation=operation),self.environment() as e:
                self.assertEqual(e.service.snapshot(refresh=True)['current']['name'],'Alice')
                e.partial=True
                with self.assertRaisesRegex(ValueError,'files were changed'):e.service.profile(operation,name)
                after=e.service.snapshot(refresh=False)
                self.assertTrue(after['metadata_current']);self.assertEqual(after['current']['name'],name)
                self.assertIn(name,[u['name'] for u in after['users']])
                self.assertEqual(e.service.profile('switch',name)['current']['name'],name)
                e.spawn.assert_not_called()

    def test_old_inflight_metadata_cannot_repopulate_cache_after_profile_change(self):
        with self.environment() as e:
            entered=threading.Event();leave=threading.Event();results=[]
            def hook(*args):
                if threading.current_thread().name=='old-read':entered.set();self.assertTrue(leave.wait(4))
            e.hook=hook
            worker=REAL_THREAD(target=lambda:results.append(e.service.snapshot(refresh=True)),name='old-read');worker.start()
            try:
                self.assertTrue(entered.wait(2));e.service.profile('switch','Bob')
            finally:leave.set();worker.join(5)
            self.assertFalse(worker.is_alive());self.assertFalse(results[0]['metadata_current']);self.assertFalse(results[0]['available'])
            current=e.service.snapshot(refresh=False);self.assertTrue(current['metadata_current']);self.assertEqual(current['current']['name'],'Bob')

    def test_cached_response_waiting_on_inventory_is_unverified_after_user_change(self):
        with self.environment() as e:
            e.service.snapshot(refresh=True);entered=threading.Event();leave=threading.Event();results=[]
            def inventory(fresh):
                if threading.current_thread().name=='old-cache':entered.set();self.assertTrue(leave.wait(4))
                return []
            e.service.device_provider=inventory
            worker=REAL_THREAD(target=lambda:results.append(e.service.snapshot(refresh=False)),name='old-cache');worker.start()
            try:
                self.assertTrue(entered.wait(2));e.service.profile('switch','Bob')
            finally:leave.set();worker.join(5)
            self.assertFalse(worker.is_alive());self.assertFalse(results[0]['metadata_current'])
            self.assertEqual(e.service.snapshot()['current']['name'],'Bob')

    def test_begin_partial_create_failure_reloads_new_user_without_starting_native(self):
        with self.environment() as e:
            e.service.snapshot(refresh=True);e.partial=True
            receipt=dict(source='explicit_calibration',serial=old.fixture.SN,side='left',owner_pid=os.getpid(),owner_start=cli_coordination.identity(os.getpid()),checked_at=time.time())
            with self.assertRaisesRegex(ValueError,'files were changed'):
                e.service.start('left',old.fixture.SN,False,profile='Carol',create_profile=True,connected=receipt)
            e.spawn.assert_not_called();after=e.service.snapshot(refresh=False)
            self.assertTrue(after['metadata_current']);self.assertEqual(after['current']['name'],'Carol')
            self.assertEqual([u['name'] for u in after['users']].count('Carol'),1)

if __name__=='__main__':unittest.main()
