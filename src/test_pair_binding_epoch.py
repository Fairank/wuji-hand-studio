"""Cross-window pairing intent with real adapters and fake device transport."""
import copy
from contextlib import contextmanager,nullcontext
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import Controller
from mapping_library import CorruptLibraryError
from retarget_settings import defaults
from task_pairing import TaskPairing
from task_sessions import TaskSessions


class PairBindingEpochTests(unittest.TestCase):
    @contextmanager
    def environment(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=directory), \
                patch('studio_component.enforce_session_owner'),patch('cli_coordination.enforce'), \
                patch('cli_coordination.gate',side_effect=lambda:nullcontext()):
            root=Path(directory);factory=Mock(side_effect=AssertionError('No actual SDK/controller may start'))
            controller=Controller(factory=factory,reports=root/'reports')
            profile=dict(id='hand2_right',generation='hand2',side='right')
            controller.state['device_profile']=profile
            for component in ('official_cli','studio','official_viz','solver_runtime'):
                getattr(controller,component).snapshot=Mock(return_value={})
            controller.player.command=Mock()
            glove=controller.glove;glove.thread=N(is_alive=lambda:True);glove.stdin=io.StringIO()
            glove.last_update=time.monotonic()
            glove.state.update(connection='receiving',glove_serial='SYNTHETIC-GLOVE',profile=profile['id'],
                user=dict(user_id='SYNTHETIC-WEARER'),hardware={},feedback={},can_prepare=True,
                stream=dict(fresh=True,age_ms=0,timeout_ms=2000,retarget=defaults(),solver=dict(engine='sdk',applied=True)))
            binding=dict(generation='hand2',side='right',glove_serial='SYNTHETIC-GLOVE',
                sdk_user='SYNTHETIC-WEARER',hand_serial='')
            controller.retarget.select(binding,profile)
            target=dict(connection='connected',device_id='SYNTHETIC-HAND-A',stale=False,session_generation=1,
                identified_device=dict(source='sdk_identity',serial='SYNTHETIC-HAND-A',profile=profile['id']),
                device_profile=profile,hardware=dict(active=False),glove={},unconfirmed_devices=[],group={},program={})
            yield N(controller=controller,profile=profile,binding=binding,target=target,factory=factory)

    def pair(self,e,change_target):
        c=e.controller;glove=c.glove;prepared=[]
        endpoints=lambda:[dict(id='source',origin='http://127.0.0.1:19000',state=c.snapshot()),
            dict(id='target',origin='http://127.0.0.1:19001',state=copy.deepcopy(e.target))]
        registry=TaskSessions(endpoints,Mock());tasks=registry.snapshot()['tasks']
        source=copy.deepcopy(registry.records[next(row['task_id'] for row in tasks if row['kind']=='glove')])
        target=copy.deepcopy(registry.records[next(row['task_id'] for row in tasks if row['kind']=='hand')])
        released=threading.Event();resume=threading.Event()
        def fake_prepare(command):
            self.assertEqual(command['name'],'glove_prepare');prepared.append(command['serial'])
            with glove.lock:
                glove.last_update=time.monotonic()
                glove.state.update(can_prepare=False,hardware=dict(active=False),
                    feedback=dict(device_id=command['serial'],connection='connected',metrics=dict(age_ms=0),
                        identified_device=dict(source='sdk_identity',serial=command['serial'],profile=e.profile['id'])))
            return dict(submitted=True)
        glove.action=Mock(side_effect=fake_prepare)
        def dispatch(endpoint,context,action,payload):
            if endpoint=='target':
                self.assertEqual(action,'disconnect')
                e.target.update(connection='disconnected',device_id=None,identified_device=None)
                released.set();self.assertTrue(resume.wait(2));return dict(ok=True)
            return c.task_context_action(context,action,payload)
        pairing=TaskPairing(lambda endpoint:c.snapshot() if endpoint=='source' else copy.deepcopy(e.target),dispatch)
        pairing.start(source,target)
        try:
            self.assertTrue(released.wait(2))
            if change_target:
                c.action(dict(name='retarget_binding_save',binding=dict(e.binding,hand_serial='SYNTHETIC-HAND-B')))
                self.assertEqual(c.retarget.context(e.profile)['binding']['hand_serial'],'SYNTHETIC-HAND-B')
            resume.set();pairing.thread.join(2);self.assertFalse(pairing.thread.is_alive())
        finally:
            resume.set();pairing.thread.join(2)
        e.factory.assert_not_called()
        return pairing.snapshot(),prepared

    def test_new_target_choice_during_release_is_preserved_and_old_pair_never_prepares(self):
        with self.environment() as e:
            result,prepared=self.pair(e,True)
            self.assertEqual(result['phase'],'error');self.assertTrue(result['released_hand'])
            self.assertEqual(prepared,[])
            self.assertEqual(e.controller.retarget.context(e.profile)['binding']['hand_serial'],'SYNTHETIC-HAND-B')

    def test_normal_pair_still_prepares_exactly_one_selected_hand(self):
        with self.environment() as e:
            result,prepared=self.pair(e,False)
            self.assertEqual(result['phase'],'paired');self.assertEqual(prepared,['SYNTHETIC-HAND-A'])
            self.assertEqual(e.controller.retarget.context(e.profile)['binding']['hand_serial'],'SYNTHETIC-HAND-A')

    def test_unreadable_binding_only_marks_selection_unavailable_not_the_entire_snapshot(self):
        with self.environment() as e:
            for data in ('{',json.dumps(dict(e.binding,hand_serial=12)),' '*4097):
                with self.subTest(data=data[:40]):
                    e.controller.retarget.path.write_text(data,encoding='utf-8')
                    result=e.controller.snapshot()
                    self.assertIsNone(result['retarget_binding']);self.assertEqual(result['device_profile'],e.profile)
                    with self.assertRaises((ValueError,OSError)):e.controller.retarget.selection(e.profile)
            e.factory.assert_not_called()

    def test_snapshot_does_not_load_mapping_library_and_context_still_rejects_its_corruption(self):
        with self.environment() as e:
            workspace=e.controller.retarget
            with patch.object(workspace.library,'snapshot',side_effect=AssertionError('Full library read in state polling')) as library:
                for _ in range(3):self.assertEqual(e.controller.snapshot()['retarget_binding'],e.binding)
                library.assert_not_called()
            (workspace.folder/'mapping_library.json').write_text('{',encoding='utf-8')
            self.assertEqual(e.controller.snapshot()['retarget_binding'],e.binding)
            with self.assertRaises(CorruptLibraryError):workspace.context(e.profile)
            e.factory.assert_not_called()


if __name__=='__main__':unittest.main()
