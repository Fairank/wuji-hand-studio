"""Pinned original Rerun web resources for a device-free local Viewer.

This module never imports the SDK, starts a process, discovers a device or
provides simulated recordings. Live Wuji sessions use official_visualization.
"""
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

from runtime_paths import RESOURCE, PORT

MANIFEST = RESOURCE / 'official_viewer_manifest.json'
FOLDER = RESOURCE / 'official-viewer'
if not FOLDER.is_dir():
    FOLDER = RESOURCE.parent / 'runtime_payload' / 'official-viewer'
_server = None
_server_lock = threading.Lock()


def manifest():
    value = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if value.get('version') != '0.36.0' or not isinstance(value.get('files'), dict):
        raise ValueError('Invalid original Viewer manifest')
    return value


def asset(name):
    spec = manifest()
    if name not in spec['files'] or '/' in name or '\\' in name:
        raise ValueError('Unknown original Viewer asset')
    path = FOLDER / name
    row = spec['files'][name]
    if path.is_symlink() or not path.is_file():
        raise ValueError('Original Viewer resources are not installed')
    body = path.read_bytes()
    if len(body) != row['bytes'] or hashlib.sha256(body).hexdigest() != row['sha256']:
        raise ValueError('Original Viewer asset checksum mismatch')
    if name == 'index.html':
        # Only the HTML host shell changes. The original renderer JS/WASM stay
        # byte-for-byte intact. No Google Fonts request is needed offline.
        html = body.decode('utf-8')
        html = re.sub(r'<link\b[^>]*\b(?:href|src)=["\']https://fonts\.(?:googleapis|gstatic)\.com[^>]*>', '', html, flags=re.I)
        body = html.encode('utf-8')
    return body, row['mime']


def snapshot(parent_port=None):
    try:
        parent_port = PORT if parent_port is None else parent_port
        if type(parent_port) is not int or not 1024 <= parent_port <= 65535:
            raise ValueError('Invalid Workbench parent port')
        spec = manifest()
        for name in spec['files']:
            asset(name)
        global _server
        with _server_lock:
            if _server is None:
                _server = ThreadingHTTPServer(('127.0.0.1', 0), ViewerHandler)
                _server.daemon_threads = True
                _server.parent_ports = set()
                threading.Thread(target=_server.serve_forever, name='Original Viewer static resources', daemon=True).start()
            _server.parent_ports.add(parent_port)
            port = _server.server_address[1]
        return dict(available=True, version=spec['version'],
                    url=f'http://127.0.0.1:{port}/index.html', device_connected=False,
                    source='original-rerun-web-viewer', error=None)
    except (OSError, ValueError, KeyError) as error:
        return dict(available=False, version=None, url=None, device_connected=False,
                    source='original-rerun-web-viewer', error=str(error)[:300])


def content_security_policy(parent_ports=None):
    # The fixed local renderer can fetch its WASM and use workers, but cannot
    # contact arbitrary remote recordings, telemetry or device endpoints.
    origins = ' '.join(f'http://{host}:{port}' for port in sorted(parent_ports or {PORT})
                       for host in ('127.0.0.1', 'localhost'))
    return ("default-src 'none'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; "
            "connect-src 'self'; worker-src 'self' blob:; "
            f"frame-ancestors {origins}; "
            "base-uri 'none'; form-action 'none'")


class ViewerHandler(BaseHTTPRequestHandler):
    """An independent static origin: never exposes any Workbench API."""
    def log_message(self, *_):
        pass

    def do_GET(self):
        port = self.server.server_address[1]
        if self.headers.get('Host') not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
            self.send_error(403)
            return
        name = urlsplit(self.path).path.removeprefix('/') or 'index.html'
        try:
            body, mime = asset(name)
        except (OSError, ValueError, KeyError):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', content_security_policy(self.server.parent_ports.copy()))
        if name == 'index.html':
            # This origin contains no settings or device control. Clear only
            # its own storage before the original renderer can restore a file,
            # source URL or stored credential. Rerun 0.36 ignores persist=false.
            self.send_header('Clear-Site-Data', '"storage"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Hiding/reloading the iframe can cancel its WASM download.
            pass


def close():
    global _server
    with _server_lock:
        server, _server = _server, None
    if server:
        server.shutdown()
        server.server_close()
