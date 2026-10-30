"""Static startup burst against the real loopback server; no Controller/SDK."""
import concurrent.futures
import http.client
from pathlib import Path
import socket
import threading
import unittest

import console_server
from console_server import ConsoleHTTPServer, Handler


class StartupQueueTests(unittest.TestCase):
    last_probe = None

    def test_static_startup_burst_survives_a_brief_accept_delay(self):
        entered = threading.Event()
        release = threading.Event()
        connected = threading.Event()
        lock = threading.Lock()
        count = 48
        connection_count = 0
        gate = threading.Barrier(count + 1)
        handler_errors = []

        class HeldServer(ConsoleHTTPServer):
            def _handle_request_noblock(self):
                if not entered.is_set():
                    entered.set()
                    release.wait(4)
                return super()._handle_request_noblock()

            def handle_error(self, request, client_address):
                handler_errors.append('handler failed')

        server = HeldServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': .01}, daemon=True)
        thread.start()

        def request(_):
            nonlocal connection_count
            try:
                gate.wait(4)
                with socket.create_connection(server.server_address, timeout=1.5) as stream:
                    with lock:
                        connection_count += 1
                        if connection_count == count:
                            connected.set()
                    stream.settimeout(4)
                    stream.sendall(('GET /glove.css HTTP/1.0\r\nHost: 127.0.0.1:'
                                    + str(console_server.PORT) + '\r\n\r\n').encode('ascii'))
                    response = http.client.HTTPResponse(stream)
                    response.begin()
                    data = response.read()
                    return {'status': response.status, 'version': response.version,
                            'cache': response.getheader('Cache-Control'),
                            'bytes': len(data), 'matches': data == expected}
            except Exception as error:
                return {'error': type(error).__name__}

        expected = (console_server.HERE / 'web' / 'glove.css').read_bytes()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
                futures = [pool.submit(request, i) for i in range(count)]
                gate.wait(4)
                self.assertTrue(entered.wait(2), 'The real listener was not reached')
                all_queued = connected.wait(2)
                release.set()
                results = [future.result(6) for future in futures]
            type(self).last_probe = dict(queue=server.request_queue_size,
                                         clients=count, all_queued=all_queued,
                                         connections=connection_count,
                                         results=results, handler_errors=handler_errors)
            self.assertTrue(all_queued, type(self).last_probe)
            self.assertEqual(connection_count, count)
            self.assertEqual(handler_errors, [])
            for result in results:
                self.assertEqual(result.get('status'), 200, result)
                self.assertEqual(result.get('version'), 10, result)
                self.assertEqual(result.get('cache'), 'no-store', result)
                self.assertTrue(result.get('matches'), result)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_static_security_and_document_nonce_are_unchanged(self):
        server = ConsoleHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': .01}, daemon=True)
        thread.start()

        def get(path, host=None):
            client = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                client.request('GET', path, headers={'Host': host or '127.0.0.1:'
                                                    + str(console_server.PORT)})
                response = client.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                client.close()

        try:
            status, headers, body = get('/desktop_shell.js')
            self.assertEqual(status, 200)
            self.assertEqual(body, (console_server.HERE / 'web' / 'desktop_shell.js').read_bytes())
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(headers['Content-Type'], 'text/javascript; charset=utf-8')
            self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
            status, headers, body = get('/')
            self.assertEqual(status, 200)
            self.assertIn(b'<meta name="workbench-style-nonce"', body)
            self.assertIn("style-src 'self' 'nonce-", headers['Content-Security-Policy'])
            self.assertEqual(get('/glove.css', 'external.invalid')[0], 403)
            self.assertEqual(get('/does-not-exist.css')[0], 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
        self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
