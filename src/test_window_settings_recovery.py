"""Corrupt window preferences cannot prevent startup; no service/window runs."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from contextlib import ExitStack

import native_desktop


class WindowSettingsRecoveryTests(unittest.TestCase):
    def start(self,content):
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            data=Path(folder)
            (data/'native-window.json').write_text(content,encoding='utf8')
            stack.enter_context(patch.object(native_desktop,'DATA',data))
            stack.enter_context(patch.dict(os.environ))
            os.environ.pop('WUJI_STUDIO_PORT',None)
            stack.enter_context(patch('console_server.PORT',8781))
            stack.enter_context(patch('console_server.ORIGIN','http://127.0.0.1:8781'))
            server=Mock()
            def bind(address,handler):
                if type(address[1]) is not int:raise TypeError('A port must be an integer')
                return server
            factory=stack.enter_context(patch('console_server.ConsoleHTTPServer',side_effect=bind))
            controller=stack.enter_context(patch('console_server.Controller',return_value=Mock()))
            stack.enter_context(patch('pose_view.PoseView',return_value=Mock()))
            thread=stack.enter_context(patch('native_desktop.threading.Thread',return_value=Mock()))
            host=native_desktop.NativeDesktop();host.start_service()
            return host.bounds,host.origin,factory.call_args.args[0]

    def test_nonobject_json_falls_back_without_breaking_startup(self):
        for value in ([],None,'old-format',12,True):
            with self.subTest(value=value):
                bounds,origin,address=self.start(json.dumps(value))
                self.assertEqual(bounds,{})
                self.assertEqual(origin,'http://127.0.0.1:8781')
                self.assertEqual(address,('127.0.0.1',8781))

    def test_fractional_saved_port_is_not_sent_to_the_server(self):
        bounds,origin,address=self.start('{"port":8781.0,"width":1200}')
        self.assertEqual(address,('127.0.0.1',8781));self.assertIs(type(address[1]),int)
        self.assertEqual(bounds['width'],1200)

    def test_invalid_saved_port_does_not_discard_usable_window_dimensions(self):
        for port in ('8792',True,999999,{'value':8792},[8792]):
            with self.subTest(port=port):
                bounds,origin,address=self.start(json.dumps(dict(port=port,width=1200,height=800)))
                self.assertEqual(address[1],8781);self.assertEqual(bounds['width'],1200)

    def test_valid_saved_port_and_dimensions_are_reused(self):
        bounds,origin,address=self.start('{"port":8792,"width":1200,"height":800}')
        self.assertEqual(address[1],8792);self.assertEqual(bounds['height'],800)

    def test_incomplete_json_uses_defaults(self):
        bounds,origin,address=self.start('{"port":')
        self.assertEqual(bounds,{});self.assertEqual(address[1],8781)


if __name__=='__main__':unittest.main()
