"""Saved-profile readiness keeps its exact switch through final local writes."""
import copy
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from connection_switch import ConnectionSwitch
from console_server import Controller


class SavedProfileReadyScopeTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,WUJI_FLEET_ID='main'),\
                patch('bridge_config.load_config',return_value={'mode':'ssh'}),\
                patch('official_device_discovery.use_native',return_value=False),\
                patch('saved_calibration.verify_saved_snapshot',return_value=True):
            controller=Controller.__new__(Controller)
            controller.lock=threading.RLock();controller.sdk_session_configs={}
            controller.calibration_handover=N(workflow_lock=threading.RLock())
            controller.state=dict(connection='disconnected',hardware=dict(active=False),view_source='feedback',
                device_profile=dict(id='hand2_right',generation='hand2',side='right'))
            controller.snapshot=lambda:copy.deepcopy(controller.state)
            switch=ConnectionSwitch(controller);controller.connection_switch=switch
            config=['original-controller'];switch._config=lambda:config[0]
            proof=dict(source_kind='saved_profile',transfer_id='c'*32,source_run_id=None,source_workspace='main',
                serial='WG-SYNTHETIC',side='right',source_profile='Alice',source_user_id='owner-1',
                model_sha256='d'*64,model_bytes=1,bundle_sha256='e'*64,bundle_bytes=1,
                bundle_path=str(Path(folder)/'synthetic.zip'))
            callbacks=[]
            glove=N(lock=threading.RLock(),session=0,stopping=False,_continuation=None)
            glove.snapshot=lambda:dict(session_id=glove.session,busy=True,
                session_cleanup=dict(session_id=glove.session,cleanup_confirmed=False))
            def start(bundle,ready,**kwargs):
                glove.session+=1;glove._continuation=copy.deepcopy(bundle)
                callbacks.append(ready);return glove.session
            glove.start_calibrated=Mock(side_effect=start);controller.glove=glove
            binding=dict(generation='hand2',side='right',glove_serial='PREVIOUS-GLOVE',sdk_user='previous-owner',hand_serial='')
            retarget=N(context=Mock(return_value=dict(binding=binding)),
                select=Mock(side_effect=lambda value,profile:dict(binding=value,settings={'verified':'saved-settings'})))
            tuning=N(context=Mock(return_value=dict(engine='official_open',values={'verified':'saved-tuning'})),
                select_engine=Mock())
            controller.retarget=retarget;controller.tuning=tuning
            receipt={k:proof[k] for k in ('source_kind','source_run_id','transfer_id','source_workspace',
                'source_profile','serial','side','model_sha256')}
            receipt.update(owner_user_id='owner-1',imported=True)
            yield N(controller=controller,switch=switch,config=config,proof=proof,callbacks=callbacks,
                glove=glove,retarget=retarget,tuning=tuning,binding=binding,receipt=receipt)

    def start(self,e,number=1):
        e.switch.state.update(busy=True,phase='connecting',attempt_id=f'{number:032x}',
            request_id=f'{number+100:032x}',cancel_requested=False)
        e.switch.cancelled=False;e.switch._thread=threading.get_ident()
        return e.controller._start_saved_profile_session(e.proof,e.proof,expected_profile='hand2_right')

    def assert_unchanged(self,e):
        e.retarget.select.assert_not_called();e.tuning.context.assert_not_called()
        e.tuning.select_engine.assert_not_called();self.assertEqual(e.controller.state['view_source'],'feedback')
        self.assertEqual(e.binding['glove_serial'],'PREVIOUS-GLOVE')

    def during_binding_read(self,e,change):
        entered=threading.Event();release=threading.Event();errors=[];results=[]
        def read(profile):
            entered.set()
            if not release.wait(2):raise RuntimeError('synthetic binding read timed out')
            return dict(binding=e.binding)
        e.retarget.context.side_effect=read
        def ready():
            try:results.append(e.callbacks[-1](e.receipt))
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=ready);thread.start()
        try:
            self.assertTrue(entered.wait(1),'readiness must reach the controlled binding read')
            change()
        finally:
            release.set();thread.join(3)
        self.assertFalse(thread.is_alive(),'cancel and readiness must not deadlock')
        return errors,results

    def test_cancel_during_binding_read_keeps_settings_and_explicit_retry_works(self):
        with self.environment() as e:
            self.start(e)
            def cancel():
                result=e.switch.cancel(dict(name='connection_switch_cancel',
                    request_id=e.switch.state['request_id'],attempt_id=e.switch.state['attempt_id']))
                self.assertTrue(result['connection_switch']['cancel_requested'])
            errors,results=self.during_binding_read(e,cancel)
            self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],ValueError);self.assertFalse(results)
            self.assert_unchanged(e)
            old=e.callbacks[0];e.retarget.context.side_effect=None
            session=self.start(e,2)
            with self.assertRaisesRegex(ValueError,'changed|改变'):old(e.receipt)
            self.assert_unchanged(e)
            result=e.callbacks[-1](e.receipt)
            self.assertEqual(session,2);self.assertEqual(result['timeout_ms'],250)
            e.retarget.select.assert_called_once_with({**e.binding,'glove_serial':'WG-SYNTHETIC','sdk_user':'owner-1'},e.controller.state['device_profile'])
            e.tuning.select_engine.assert_called_once();self.assertEqual(e.controller.state['view_source'],'glove')

    def test_configuration_change_during_binding_read_is_rechecked_before_writes(self):
        with self.environment() as e:
            self.start(e)
            errors,results=self.during_binding_read(e,lambda:e.config.__setitem__(0,'replacement-controller'))
            self.assertEqual(len(errors),1);self.assertIn('configuration changed',str(errors[0]))
            self.assertFalse(results);self.assert_unchanged(e)

    def test_replacement_bridge_during_binding_read_is_not_modified(self):
        for change in ('session','context','stopping'):
            with self.subTest(change=change),self.environment() as e:
                self.start(e)
                def replace():
                    with e.glove.lock:
                        if change=='session':e.glove.session+=1
                        elif change=='context':e.glove._continuation['transfer_id']='f'*32
                        else:e.glove.stopping=True
                errors,results=self.during_binding_read(e,replace)
                self.assertEqual(len(errors),1);self.assertIn('session changed',str(errors[0]))
                self.assertFalse(results);self.assert_unchanged(e)

    def test_current_readiness_uses_its_exact_saved_binding_and_tuning(self):
        with self.environment() as e:
            session=self.start(e);result=e.callbacks[0](e.receipt)
            self.assertEqual(session,1);self.assertEqual(e.controller.sdk_session_configs,{1:'original-controller'})
            self.assertEqual(result,dict(timeout_ms=250,retarget={'verified':'saved-settings'},
                solver=dict(engine='sdk',values={'verified':'saved-tuning'})))
            self.assertEqual(e.controller.state['view_source'],'glove')
            e.retarget.select.assert_called_once();e.tuning.select_engine.assert_called_once()


if __name__=='__main__':unittest.main()
