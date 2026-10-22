"""Ensure newly linked panels are actually delivered by the HTTP allowlist."""
from html.parser import HTMLParser
import io
import json
import re
from types import SimpleNamespace
from urllib.parse import urlsplit
import unittest
import console_server


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.paths=[]

    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='script' and 'src' in values:self.paths.append(values['src'])
        if tag=='link' and values.get('rel')=='stylesheet':self.paths.append(values['href'])


class WebAssetsTests(unittest.TestCase):
    def test_every_entrypoint_script_and_stylesheet_is_served(self):
        parser=Links();parser.feed((console_server.HERE/'web/index.html').read_text(encoding='utf-8'))
        for path in parser.paths:
            with self.subTest(path=path):
                responses=[]
                request=SimpleNamespace(path=path,allowed_host=lambda:True,
                    reply=lambda status,body,mime=None:responses.append((status,body,mime)))
                console_server.Handler.do_GET(request)
                self.assertEqual(responses[0][0],200)
                self.assertEqual(responses[0][1],(console_server.HERE/'web'/urlsplit(path).path.lstrip('/')).read_bytes())

    def response(self, body, mime):
        headers = {}
        request = SimpleNamespace(path='/', wfile=io.BytesIO(), send_response=lambda _status:None,
            send_header=lambda name,value:headers.__setitem__(name,value), end_headers=lambda:None)
        console_server.Handler.reply(request, 200, body, mime)
        return headers, request.wfile.getvalue()

    def test_html_style_nonce_matches_csp_is_fresh_and_preserves_script_connect_policy(self):
        previous = None
        for _ in range(2):
            headers, body = self.response(b'<!doctype html><html><head><title>test</title></head><body></body></html>',
                                          'text/html; charset=utf-8')
            matches = re.findall(rb'<meta name="workbench-style-nonce" content="([A-Za-z0-9_-]{32})">', body)
            self.assertEqual(len(matches), 1)
            nonce = matches[0].decode('ascii')
            self.assertNotEqual(nonce, previous)
            previous = nonce
            policy = headers['Content-Security-Policy']
            self.assertIn("style-src 'self' 'nonce-" + nonce + "';", policy)
            self.assertIn("script-src 'self';", policy)
            self.assertIn("connect-src 'self';", policy)
            self.assertNotIn('unsafe-inline', policy)
            self.assertNotIn('unsafe-eval', policy)
            self.assertEqual(int(headers['Content-Length']), len(body))
            self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_json_and_script_replies_do_not_inject_or_authorize_nonce(self):
        for body, mime in [(b'const marker="<head>";', 'text/javascript; charset=utf-8'),
                           ({'literal':'<head>'}, 'application/json; charset=utf-8')]:
            with self.subTest(mime=mime):
                headers, delivered = self.response(body, mime)
                expected = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
                self.assertEqual(delivered, expected)
                self.assertNotIn(b'workbench-style-nonce', delivered)
                self.assertNotIn('nonce-', headers['Content-Security-Policy'])
                self.assertIn("style-src 'self';", headers['Content-Security-Policy'])
                self.assertIn("script-src 'self';", headers['Content-Security-Policy'])

    def test_terminal_update_allowlist_is_exact_and_does_not_expose_vendor_tree(self):
        allowed = ('official_terminal.js', 'workbench_update.js', 'vendor/terminal/xterm.js',
                   'vendor/terminal/addon-fit.js', 'official_terminal.css', 'workbench_update.css',
                   'vendor/terminal/xterm.css')
        for name in allowed:
            with self.subTest(name=name):
                responses = []
                request = SimpleNamespace(path='/' + name + '?v=fixture', allowed_host=lambda:True,
                    reply=lambda status,body,mime=None:responses.append((status,body,mime)))
                console_server.Handler.do_GET(request)
                self.assertEqual(responses[0][0], 200)
                self.assertEqual(responses[0][1], (console_server.HERE/'web'/name).read_bytes())
                expected = 'text/javascript' if name.endswith('.js') else 'text/css'
                self.assertTrue(responses[0][2].startswith(expected))
        for path in ('/vendor/terminal/anything.js', '/vendor/terminal/xterm.js.map',
                     '/vendor/terminal/xterm.js/arbitrary', '/vendor/terminal/../official_tools.js',
                     '/vendor/terminal/%2e%2e/official_tools.js', '/vendor/terminal/package.json',
                     '/vendor/other/xterm.js', '/workbench_update_apply.ps1', '/official_terminal_manifest.json'):
            with self.subTest(path=path):
                responses = []
                request = SimpleNamespace(path=path, allowed_host=lambda:True,
                    reply=lambda status,body,mime=None:responses.append((status,body,mime)))
                console_server.Handler.do_GET(request)
                self.assertEqual(responses[0][0], 404)


if __name__=='__main__':unittest.main()
