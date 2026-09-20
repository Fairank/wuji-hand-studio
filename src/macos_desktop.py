"""Native Cocoa/WKWebView host for the same Hand Workbench, without browser fallback."""
import base64
import json
import logging
import os
import subprocess
import threading
from runtime_paths import DATA, RESOURCE
from native_desktop import NativeDesktop, DesktopAPI, same_origin


class MacDesktop(NativeDesktop):
    def __init__(self):
        super().__init__()
        self.mac_material = None

    def info(self):
        result = super().info()
        result.update(platform='macos', engine='WKWebView', external_capture_supported=False)
        return result

    def set_window_material(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Expected boolean material preference')
        from macos_material import WindowMaterial
        if self.mac_material is None:
            self.mac_material = WindowMaterial(self.window.native)
        self.material = self.mac_material.apply('glass' if enabled else 'solid')
        return self.material

    def set_external_refraction(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Expected boolean refraction preference')
        return dict(enabled=False, active=False, supported=False,
                    reason='macOS uses native system material; desktop capture is not used')

    def install_runtime_components(self):
        from managed_runtime import start_install
        from desktop_tools import idle
        if not idle(self.server.controller.snapshot(), self.server.controller.doctor.snapshot()):
            raise ValueError('Disconnect devices before installing the controller')
        return start_install()

    def network_status(self, address=''):
        from macos_network import snapshot
        selected = self.server.controller.snapshot()['device_profile']
        return snapshot(address, selected['side'])

    def configure_device_network(self, adapter_id, address=''):
        from macos_network import configure
        state = self.server.controller.snapshot()
        if state['connection'] not in ('disconnected', 'error') or state['hardware'].get('active') or self.server.controller.glove.busy:
            raise ValueError('Disconnect devices before changing the network')
        return configure(adapter_id, address, state['device_profile']['side'])

    def open_data_folder(self):
        subprocess.Popen(['/usr/bin/open', str(DATA)])
        return dict(ok=True)

    def secure_window(self, window):
        # The Cocoa delegate is installed BEFORE the first navigation in run().
        return None

    def capture(self):
        """WKWebView snapshot only, not WindowServer or other applications."""
        if not self.info()['ready']:
            raise ValueError('Desktop is still starting')
        from PyObjCTools import AppHelper
        from webview.platforms.cocoa import BrowserView
        import AppKit
        import WebKit
        done, result = threading.Event(), {}
        def captured(image, error):
            try:
                if error or image is None:
                    raise RuntimeError(str(error or 'Empty WKWebView snapshot'))
                rep = AppKit.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
                result.update(ok=True, mime='image/png', scope='application_webview',
                              image=base64.b64encode(bytes(data)).decode('ascii'))
            except Exception as e:
                result.update(ok=False, error=str(e))
            finally:
                done.set()
        def begin():
            try:
                view = BrowserView.instances[self.window.uid].webview
                view.takeSnapshotWithConfiguration_completionHandler_(WebKit.WKSnapshotConfiguration.new(), captured)
            except Exception as e:
                result.update(ok=False, error=str(e)); done.set()
        with self.inspect_lock:
            AppHelper.callAfter(begin)
            if not done.wait(10):
                raise TimeoutError('Own-window capture timed out')
            return result

    def run(self):
        import webview
        from webview.platforms import cocoa
        from urllib.parse import urlsplit
        import webbrowser
        self.start_service()
        origin = self.origin
        class WorkbenchBrowserDelegate(cocoa.BrowserView.BrowserDelegate):
            def webView_decidePolicyForNavigationAction_decisionHandler_(self, view, action, handler):
                url = str(action.request().URL().absoluteString())
                if url == 'about:blank' or same_origin(url, origin):
                    super().webView_decidePolicyForNavigationAction_decisionHandler_(view, action, handler)
                else:
                    handler(0)
            def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(self, view, config, action, features):
                url = str(action.request().URL().absoluteString())
                if urlsplit(url).scheme == 'https':
                    webbrowser.open(url)
                return None
        cocoa.BrowserView.BrowserDelegate = WorkbenchBrowserDelegate
        width = self.bounds.get('width', 1320)
        height = self.bounds.get('height', 900)
        if type(width) is not int or type(height) is not int:
            width, height = 1320, 900
        self.window = webview.create_window(self.title, origin + '/?desktop=1#library',
            js_api=DesktopAPI(self), width=max(960, min(width, 2560)), height=max(680, min(height, 1600)),
            min_size=(960, 680), background_color='#f5f5f7', transparent=True, text_select=True,
            zoomable=False)
        self.window.events.closing += self.on_closing
        self.window.events.resized += self.save_bounds
        def closed():
            if self.viewer is not None:
                self.viewer.destroy()
        self.window.events.closed += closed
        webview.settings['ALLOW_DOWNLOADS'] = True
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        try:
            webview.start(gui='cocoa', private_mode=False, storage_path=str(DATA / 'webview-macos'))
        finally:
            self.server.shutdown()
            self.server.server_close()


def main():
    import fcntl
    DATA.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(DATA / 'desktop.log'), encoding='utf-8', level=logging.INFO)
    # The OS app bundle opens one instance; this also covers a source launch.
    with (DATA / 'desktop-macos.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            MacDesktop().run()
            return 0
        except Exception:
            logging.exception('Mac desktop startup failed')
            raise
