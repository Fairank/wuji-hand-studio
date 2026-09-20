import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from console_server import Controller, Handler


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.controller=Controller(reports=Path(self.tmp.name)/'reports')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,method,path,body=None,headers=None):
        h={'Host':'127.0.0.1:8781'}
        if headers:h.update(headers)
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        c.request(method,path,body=body,headers=h)
        response=c.getresponse();result=(response.status,json.loads(response.read()));c.close()
        return result

    def test_initial_state_never_autoconnects(self):
        code,state=self.request('GET','/api/state')
        self.assertEqual(code,200);self.assertEqual(state['connection'],'disconnected')
        self.assertFalse(state['motion_enabled']);self.assertIsNone(state['latest'])

    def test_network_preflight_script_is_served(self):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        c.request('GET','/device_network.js',headers={'Host':'127.0.0.1:8781'})
        r=c.getresponse();body=r.read();c.close()
        self.assertEqual(r.status,200);self.assertIn(b'WujiNetwork',body)

    def test_cross_origin_and_bad_host_rejected(self):
        code,_=self.request('GET','/api/state',headers={'Host':'external.invalid'})
        self.assertEqual(code,403)
        code,_=self.request('POST','/api/action',json.dumps({'name':'disconnect'}),
            {'Content-Type':'application/json','X-Console-Token':self.server.controller.csrf,'Origin':'https://external.invalid'})
        self.assertEqual(code,403)

    def test_motion_not_exposed_and_record_requires_feedback(self):
        h={'Content-Type':'application/json','X-Console-Token':self.server.controller.csrf}
        for name in ['enable','joint_command','emergency_stop']:
            code,_=self.request('POST','/api/action',json.dumps({'name':name}),h)
            self.assertEqual(code,400)
        code,_=self.request('POST','/api/action',json.dumps({'name':'record','label':'baseline','seconds':30}),h)
        self.assertEqual(code,400)

    def test_report_path_traversal_rejected(self):
        code,_=self.request('GET','/api/report?id=../../capture.py')
        self.assertEqual(code,400)

    def test_desktop_inspection_is_authenticated_and_app_scoped(self):
        from unittest.mock import Mock
        self.assertFalse(self.request('GET','/api/desktop')[1]['native'])
        payload=json.dumps({'operation':'inspect'})
        headers={'Content-Type':'application/json','X-Console-Token':self.server.controller.csrf}
        self.assertEqual(self.request('POST','/api/desktop',payload)[0],403)
        self.assertEqual(self.request('POST','/api/desktop',payload,headers)[0],409)
        host=Mock();host.inspect.return_value=dict(ok=True,ui=dict(page='library'))
        self.server.desktop=host
        code,result=self.request('POST','/api/desktop',payload,headers)
        self.assertEqual(code,200);self.assertTrue(result['ok'])
        self.assertEqual(self.request('POST','/api/desktop',payload,dict(headers,Origin='https://foreign.invalid'))[0],403)
        self.assertEqual(self.request('POST','/api/desktop',json.dumps({'operation':'exec'}),headers)[0],400)

    def test_shutdown_cannot_race_a_new_connection(self):
        self.server.controller.desktop_closing=True
        with self.assertRaisesRegex(ValueError,'closing'):
            self.server.controller.action(dict(name='connect'))
        self.server.controller.action(dict(name='disconnect'))


if __name__=='__main__':unittest.main()
