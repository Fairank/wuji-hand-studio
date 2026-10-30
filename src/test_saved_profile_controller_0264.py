"""Saved official profile callback ownership; vendor export is the boundary."""
from contextlib import contextmanager
import copy,hashlib,threading
from pathlib import Path
import tempfile
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import Controller
from device_profiles import profile
from retarget_workspace import RetargetWorkspace
from retarget_tuning import TuningStore


class SavedControllerTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'original-official.zip';archive.write_bytes(b'synthetic exact original archive')
            proof=dict(source_kind='saved_profile',source_run_id=None,transfer_id='a'*32,source_workspace='main',
                serial='WG_SYNTHETIC_SAVED',side='right',source_profile='Alice',source_user_id='sdk-owner-Alice',
                model_sha256='b'*64,model_bytes=42,bundle_path=str(archive),
                bundle_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),bundle_bytes=archive.stat().st_size,expected_revision='c'*64)
            switch=N(internal=True,lock=threading.RLock(),state=dict(busy=True,phase='connecting',attempt_id='d'*32),cancelled=False)
            switch._config=Mock(return_value='e'*64)
            def check():
                if switch.cancelled:raise ValueError('Synthetic exact switch cancelled')
            switch._check_cancel=check
            c=Controller.__new__(Controller)
            c.__dict__.update(dict(lock=threading.RLock(),connection_switch=switch,desktop_closing=False,reports=root/'reports',
                state=dict(device_profile=profile('hand2_right'),view_source='feedback'),sdk_session_configs={},
                retarget=RetargetWorkspace(root/'workspace')))
            c.tuning=TuningStore(c.retarget.folder/'official_tuning')
            c.calibration_devices=Mock(return_value=[])
            bridge=N(lock=threading.RLock(),session=10,stopping=False,_continuation=None)
            callbacks=[]
            def start(bundle,callback,expected_profile=None,open_guard=None):
                bridge.session+=1;bridge._continuation=copy.deepcopy(bundle);callbacks.append(callback);return bridge.session
            bridge.start_calibrated=Mock(side_effect=start);c.glove=bridge
            with patch.dict('os.environ',{'WUJI_FLEET_ID':''}):
                yield N(c=c,proof=proof,switch=switch,bridge=bridge,callbacks=callbacks)

    def start(self,e):
        self.assertTrue(callable(getattr(Controller,'_start_saved_profile_session',None)),
                        'Existing official profiles need their own private start, without requiring a new calibration run')
        return Controller._start_saved_profile_session(e.c,e.proof,e.proof,expected_profile='hand2_right')

    def receipt(self,e):
        p=e.proof
        return dict({k:p[k] for k in ('source_run_id','source_workspace','source_profile','serial','side','model_sha256','source_kind','transfer_id')},
                    owner_user_id=p['source_user_id'],imported=True)

    def test_saved_profile_starts_exact_session_and_binds_model_without_new_calibration_run(self):
        with self.environment() as e,patch('saved_calibration.verify_saved_snapshot',return_value=True):
            session=self.start(e)
            settings=e.callbacks[0](self.receipt(e))
            binding=e.c.retarget.selection(e.c.state['device_profile'])
            self.assertEqual(session,11);self.assertEqual(e.c.sdk_session_configs[session],'e'*64)
            self.assertEqual(binding['sdk_user'],'sdk-owner-Alice');self.assertEqual(binding['glove_serial'],'WG_SYNTHETIC_SAVED')
            self.assertEqual(settings['solver']['engine'],'sdk');self.assertEqual(e.c.state['view_source'],'glove')
            self.assertFalse(hasattr(e.c,'calibration'));e.bridge.start_calibrated.assert_called_once()

    def test_retired_attempt_wrong_receipt_and_changed_profile_never_bind_new_user(self):
        for changed in ('attempt','receipt','profile','config','session'):
            with self.subTest(changed=changed),self.environment() as e,patch('saved_calibration.verify_saved_snapshot',return_value=True):
                self.start(e);receipt=self.receipt(e);before=e.c.retarget.selection(e.c.state['device_profile'])
                if changed=='attempt':e.switch.state['attempt_id']='f'*32
                if changed=='receipt':receipt['owner_user_id']='different-sdk-owner'
                if changed=='profile':e.c.state['device_profile']=profile('hand2_left')
                if changed=='config':e.switch._config.return_value='f'*64
                if changed=='session':e.bridge.session+=1
                with self.assertRaises(ValueError):e.callbacks[0](receipt)
                self.assertEqual(e.c.state['view_source'],'feedback')
                self.assertEqual(e.c.retarget.selection(profile('hand2_right')),before)

    def test_observed_native_route_is_carried_to_sdk_without_controller_broadcast_dependency(self):
        with self.environment() as e,patch('saved_calibration.verify_saved_snapshot',return_value=True),patch('official_device_discovery.use_native',return_value=True):
            e.c.calibration_devices.return_value=[dict(sn=e.proof['serial'],device_type='wuji_glove',handedness='right',address='udp/192.0.2.44:50001')]
            self.start(e)
            wire=e.bridge.start_calibrated.call_args.args[0]
            self.assertEqual(wire.get('glove_route'),dict(serial=e.proof['serial'],address='192.0.2.44:50001',source='official_discovery'))
            e.c.calibration_devices.assert_called_once_with(fresh=True)

    def test_saved_profile_passes_exact_attempt_guard_to_final_bridge_dispatch(self):
        with self.environment() as e:
            @contextmanager
            def permitted():yield
            e.switch.open_guard=Mock(side_effect=lambda attempt:permitted())
            self.start(e)
            guard=e.bridge.start_calibrated.call_args.kwargs.get('open_guard')
            self.assertTrue(callable(guard))
            with guard():pass
            e.switch.open_guard.assert_called_once_with('d'*32)

    def test_saved_profile_keeps_requested_timeout_and_rejects_invalid_before_start(self):
        with self.environment() as e,patch('saved_calibration.verify_saved_snapshot',return_value=True):
            Controller._start_saved_profile_session(e.c,e.proof,e.proof,
                expected_profile='hand2_right',timeout_ms=650)
            self.assertEqual(e.callbacks[0](self.receipt(e))['timeout_ms'],650)
        for timeout in (True,99,2001,650.0,'650',None):
            with self.subTest(timeout=timeout),self.environment() as e:
                with self.assertRaises(ValueError):
                    Controller._start_saved_profile_session(e.c,e.proof,e.proof,
                        expected_profile='hand2_right',timeout_ms=timeout)
                e.bridge.start_calibrated.assert_not_called()

    def test_cancel_during_saved_source_readback_cannot_commit_mapping(self):
        with self.environment() as e:
            def readback(*args,**kwargs):e.switch.cancelled=True;return True
            with patch('saved_calibration.verify_saved_snapshot',side_effect=readback):
                self.start(e)
                with self.assertRaisesRegex(ValueError,'cancelled'):e.callbacks[0](self.receipt(e))
            self.assertEqual(e.c.state['view_source'],'feedback')
            self.assertEqual(e.c.retarget.selection(e.c.state['device_profile'])['sdk_user'],'')

    def test_start_outside_current_switch_or_wrong_workspace_has_zero_bridge_starts(self):
        for changed in ('outside','workspace','side'):
            with self.subTest(changed=changed),self.environment() as e:
                if changed=='outside':e.switch.internal=False
                if changed=='workspace':e.proof['source_workspace']='other-workspace'
                if changed=='side':e.proof['side']='left'
                with self.assertRaises(ValueError):self.start(e)
                e.bridge.start_calibrated.assert_not_called()


if __name__=='__main__':unittest.main()
