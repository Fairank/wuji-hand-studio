"""Small fragmented refused POSTs must receive their complete HTTP response."""
import http.client
from http.server import ThreadingHTTPServer
import json
import socket
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from console_server import Handler


class RejectedPOSTTests(unittest.TestCase):
    def fragmented(self,path,headers,status,*,authorization_first=True):
        reading=threading.Event();decided=threading.Event();reads=[]
        class Body:
            def __init__(self,stream):self.stream=stream
            def __getattr__(self,name):return getattr(self.stream,name)
            def read(self,size):reading.set();reads.append(('read',size));return self.stream.read(size)
            def read1(self,size):reading.set();reads.append(('read1',size));return self.stream.read1(size)
        class Observed(Handler):
            def do_POST(self):self.rfile=Body(self.rfile);return super().do_POST()
            def reply(self,code,*args,**kwargs):decided.set();return super().reply(code,*args,**kwargs)
        server=ThreadingHTTPServer(('127.0.0.1',0),Observed);server.controller=N(csrf='synthetic-http-token',action=Mock(side_effect=AssertionError('No action allowed')))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        body=b'{"operation":"inspect"}'
        try:
            connection.putrequest('POST',path,skip_host=True)
            for key,value in dict({'Host':'127.0.0.1:8781','Content-Length':str(len(body))},**headers).items():connection.putheader(key,value)
            connection.endheaders()
            if authorization_first:self.assertTrue(decided.wait(1),'Authentication/field rejection must precede reading or parsing')
            self.assertTrue(reading.wait(.5),'Rejected the request before consuming its bounded body')
            connection.send(body[:4]);connection.send(body[4:])
            response=connection.getresponse();raw=response.read()
            self.assertEqual(response.status,status);self.assertEqual(len(raw),int(response.getheader('Content-Length')));self.assertFalse(json.loads(raw).get('ok',False))
            server.controller.action.assert_not_called()
            self.assertTrue(reads);self.assertLessEqual(sum(size for _,size in reads),2*len(body))
        finally:
            connection.close();server.shutdown();server.server_close();thread.join(2)
        self.assertFalse(thread.is_alive())
        return reads

    def test_fragmented_unauthorized_desktop_receives_complete_403(self):
        self.fragmented('/api/desktop',{},403)

    def test_shared_private_and_standard_auth_refusals_consume_bounded_body(self):
        for path in ('/api/action','/api/task-owner','/api/connection-switch-owner-check'):
            with self.subTest(path=path):self.fragmented(path,{},403)

    def test_unsupported_endpoint_and_content_type_rejections_also_drain(self):
        headers={'X-Console-Token':'synthetic-http-token'}
        self.fragmented('/unsupported',headers,404)
        self.fragmented('/api/action',headers,400)

    def test_authenticated_payload_is_read_once_without_a_second_drain(self):
        reads=self.fragmented('/api/desktop',{'X-Console-Token':'synthetic-http-token','Content-Type':'application/json'},409,authorization_first=False)
        self.assertEqual(reads,[('read',23)])

    def test_authenticated_partial_eof_receives_complete_400_without_redrain(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.controller=N(csrf='synthetic-http-token',action=Mock(side_effect=AssertionError('No action allowed')))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            connection.putrequest('POST','/api/action',skip_host=True)
            for key,value in {'Host':'127.0.0.1:8781','Content-Length':'23','Content-Type':'application/json','X-Console-Token':'synthetic-http-token'}.items():connection.putheader(key,value)
            connection.endheaders();connection.send(b'{"');connection.sock.shutdown(socket.SHUT_WR)
            response=connection.getresponse();raw=response.read();self.assertEqual(response.status,400)
            self.assertEqual(len(raw),int(response.getheader('Content-Length')));self.assertFalse(json.loads(raw)['ok']);server.controller.action.assert_not_called()
        finally:connection.close();server.shutdown();server.server_close();thread.join(2)

    def test_partial_json_or_read_exception_does_not_wait_for_the_body_again(self):
        for effect in (b'{"',OSError('Synthetic broken body read')):
            with self.subTest(effect=effect):
                read=Mock(side_effect=effect) if isinstance(effect,Exception) else Mock(return_value=effect)
                target=N(path='/api/action',server=N(controller=N(csrf='synthetic-http-token')),
                    rfile=N(read=read,read1=Mock(side_effect=AssertionError('Do not redrain'))),connection=Mock(),
                    headers={'Content-Length':'23','Content-Type':'application/json','X-Console-Token':'synthetic-http-token'},allowed_host=lambda:True,reply=Mock())
                Handler.do_POST(target);self.assertEqual(target.reply.call_args.args[0],503 if isinstance(effect,Exception) else 400)
                Handler._discard_post_body(target);self.assertTrue(target._post_body_read);target.rfile.read1.assert_not_called();target.connection.settimeout.assert_not_called()

    def test_rejection_read_error_restores_timeout_and_is_not_retried(self):
        connection=N(gettimeout=lambda:7.,settimeout=Mock());read=Mock(side_effect=[b'x',TimeoutError('Synthetic read timeout')])
        target=N(headers={'Content-Length':'3'},connection=connection,rfile=N(read1=read))
        Handler._discard_post_body(target);Handler._discard_post_body(target)
        self.assertEqual(read.call_count,2);self.assertEqual(connection.settimeout.call_args.args,(7.,));self.assertTrue(target._post_body_read)

    def test_rejection_drain_has_one_total_deadline_and_restores_timeout(self):
        tick=[0.];connection=N(timeout=9.);timeouts=[]
        connection.gettimeout=lambda:connection.timeout
        def timeout(value):connection.timeout=value;timeouts.append(value)
        connection.settimeout=timeout
        def read1(size):tick[0]+=min(.4,connection.timeout);return b'x'
        target=N(headers={'Content-Length':'20'},connection=connection,rfile=N(read1=Mock(side_effect=read1)))
        with patch('console_server.time.monotonic',side_effect=lambda:tick[0]):Handler._discard_post_body(target)
        self.assertLessEqual(tick[0],1.);self.assertEqual(target.rfile.read1.call_count,3);self.assertEqual(connection.timeout,9.)
        self.assertTrue(target.close_connection);self.assertLess(timeouts[2],timeouts[0])

    def test_unbounded_or_ambiguous_framing_is_not_drained(self):
        for headers in ({'Content-Length':'4097'},{'Content-Length':'invalid'},{'Content-Length':'-1'},{'Content-Length':'3','Transfer-Encoding':'chunked'}):
            with self.subTest(headers=headers):
                target=N(headers=headers,connection=Mock(),rfile=Mock())
                Handler._discard_post_body(target)
                target.rfile.read1.assert_not_called();target.connection.settimeout.assert_not_called()


if __name__=='__main__':unittest.main()
