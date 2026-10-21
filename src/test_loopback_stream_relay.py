"""Only synthetic loopback TCP servers; no CLI, SDK or physical device."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import socket
import threading
import time
import unittest
from unittest.mock import patch

import loopback_stream_relay as relay_module
from loopback_stream_relay import StreamRelay


def receive_all(connection):
    result = bytearray()
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            return bytes(result)
        result.extend(chunk)


def echo(connection):
    while True:
        data = connection.recv(32768)
        if not data:
            return
        connection.sendall(data)


class SyntheticServer:
    def __init__(self, handler=echo):
        self.handler = handler
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.accepted = 0
        self.connections = set()
        self.workers = []
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(32)
        self.listener.settimeout(.1)
        self.acceptor = threading.Thread(target=self.accept, daemon=True)
        self.acceptor.start()

    def accept(self):
        while not self.stopping.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            connection.settimeout(5)
            with self.lock:
                self.connections.add(connection)
                self.accepted += 1
                worker = threading.Thread(target=self.serve, args=(connection,), daemon=True)
                self.workers.append(worker)
                worker.start()

    def serve(self, connection):
        try:
            self.handler(connection)
        except OSError:
            pass
        finally:
            connection.close()
            with self.lock:
                self.connections.discard(connection)

    def close(self):
        self.stopping.set()
        self.listener.close()
        with self.lock:
            connections, workers = list(self.connections), list(self.workers)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.acceptor.join(2)
        for worker in workers:
            worker.join(2)


class RelayTests(unittest.TestCase):
    def server(self, handler=echo):
        server = SyntheticServer(handler)
        self.addCleanup(server.close)
        return server

    def relay(self, server):
        relay = StreamRelay(server.port)
        self.addCleanup(relay.close)
        return relay

    def connect(self, port):
        connection = socket.create_connection(('127.0.0.1', port), timeout=5)
        self.addCleanup(connection.close)
        return connection

    def wait_until(self, predicate, seconds=3):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.assertTrue(predicate(), 'Synthetic transport did not reach the expected state')

    def test_import_has_no_socket_or_thread_side_effects(self):
        specification = importlib.util.spec_from_file_location('relay_import_audit', relay_module.__file__)
        module = importlib.util.module_from_spec(specification)
        with patch.object(socket, 'socket', side_effect=AssertionError('No socket at import')), \
             patch.object(threading, 'Thread', side_effect=AssertionError('No thread at import')):
            specification.loader.exec_module(module)

    def test_constructor_rejects_invalid_port_before_opening_socket(self):
        with patch.object(socket, 'socket') as create:
            for port in (0, -1, 65536, True, False, '5061', None, 1.0):
                with self.subTest(port=port), self.assertRaises(ValueError):
                    StreamRelay(port)
            create.assert_not_called()

    def test_loopback_binding_noninheritance_and_fixed_opaque_target(self):
        server = self.server()
        relay = self.relay(server)
        self.assertEqual(relay._listener.getsockname(), ('127.0.0.1', relay.port))
        self.assertFalse(relay._listener.get_inheritable())
        request = b'CONNECT 203.0.113.1:9999 HTTP/1.1\r\nHost: example.invalid\r\n\r\n'
        client = self.connect(relay.port)
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        self.assertEqual(receive_all(client), request)
        self.assertEqual(server.accepted, 1)
        self.assertEqual(relay._upstream, ('127.0.0.1', server.port))

    def test_large_binary_stream_is_exact_and_does_not_deadlock(self):
        relay = self.relay(self.server())
        client = self.connect(relay.port)
        payload = bytes(range(256)) * 16384  # 4 MiB; bounded queues must drain.
        errors = []

        def send():
            try:
                for offset in range(0, len(payload), 17003):
                    client.sendall(payload[offset:offset + 17003])
                client.shutdown(socket.SHUT_WR)
            except OSError as error:
                errors.append(error)

        sender = threading.Thread(target=send, daemon=True)
        sender.start()
        self.assertEqual(receive_all(client), payload)
        sender.join(5)
        self.assertFalse(sender.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(relay_module.BUFFER_SIZE, 65536)

    def test_client_half_close_preserves_delayed_reverse_response(self):
        captured = []

        def handler(connection):
            captured.append(receive_all(connection))
            connection.sendall(b'response after request FIN')
            connection.shutdown(socket.SHUT_WR)

        relay = self.relay(self.server(handler))
        client = self.connect(relay.port)
        client.sendall(b'request payload')
        client.shutdown(socket.SHUT_WR)
        self.assertEqual(receive_all(client), b'response after request FIN')
        self.assertEqual(captured, [b'request payload'])

    def test_upstream_half_close_keeps_client_upload_alive(self):
        captured, done = [], threading.Event()

        def handler(connection):
            connection.sendall(b'upstream greeting')
            connection.shutdown(socket.SHUT_WR)
            captured.append(receive_all(connection))
            done.set()

        relay = self.relay(self.server(handler))
        client = self.connect(relay.port)
        self.assertEqual(receive_all(client), b'upstream greeting')
        client.sendall(b'upload after seeing response EOF')
        client.shutdown(socket.SHUT_WR)
        self.assertTrue(done.wait(3))
        self.assertEqual(captured, [b'upload after seeing response EOF'])

    def test_http_grpc_web_chunked_headers_trailers_and_bytes_are_unchanged(self):
        request = (b'POST /rerun.sdk_comms.v1alpha1.MessageProxyService/ReadMessages HTTP/1.1\r\n'
                   b'Host: localhost\r\nContent-Type: application/grpc-web+proto\r\n'
                   b'Content-Length: 6\r\n\r\n\x00\x00\x00\x00\x01\x7f')
        response = (b'HTTP/1.1 200 OK\r\nContent-Type: application/grpc-web+proto\r\n'
                    b'Transfer-Encoding: chunked\r\nAccess-Control-Allow-Origin: *\r\n\r\n'
                    b'6\r\n\x00\x00\x00\x00\x01\x7f\r\n3\r\nabc\r\n0\r\nGrpc-Status: 0\r\n\r\n')
        captured = []

        def handler(connection):
            received = bytearray()
            while len(received) < len(request):
                received.extend(connection.recv(4096))
            captured.append(bytes(received))
            for offset in range(0, len(response), 7):
                connection.sendall(response[offset:offset + 7])
            connection.shutdown(socket.SHUT_WR)

        relay = self.relay(self.server(handler))
        client = self.connect(relay.port)
        client.sendall(request)
        self.assertEqual(receive_all(client), response)
        self.assertEqual(captured, [request])

    def test_concurrent_clients_are_isolated(self):
        relay = self.relay(self.server())

        def exercise(index):
            with socket.create_connection(('127.0.0.1', relay.port), timeout=5) as client:
                payload = ('isolated client ' + str(index)).encode() * 200
                client.sendall(payload)
                client.shutdown(socket.SHUT_WR)
                return receive_all(client) == payload

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertTrue(all(pool.map(exercise, range(24))))

    def test_connection_limit_rejects_overflow_without_affecting_existing_clients(self):
        server = self.server()
        relay = self.relay(server)
        clients = [self.connect(relay.port) for _ in range(16)]
        self.wait_until(lambda: server.accepted == 16)
        excess = self.connect(relay.port)
        try:
            self.assertEqual(excess.recv(1), b'')
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        self.assertEqual(server.accepted, 16)
        with relay._lock:
            self.assertLessEqual(len(relay._workers), 16)
            self.assertTrue(all(not owned.get_inheritable() for owned in relay._sockets))
        clients[0].sendall(b'still working')
        self.assertEqual(clients[0].recv(64), b'still working')

    def test_close_stops_owned_connections_but_not_upstream_or_other_server(self):
        upstream, unrelated = self.server(), self.server()
        relay = self.relay(upstream)
        client = self.connect(relay.port)
        client.sendall(b'open')
        self.assertEqual(client.recv(4), b'open')
        self.assertTrue(relay.close(timeout=5))
        self.assertTrue(relay.close())
        self.assertIsNone(relay._listener)
        self.assertIsNone(relay._acceptor)
        self.assertEqual(relay._sockets, set())
        self.assertFalse(any(worker.is_alive() for worker in relay._workers))
        try:
            self.assertEqual(client.recv(1), b'')
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        for server in (upstream, unrelated):
            direct = self.connect(server.port)
            direct.sendall(b'not stopped')
            self.assertEqual(direct.recv(32), b'not stopped')

    def test_listener_close_failure_retains_reference_for_retry(self):
        relay = self.relay(self.server())
        original = relay_module._close_socket
        listener = relay._listener

        def fail_listener(sock):
            if sock is listener:
                raise OSError('synthetic close error')
            return original(sock)

        with patch.object(relay_module, '_close_socket', side_effect=fail_listener):
            self.assertFalse(relay.close(timeout=1))
        self.assertIs(relay._listener, listener)
        self.assertGreaterEqual(listener.fileno(), 0)
        self.assertTrue(relay.close())

    def test_zero_timeout_can_retry_until_workers_have_exited(self):
        relay = self.relay(self.server())
        client = self.connect(relay.port)
        client.sendall(b'open')
        self.assertEqual(client.recv(4), b'open')
        first = relay.close(timeout=0)
        self.assertIsInstance(first, bool)
        self.assertTrue(relay.close(timeout=5))

    def test_close_waits_for_worker_finalizer_after_sockets_have_closed(self):
        relay = self.relay(self.server())
        finishing, allow_exit = threading.Event(), threading.Event()
        original_release = relay._capacity.release

        def paused_release():
            finishing.set()
            allow_exit.wait(3)
            original_release()

        with patch.object(relay._capacity, 'release', side_effect=paused_release):
            try:
                client = self.connect(relay.port)
                client.sendall(b'end normally')
                client.shutdown(socket.SHUT_WR)
                self.assertEqual(receive_all(client), b'end normally')
                self.assertTrue(finishing.wait(2))
                relay._stopping.set()
                relay._listener.close()
                relay._acceptor.join(2)
                self.assertFalse(relay.close(timeout=.03), 'A live finalizer cannot certify its own thread exit')
                with relay._lock:
                    self.assertTrue(any(worker.is_alive() for worker in relay._workers))
            finally:
                allow_exit.set()
            self.assertTrue(relay.close(timeout=5))

    def test_idle_timeout_closes_only_the_idle_connection(self):
        relay = self.relay(self.server())
        with patch.object(relay_module, 'IDLE_TIMEOUT', .05), patch.object(relay_module, 'SELECT_TIMEOUT', .01):
            idle = self.connect(relay.port)
            self.assertEqual(idle.recv(1), b'')
        self.assertTrue(relay.close())

    def test_upstream_unavailable_closes_client_without_retargeting(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as unused:
            unused.bind(('127.0.0.1', 0))
            port = unused.getsockname()[1]
        relay = StreamRelay(port)
        self.addCleanup(relay.close)
        client = self.connect(relay.port)
        try:
            self.assertEqual(client.recv(1), b'')
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        self.assertEqual(relay._upstream, ('127.0.0.1', port))
        self.assertTrue(relay.close())

    def test_close_timeout_validation(self):
        relay = self.relay(self.server())
        for value in (-1, True, '1', float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                relay.close(timeout=value)


if __name__ == '__main__':
    unittest.main()
