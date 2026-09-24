"""Ensure newly linked panels are actually delivered by the HTTP allowlist."""
from html.parser import HTMLParser
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


if __name__=='__main__':unittest.main()
