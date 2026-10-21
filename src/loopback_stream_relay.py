"""Bounded TCP relay for one fixed loopback endpoint; no HTTP interpretation.

Importing creates no sockets or threads. The caller owns each relay's lifetime.
Bytes are held only in bounded in-memory write queues and are never logged,
cached, inspected for URLs, or written to disk. No device API is used.
"""
import math
import selectors
import socket
import threading
import time


BUFFER_SIZE = 64 * 1024  # Per direction; backpressure stops further reads.
CONNECTION_LIMIT = 16
CONNECT_TIMEOUT = 3
IDLE_TIMEOUT = 120
SELECT_TIMEOUT = .2


def _close_socket(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


class StreamRelay:
    """Listen on 127.0.0.1:0 and forward only to 127.0.0.1:upstream_port.

    ``close`` returns True only after the listener, all owned sockets and every
    owned worker have stopped. A False result retains references for retry.
    Closing a relay never closes, stops, or sends an administrative command to
    the upstream server. All socket handles are explicitly non-inheritable.
    """
    def __init__(self, upstream_port):
        if type(upstream_port) is not int or not 1 <= upstream_port <= 65535:
            raise ValueError('Expected an upstream TCP port from 1 to 65535')
        self._upstream = ('127.0.0.1', upstream_port)
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._capacity = threading.BoundedSemaphore(CONNECTION_LIMIT)
        self._sockets = set()
        self._workers = set()
        self._listener = None
        self._acceptor = None
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.set_inheritable(False)
            listener.bind(('127.0.0.1', 0))
            listener.listen(CONNECTION_LIMIT)
            listener.settimeout(SELECT_TIMEOUT)
            self._port = listener.getsockname()[1]
            self._listener = listener
            self._acceptor = threading.Thread(target=self._accept, name='Loopback stream relay', daemon=True)
            self._acceptor.start()
        except BaseException:
            listener.close()
            self._listener = None
            raise

    @property
    def port(self):
        return self._port

    def _accept(self):
        while not self._stopping.is_set():
            with self._lock:
                listener = self._listener
            if listener is None:
                return
            client = None
            try:
                client, _ = listener.accept()
                client.set_inheritable(False)
            except socket.timeout:
                continue
            except OSError:
                if client is not None:
                    _close_socket(client)
                return
            with self._lock:
                if self._stopping.is_set() or not self._capacity.acquire(blocking=False):
                    _close_socket(client)
                    continue
                # Keep every still-live worker handle until another thread
                # can positively observe its exit; workers cannot certify
                # their own termination from inside their finalizer.
                self._workers = {worker for worker in self._workers if worker.is_alive()}
                self._sockets.add(client)
                worker = threading.Thread(target=self._serve, args=(client,), name='Loopback stream connection', daemon=True)
                self._workers.add(worker)
                try:
                    worker.start()
                except BaseException:
                    self._workers.discard(worker)
                    self._sockets.discard(client)
                    self._capacity.release()
                    _close_socket(client)

    def _serve(self, client):
        upstream = None
        try:
            with self._lock:
                if self._stopping.is_set():
                    return
                upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                upstream.set_inheritable(False)
                self._sockets.add(upstream)
            upstream.settimeout(CONNECT_TIMEOUT)
            upstream.connect(self._upstream)
            client.setblocking(False)
            upstream.setblocking(False)
            self._forward(client, upstream)
        except (OSError, ValueError):
            # Transport failures end only this connection. No payload or URLs
            # are included in diagnostics, and no reconnection is attempted.
            pass
        finally:
            for sock in (client, upstream):
                if sock is None:
                    continue
                try:
                    _close_socket(sock)
                except OSError:
                    pass
                finally:
                    with self._lock:
                        if sock.fileno() < 0:
                            self._sockets.discard(sock)
            self._capacity.release()

    def _forward(self, client, upstream):
        peers = {client: upstream, upstream: client}
        pending = {client: bytearray(), upstream: bytearray()}
        reading = {client: True, upstream: True}
        writing = {client: True, upstream: True}
        last_progress = time.monotonic()
        with selectors.DefaultSelector() as selector:
            while not self._stopping.is_set():
                for source, target in peers.items():
                    # A FIN is forwarded only after all preceding bytes have
                    # drained; the reverse direction remains fully usable.
                    if not reading[source] and not pending[target] and writing[target]:
                        target.shutdown(socket.SHUT_WR)
                        writing[target] = False
                if not any(reading.values()) and not any(pending.values()):
                    return
                for current, target in peers.items():
                    events = 0
                    if reading[current] and writing[target] and len(pending[target]) < BUFFER_SIZE:
                        events |= selectors.EVENT_READ
                    if writing[current] and pending[current]:
                        events |= selectors.EVENT_WRITE
                    registered = current in selector.get_map()
                    if events:
                        if registered:
                            selector.modify(current, events)
                        else:
                            selector.register(current, events)
                    elif registered:
                        selector.unregister(current)
                if time.monotonic() - last_progress >= IDLE_TIMEOUT:
                    return
                for key, events in selector.select(SELECT_TIMEOUT):
                    current = key.fileobj
                    if events & selectors.EVENT_READ:
                        target = peers[current]
                        try:
                            data = current.recv(BUFFER_SIZE - len(pending[target]))
                        except BlockingIOError:
                            data = None
                        if data is not None:
                            last_progress = time.monotonic()
                            if data:
                                pending[target].extend(data)
                            else:
                                reading[current] = False
                    if events & selectors.EVENT_WRITE:
                        try:
                            sent = current.send(pending[current])
                        except BlockingIOError:
                            sent = None
                        if sent == 0:
                            raise ConnectionError('Loopback peer closed during write')
                        if sent:
                            del pending[current][:sent]
                            last_progress = time.monotonic()

    def close(self, timeout=5):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('Timeout must be a finite nonnegative number')
        deadline = time.monotonic() + timeout
        with self._lock:
            self._stopping.set()
            listener = self._listener
            owned = list(self._sockets)
            workers = list(self._workers)
            acceptor = self._acceptor
        confirmed = True
        for sock in ([listener] if listener is not None else []) + owned:
            try:
                _close_socket(sock)
            except OSError:
                confirmed = False
            finally:
                with self._lock:
                    if sock.fileno() < 0:
                        self._sockets.discard(sock)
                        if self._listener is sock:
                            self._listener = None
        current = threading.current_thread()
        for worker in ([acceptor] if acceptor is not None else []) + workers:
            if worker is current:
                confirmed = False
                continue
            worker.join(timeout=max(0, deadline - time.monotonic()))
            if worker.is_alive():
                confirmed = False
        with self._lock:
            self._workers = {worker for worker in self._workers if worker.is_alive()}
            remaining = self._listener is not None or bool(self._sockets) or any(worker.is_alive() for worker in self._workers)
            acceptor_alive = self._acceptor is not None and self._acceptor.is_alive()
            if confirmed and not remaining and not acceptor_alive:
                self._acceptor = None
                return True
        return False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if not self.close():
            raise RuntimeError('Loopback stream relay cleanup is unconfirmed')
