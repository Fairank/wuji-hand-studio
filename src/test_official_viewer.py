"""Independent, synthetic tests for the device-free static Viewer origin.

No real Viewer package, SDK, device or external service is used. Each module
instance receives a temporary runtime_paths before its source is imported.
"""
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


class ViewerFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='viewer-static-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.folder = self.root / 'official-viewer'
        self.folder.mkdir()
        self.font_links = [
            '<link rel="preconnect" href="https://fonts.googleapis.com">',
            "<LINK HREF='https://fonts.gstatic.com' rel='preconnect' crossorigin>",
            '<link href="https://fonts.googleapis.com/css2?family=Roboto" rel="stylesheet">',
        ]
        html = ('<!doctype html><html><head>' + ''.join(self.font_links) +
                '<link rel="icon" href="./favicon.svg">'
                '<link href="./icon.png" rel="apple-touch-icon">'
                '<link rel="stylesheet" href="./viewer.css">'
                '</head><body><div id="renderer"></div>'
                '<script type="module">import {start} from "./viewer.js";'
                'start({worker:"./worker.js",wasm:"./viewer_bg.wasm"});</script>'
                '</body></html>')
        self.originals = {
            'index.html': (html.encode('utf-8'), 'text/html; charset=utf-8'),
            'viewer.js': (b'export const start = cfg => cfg; // original renderer\n', 'text/javascript'),
            'viewer_bg.wasm': (b'\0asm\x01\0\0\0\x00\x03test', 'application/wasm'),
            'worker.js': (b'self.onmessage = e => postMessage(e.data);\n', 'text/javascript'),
            'viewer.css': (b'#renderer { color: #123456; }\n', 'text/css'),
            'favicon.svg': (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', 'image/svg+xml'),
            'icon.png': (b'\x89PNG\r\n\x1a\nsynthetic-icon', 'image/png'),
        }
        self.spec = {'version': '0.36.0', 'files': {}}
        for name, (body, mime) in self.originals.items():
            (self.folder / name).write_bytes(body)
            self.spec['files'][name] = dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), mime=mime)
        self.write_manifest()
        module_name = '_isolated_official_viewer_' + self.root.name.replace('-', '_')
        source = Path(__file__).with_name('official_viewer.py')
        spec = importlib.util.spec_from_file_location(module_name, source)
        self.viewer = importlib.util.module_from_spec(spec)
        previous_runtime = sys.modules.get('runtime_paths')
        with patch.dict(sys.modules, {'runtime_paths': SimpleNamespace(RESOURCE=self.root, PORT=18781)}):
            spec.loader.exec_module(self.viewer)
        self.assertIs(sys.modules.get('runtime_paths'), previous_runtime)
        self.addCleanup(self.viewer.close)

    def write_manifest(self):
        (self.root / 'official_viewer_manifest.json').write_text(json.dumps(self.spec), encoding='utf-8')

    def start_server(self):
        state = self.viewer.snapshot()
        self.assertTrue(state['available'], state.get('error'))
        self.assertFalse(state['device_connected'])
        self.assertEqual(state['source'], 'original-rerun-web-viewer')
        parsed = urlsplit(state['url'])
        self.assertEqual((parsed.scheme, parsed.hostname, parsed.path), ('http', '127.0.0.1', '/index.html'))
        return state, parsed.port

    def request(self, port, target='/', *, method='GET', host=None, missing_host=False, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            if missing_host:
                connection.putrequest(method, target, skip_host=True)
                connection.endheaders()
            else:
                connection.request(method, target, body=body, headers={'Host': host or f'127.0.0.1:{port}'})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()


class AssetTests(ViewerFixture):
    def test_runtime_paths_isolated_before_import(self):
        self.assertEqual(self.viewer.RESOURCE, self.root)
        self.assertEqual(self.viewer.FOLDER, self.folder)
        self.assertEqual(self.viewer.MANIFEST, self.root / 'official_viewer_manifest.json')
        self.assertIsNone(self.viewer._server)
        self.assertLess(sum(len(body) for body, _ in self.originals.values()), 4096)

    def test_unknown_and_traversal_resources_rejected_even_if_manifest_lists_them(self):
        names = ('unknown.js', '../outside.txt', '..\\outside.txt', '/index.html', './viewer.js', 'nested/worker.js')
        for name in names[1:]:
            self.spec['files'][name] = dict(self.spec['files']['viewer.js'])
        self.write_manifest()
        (self.root / 'outside.txt').write_text('must not be served', encoding='utf-8')
        for name in names:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.viewer.asset(name)

    def test_binary_renderer_and_other_static_resources_are_byte_identical(self):
        for name, (body, mime) in self.originals.items():
            if name == 'index.html':
                continue
            with self.subTest(name=name):
                self.assertEqual(self.viewer.asset(name), (body, mime))
                self.assertEqual((self.folder / name).read_bytes(), body)

    def test_html_removes_only_external_fonts_preserving_icons_and_renderer(self):
        expected = self.originals['index.html'][0].decode('utf-8')
        for link in self.font_links:
            expected = expected.replace(link, '')
        body, mime = self.viewer.asset('index.html')
        self.assertEqual(body, expected.encode('utf-8'))
        self.assertEqual(mime, 'text/html; charset=utf-8')
        self.assertIn(b'import {start} from "./viewer.js"', body)
        self.assertIn(b'rel="icon"', body)
        self.assertIn(b'rel="apple-touch-icon"', body)
        self.assertEqual((self.folder / 'index.html').read_bytes(), self.originals['index.html'][0])

    def test_mismatching_digest_is_rejected_before_serving(self):
        self.spec['files']['viewer.js']['sha256'] = '0' * 64
        self.write_manifest()
        with self.assertRaises(ValueError):
            self.viewer.asset('viewer.js')

    def test_mismatching_size_is_rejected_even_with_correct_digest(self):
        self.spec['files']['viewer.js']['bytes'] += 1
        self.write_manifest()
        with self.assertRaises(ValueError):
            self.viewer.asset('viewer.js')

    def test_changed_manifest_digest_invalidates_successful_verification(self):
        self.viewer.asset('viewer.js')
        self.spec['files']['viewer.js']['sha256'] = '0' * 64
        self.write_manifest()
        with self.assertRaises(ValueError):
            self.viewer.asset('viewer.js')

    def test_changed_manifest_size_invalidates_successful_verification(self):
        self.viewer.asset('viewer.js')
        self.spec['files']['viewer.js']['bytes'] += 1
        self.write_manifest()
        with self.assertRaises(ValueError):
            self.viewer.asset('viewer.js')

    def test_asset_content_tampering_after_verification_is_rejected(self):
        self.viewer.asset('viewer.js')
        target = self.folder / 'viewer.js'
        stamp = target.stat()
        body = self.originals['viewer.js'][0]
        target.write_bytes(b'X' + body[1:])
        os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 2_000_000_000))
        with self.assertRaises(ValueError):
            self.viewer.asset('viewer.js')

    def test_content_tampering_with_preserved_size_and_mtime_is_rejected(self):
        self.viewer.asset('viewer.js')
        target = self.folder / 'viewer.js'
        stamp = target.stat()
        body = self.originals['viewer.js'][0]
        target.write_bytes(b'X' + body[1:])
        os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual((target.stat().st_size, target.stat().st_mtime_ns), (stamp.st_size, stamp.st_mtime_ns))
        with self.assertRaises(ValueError):
            self.viewer.asset('viewer.js')

    def test_missing_resource_is_rejected(self):
        (self.folder / 'viewer_bg.wasm').unlink()
        with self.assertRaises(ValueError):
            self.viewer.asset('viewer_bg.wasm')

    def test_symlink_metadata_is_rejected_before_reading_asset(self):
        # Windows does not guarantee symlink-creation rights. Exercise the
        # actual Path guard without requesting elevation or creating a link.
        with patch.object(Path, 'is_symlink', return_value=True), patch.object(Path, 'read_bytes') as read:
            with self.assertRaises(ValueError):
                self.viewer.asset('viewer.js')
            read.assert_not_called()

    def test_invalid_manifest_returns_recoverable_snapshot_error(self):
        for invalid in ('{', json.dumps({'version': 'unknown', 'files': {}}), json.dumps({'version': '0.36.0', 'files': []})):
            with self.subTest(manifest=invalid):
                self.viewer.MANIFEST.write_text(invalid, encoding='utf-8')
                state = self.viewer.snapshot()
                self.assertFalse(state['available'])
                self.assertFalse(state['device_connected'])
                self.assertIsNone(state['url'])
                self.assertTrue(state['error'])
                self.assertIsNone(self.viewer._server)
        self.write_manifest()
        state, _ = self.start_server()
        self.assertIsNone(state['error'])


class StaticOriginTests(ViewerFixture):
    def test_snapshot_uses_loopback_ephemeral_static_origin_and_reuses_it(self):
        real_server = self.viewer.ThreadingHTTPServer
        with patch.object(self.viewer, 'ThreadingHTTPServer', wraps=real_server) as create:
            state, port = self.start_server()
            self.assertGreater(port, 0)
            self.assertEqual(create.call_args.args[0], ('127.0.0.1', 0))
            self.assertEqual(self.viewer.snapshot()['url'], state['url'])
            create.assert_called_once()

    def test_only_exact_loopback_host_and_port_are_accepted(self):
        _, port = self.start_server()
        for host in (f'127.0.0.1:{port}', f'localhost:{port}'):
            with self.subTest(host=host):
                self.assertEqual(self.request(port, '/viewer.js', host=host)[0], 200)
        for host in ('example.invalid', '127.0.0.1', 'localhost', f'localhost:{port + 1}', f'127.0.0.1:{port}.example.invalid', '[::1]:1234'):
            with self.subTest(host=host):
                self.assertEqual(self.request(port, '/viewer.js', host=host)[0], 403)
        self.assertEqual(self.request(port, '/viewer.js', missing_host=True)[0], 403)

    def test_html_clears_only_its_origin_storage_and_sends_static_headers(self):
        _, port = self.start_server()
        for target in ('/', '/index.html'):
            with self.subTest(target=target):
                code, headers, body = self.request(port, target)
                self.assertEqual(code, 200)
                self.assertEqual(headers['Clear-Site-Data'], '"storage"')
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(int(headers['Content-Length']), len(body))

    def test_csp_has_only_static_same_origin_connections_and_local_parent(self):
        _, port = self.start_server()
        code, headers, _ = self.request(port, '/index.html')
        self.assertEqual(code, 200)
        policy = {}
        for raw in headers['Content-Security-Policy'].split(';'):
            tokens = raw.split()
            if tokens:
                policy[tokens[0]] = tokens[1:]
        self.assertEqual(policy['default-src'], ["'none'"])
        self.assertEqual(policy['connect-src'], ["'self'"])
        self.assertEqual(policy['font-src'], ["'self'"])
        self.assertEqual(policy['frame-ancestors'], ['http://127.0.0.1:18781', 'http://localhost:18781'])
        self.assertEqual(policy['base-uri'], ["'none'"])
        self.assertEqual(policy['form-action'], ["'none'"])
        self.assertIn("'wasm-unsafe-eval'", policy['script-src'])
        self.assertNotIn("'unsafe-eval'", policy['script-src'])
        self.assertNotIn('https:', headers['Content-Security-Policy'])
        self.assertNotIn('*', headers['Content-Security-Policy'])

    def test_static_resources_preserve_binary_bytes_without_clearing_storage(self):
        _, port = self.start_server()
        for name, (body, mime) in self.originals.items():
            if name == 'index.html':
                continue
            with self.subTest(name=name):
                code, headers, served = self.request(port, '/' + name)
                self.assertEqual(code, 200)
                self.assertEqual(served, body)
                self.assertEqual(headers['Content-Type'], mime)
                self.assertEqual(int(headers['Content-Length']), len(body))
                self.assertNotIn('Clear-Site-Data', headers)

    def test_unknown_traversal_and_workbench_api_paths_are_not_exposed(self):
        _, port = self.start_server()
        for target in ('/api/state', '/api/action?name=hardware_start', '/api/official-viewer', '/official_viewer_manifest.json', '/missing.js', '/../outside.txt', '/%2e%2e%2foutside.txt', '/..%5coutside.txt'):
            with self.subTest(target=target):
                self.assertEqual(self.request(port, target)[0], 404)

    def test_post_cannot_reach_an_action_or_change_static_resources(self):
        _, port = self.start_server()
        for target in ('/api/action', '/index.html'):
            with self.subTest(target=target):
                code, _, _ = self.request(port, target, method='POST', body=b'{"name":"hardware_start"}')
                self.assertIn(code, {404, 405, 501})
        for name, (body, _) in self.originals.items():
            self.assertEqual((self.folder / name).read_bytes(), body)

    def test_url_query_does_not_change_served_html_or_resource_selection(self):
        _, port = self.start_server()
        plain = self.request(port, '/index.html')
        queried = self.request(port, '/index.html?url=https%3A%2F%2Fexample.invalid%2Fdata.rrd')
        self.assertEqual(queried[0], 200)
        self.assertEqual(plain[2], queried[2])
        self.assertEqual(queried[1]['Clear-Site-Data'], '"storage"')

    def test_snapshot_asset_failure_can_recover_without_restarting_module(self):
        state, _ = self.start_server()
        target = self.folder / 'viewer_bg.wasm'
        target.write_bytes(b'broken')
        broken = self.viewer.snapshot()
        self.assertFalse(broken['available'])
        self.assertIsNone(broken['url'])
        self.assertTrue(broken['error'])
        target.write_bytes(self.originals['viewer_bg.wasm'][0])
        recovered = self.viewer.snapshot()
        self.assertTrue(recovered['available'], recovered['error'])
        self.assertEqual(recovered['url'], state['url'])
        self.assertFalse(recovered['device_connected'])

    def test_close_is_idempotent_and_a_new_snapshot_can_restart_static_server(self):
        self.start_server()
        first = self.viewer._server
        self.viewer.close()
        self.assertIsNone(self.viewer._server)
        self.viewer.close()
        self.start_server()
        self.assertIsNot(self.viewer._server, first)


class DynamicParentPortTests(ViewerFixture):
    def frame_ancestors(self, state):
        self.assertTrue(state['available'], state.get('error'))
        port = urlsplit(state['url']).port
        code, headers, _ = self.request(port, '/index.html')
        self.assertEqual(code, 200)
        policy = headers['Content-Security-Policy']
        self.assertNotIn('*', policy)
        for directive in policy.split(';'):
            tokens = directive.split()
            if tokens and tokens[0] == 'frame-ancestors':
                return tokens[1:]
        self.fail('Missing frame-ancestors policy')

    def test_explicit_native_parent_port_is_used_instead_of_module_default(self):
        self.assertEqual(self.viewer.PORT, 18781)
        state = self.viewer.snapshot(parent_port=8783)
        self.assertEqual(self.frame_ancestors(state), ['http://127.0.0.1:8783', 'http://localhost:8783'])
        self.assertEqual(self.viewer._server.parent_ports, {8783})

    def test_multiple_parent_ports_reuse_one_static_origin_without_wildcards(self):
        first = self.viewer.snapshot(parent_port=8783)
        server = self.viewer._server
        second = self.viewer.snapshot(parent_port=8785)
        repeated = self.viewer.snapshot(parent_port=8783)
        self.assertEqual(first['url'], second['url'])
        self.assertEqual(first['url'], repeated['url'])
        self.assertIs(self.viewer._server, server)
        self.assertEqual(server.parent_ports, {8783, 8785})
        self.assertEqual(self.frame_ancestors(second), [
            'http://127.0.0.1:8783', 'http://localhost:8783',
            'http://127.0.0.1:8785', 'http://localhost:8785',
        ])

    def test_default_parent_is_registered_only_when_default_snapshot_is_requested(self):
        explicit = self.viewer.snapshot(parent_port=8783)
        default = self.viewer.snapshot()
        self.assertEqual(explicit['url'], default['url'])
        self.assertEqual(self.viewer._server.parent_ports, {8783, 18781})
        self.assertEqual(self.frame_ancestors(default), [
            'http://127.0.0.1:8783', 'http://localhost:8783',
            'http://127.0.0.1:18781', 'http://localhost:18781',
        ])

    def test_invalid_parent_types_and_port_ranges_do_not_start_a_server(self):
        for value in (True, False, '8783', '', 8783.0, [], {}, -1, 0, 1023, 65536, 999999):
            with self.subTest(parent_port=value):
                state = self.viewer.snapshot(parent_port=value)
                self.assertFalse(state['available'])
                self.assertIsNone(state['url'])
                self.assertIn('Invalid Workbench parent port', state['error'])
                self.assertIsNone(self.viewer._server)

    def test_invalid_parent_does_not_expand_a_running_servers_trusted_ports(self):
        first = self.viewer.snapshot(parent_port=8783)
        server = self.viewer._server
        for value in (True, '8785', 65536):
            with self.subTest(parent_port=value):
                self.assertFalse(self.viewer.snapshot(parent_port=value)['available'])
                self.assertIs(self.viewer._server, server)
                self.assertEqual(server.parent_ports, {8783})
        self.assertEqual(self.frame_ancestors(first), ['http://127.0.0.1:8783', 'http://localhost:8783'])

    def test_valid_parent_port_boundaries_remain_exact_local_origins(self):
        self.assertTrue(self.viewer.snapshot(parent_port=1024)['available'])
        state = self.viewer.snapshot(parent_port=65535)
        self.assertEqual(self.frame_ancestors(state), [
            'http://127.0.0.1:1024', 'http://localhost:1024',
            'http://127.0.0.1:65535', 'http://localhost:65535',
        ])


if __name__ == '__main__':
    unittest.main()
