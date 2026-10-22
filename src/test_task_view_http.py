"""Exercise the actual HTTP task-page boundary with synthetic owners only."""
import copy
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
import os
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

from console_server import Handler
from test_task_view import idle


def task(kind, serial):
    return hashlib.sha256((kind+':'+serial).encode()).hexdigest()[:24]


class TaskViewHTTPTests(unittest.TestCase):
    def setUp(self):
        self.state=idle()
        self.state['glove'].update(busy=True, connection='receiving', session_id=7,
            glove_serial='WG-A', profile='hand2_right', user={'user_id':'Alice'},
            stream={'fresh':True}, can_prepare=True)
        self.controller=N(snapshot=lambda:copy.deepcopy(self.state), action=Mock(return_value={'requested':True}),
            csrf='synthetic-token', calibration_handover=N(workflow_lock=threading.RLock()))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.controller=self.controller
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.ident=task('glove','WG-A')

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def request(self, method, path, payload=None, headers=None):
        request_headers={'Host':'127.0.0.1:8781','Content-Type':'application/json',
                         'X-Console-Token':'synthetic-token'}
        request_headers.update(headers or {})
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request(method,path,body=json.dumps(payload) if payload is not None else None,headers=request_headers)
        response=connection.getresponse();code=response.status;value=json.loads(response.read());connection.close()
        return code,value

    def scope(self, ident=None):
        code,value=self.request('GET','/api/task-view?task_id='+(ident or self.ident))
        self.assertEqual(code,200)
        return value

    def headers(self, scope):
        return {'X-Task-Id':scope['task_id'],'X-Task-Context':scope['owner_context'],
                'Referer':'http://127.0.0.1:8781/?embedded=1&task_view=1&task_id='+scope['task_id']}

    def test_old_tab_cannot_disconnect_or_renew_the_replacement_glove(self):
        scope=self.scope();self.assertTrue(scope['current'])
        self.state['glove']['glove_serial']='WG-B'
        for name in ('glove_disconnect','glove_stop','glove_keepalive','session_keepalive'):
            with self.subTest(name=name):
                code,value=self.request('POST','/api/action',{'name':name},self.headers(scope))
                self.assertEqual(code,400);self.assertFalse(value['ok'])
        self.controller.action.assert_not_called()
        self.assertFalse(self.scope()['current'])
        self.assertTrue(self.scope(task('glove','WG-B'))['current'])

    def test_same_glove_new_epoch_requires_fresh_owner_context(self):
        old=self.scope();self.state['glove']['session_id']=8
        code,_=self.request('POST','/api/action',{'name':'glove_disconnect'},self.headers(old))
        self.assertEqual(code,400);self.controller.action.assert_not_called()
        new=self.scope();self.assertTrue(new['current']);self.assertNotEqual(new['owner_context'],old['owner_context'])
        code,value=self.request('POST','/api/action',{'name':'glove_disconnect'},self.headers(new))
        self.assertEqual(code,200);self.assertTrue(value['ok'])
        self.controller.action.assert_called_once_with({'name':'glove_disconnect'})

    def test_iframe_referrer_requires_both_headers_and_matching_fixed_task(self):
        scope=self.scope();headers=self.headers(scope)
        invalid=[{'Referer':headers['Referer']},
                 {**headers,'X-Task-Context':''},
                 {**headers,'X-Task-Id':task('glove','WG-B')}]
        for value in invalid:
            with self.subTest(value=value):
                code,_=self.request('POST','/api/action',{'name':'glove_disconnect'},value)
                self.assertEqual(code,400)
        self.controller.action.assert_not_called()

    def test_task_page_cannot_bypass_child_native_or_desktop_boundary(self):
        scope=self.scope();headers=self.headers(scope)
        code,_=self.request('POST','/api/desktop',{'operation':'inspect'},headers)
        self.assertEqual(code,400)
        with patch.dict(os.environ,WUJI_FLEET_PARENT='http://127.0.0.1:8783'):
            code,_=self.request('POST','/api/action',{'name':'calibration_start','serial':'WG-A'},headers)
            self.assertEqual(code,400)
        self.controller.action.assert_not_called()

    def test_scope_reads_create_no_catalog_fleet_or_connection(self):
        self.scope()
        self.assertFalse(any(hasattr(self.server,name) for name in ('_tasks','_fleet','_workspaces')))
        self.controller.action.assert_not_called()
        for query in ('','?task_id=x','?task_id='+self.ident+'&task_id='+self.ident,
                      '?task_id='+self.ident+'&serial=OTHER'):
            self.assertEqual(self.request('GET','/api/task-view'+query)[0],400)

    def test_ensemble_frame_only_reaches_its_exact_existing_group(self):
        ident=task('ensemble','main')
        row=dict(task_id=ident,revision=1,kind='ensemble',status='running',
                 intent={'group_id':'main','members':['a','b']},devices={'hands':['WH-A','WH-B']})
        rows=[row]
        self.server._tasks=N(snapshot=lambda:{'tasks':copy.deepcopy(rows)})
        self.server._workspaces=N(lock=threading.RLock(),action=Mock(return_value={'stopped':True}))
        scope=self.scope(ident);self.assertTrue(scope['current'])
        command={'name':'ensemble_stop','workspace':'main'}
        code,_=self.request('POST','/api/action',command,self.headers(scope))
        self.assertEqual(code,200)
        self.server._workspaces.action.assert_called_once_with(command)
        for other in ({'name':'glove_disconnect'}, {'name':'ensemble_stop','workspace':'elsewhere'}):
            self.assertEqual(self.request('POST','/api/action',other,self.headers(scope))[0],400)
        rows[0]['devices']['hands']=['WH-C','WH-D']
        self.assertEqual(self.request('POST','/api/action',command,self.headers(scope))[0],400)
        self.controller.action.assert_not_called()
        self.assertEqual(self.server._workspaces.action.call_count,1)


if __name__=='__main__':
    unittest.main()
