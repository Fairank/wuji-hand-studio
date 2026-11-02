"""Display-only writes stay with the exact source the user reviewed."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as N
from unittest.mock import Mock

from console_server import Controller,ConsoleHTTPServer,Handler,ORIGIN


class DisplayMappingContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.c=Controller(factory=Mock(side_effect=AssertionError('No real SDK/process')),reports=self.root/'reports')
        self.server=object.__new__(ConsoleHTTPServer);self.server.controller=self.c
        self.server.configure_official_cli(startup=False)
        self.c.generation=1;self.c.state.update(connection='connected',device_id='WH_SYNTHETIC_A',
            device_profile=dict(id='hand2_right',generation='hand2',side='right'),joint_rates=[dict(nid=1)])
        self.entries=[dict(index=0,nid=1,sign=-1,offset=.25,verified=True)]
        self.path=self.root/'display_mapping.json'
        self.context=dict(service_token=self.c.csrf,session_generation=1,device_id='WH_SYNTHETIC_A',profile='hand2_right')

    def tearDown(self):self.tmp.cleanup()

    def request(self,context=...,include=True):
        command=dict(name='display_mapping',entries=self.entries)
        if include:command['expected_context']=copy.deepcopy(self.context if context is ... else context)
        raw=json.dumps(command).encode();replies=[]
        target=N(path='/api/action',server=self.server,rfile=io.BytesIO(raw),
            headers={'Origin':ORIGIN,'X-Console-Token':self.c.csrf,'Content-Type':'application/json','Content-Length':str(len(raw))},
            allowed_host=lambda:True,_task_view_headers=lambda:None)
        target.reply=lambda *a,**k:replies.append(a);Handler.do_POST(target)
        self.assertEqual(len(replies),1);return replies[0][0],replies[0][1]

    def test_current_source_saves_original_entries_without_hardware_commands(self):
        self.c.send=Mock(side_effect=AssertionError('Display write must not send SDK commands'))
        code,body=self.request();self.assertEqual(code,200);self.assertTrue(body['ok'])
        saved=json.loads(self.path.read_text('utf8'));self.assertEqual(saved['device_id'],'WH_SYNTHETIC_A')
        self.assertEqual(saved['entries'],self.entries);self.assertTrue(saved['no_hardware_commands']);self.c.send.assert_not_called()

    def test_same_nids_in_replacement_device_cannot_receive_old_mapping(self):
        self.c.state['device_id']='WH_SYNTHETIC_B';code,_=self.request()
        self.assertEqual(code,400);self.assertFalse(self.path.exists());self.assertEqual(self.c.state['mapping'],[])

    def test_same_device_reconnected_with_new_generation_rejects_old_context_and_allows_explicit_new_save(self):
        self.c.generation=2;self.assertEqual(self.request()[0],400);self.assertFalse(self.path.exists())
        fresh=dict(self.context,session_generation=2);self.assertEqual(self.request(fresh)[0],200)
        self.assertEqual(json.loads(self.path.read_text('utf8'))['device_id'],'WH_SYNTHETIC_A')

    def test_profile_and_service_changes_preserve_previous_mapping_file(self):
        self.assertEqual(self.request()[0],200);original=self.path.read_bytes()
        for change in (dict(profile='hand1_right'),dict(service_token='previous-service'),dict(device_id='WH_OTHER')):
            self.assertEqual(self.request(dict(self.context,**change))[0],400);self.assertEqual(self.path.read_bytes(),original)
        self.c.state['device_profile']=dict(id='hand2_left',generation='hand2',side='left')
        self.assertEqual(self.request()[0],400);self.assertEqual(self.path.read_bytes(),original)

    def test_missing_partial_extra_and_untyped_context_never_write(self):
        malformed=[None,{},dict(self.context,extra=True),dict(self.context,session_generation=True),
            dict(self.context,session_generation=0),dict(self.context,session_generation=1.0),
            dict(self.context,session_generation=2**53),dict(self.context,profile=None),dict(self.context,device_id=''),
            dict(self.context,service_token=False),[],False]
        malformed.extend({key:value for key,value in self.context.items() if key!=missing} for missing in self.context)
        self.assertEqual(self.request(include=False)[0],400)
        for value in malformed:
            with self.subTest(value=value):self.assertEqual(self.request(value)[0],400);self.assertFalse(self.path.exists())

    def test_final_controller_lock_rechecks_source_after_outer_request_preflight(self):
        # Exercise the real action coordinator, changing the owner after its
        # outer gate checks and immediately before the lock-held write branch.
        original=self.c._action
        def replace(command):
            self.c.generation=2;self.c.state['device_id']='WH_SYNTHETIC_B'
            return original(command)
        self.c._action=replace
        self.assertEqual(self.request()[0],400);self.assertFalse(self.path.exists())


if __name__=='__main__':unittest.main()
