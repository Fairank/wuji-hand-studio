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


if __name__=='__main__':unittest.main()
