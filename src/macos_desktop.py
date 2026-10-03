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

    def bind_script_bridge(self, window):
        if getattr(window, '_workbench_csp_bridge', False):
            return
        original_run_js = window.run_js
        bridge_factory = (RESOURCE / 'web/macos_bridge.js').read_text(encoding='utf-8')
        def run_script(script):
            # Closure-based exposed methods work with the strict page CSP;
            # pywebview's default API factory uses the Function constructor.
            if script.lstrip().startswith('window.pywebview._createApi('):
                script = bridge_factory + '\n' + script
            return original_run_js(script)
        window.run_js = run_script
        # Trusted, fixed host operations use WebKit's native evaluator, not
        # JavaScript eval(). Do not relax script-src for desktop integration.
        def evaluate(script, callback=None):
            from Foundation import NSJSONSerialization, NSJSONWritingFragmentsAllowed, NSThread
            from PyObjCTools import AppHelper
            from webview.platforms.cocoa import BrowserView
            if NSThread.isMainThread():
                raise RuntimeError('Synchronous JavaScript must run off the AppKit main thread')
            done, cancelled, result = threading.Event(), threading.Event(), {}
            def completed(value, error):
                try:
                    if error is not None:
                        raise RuntimeError(str(error.localizedDescription()))
                    if value is None:
                        result['value'] = None
                    else:
                        raw, failure = NSJSONSerialization.dataWithJSONObject_options_error_(
                            value, NSJSONWritingFragmentsAllowed, None)
                        if failure is not None:
                            raise RuntimeError(str(failure.localizedDescription()))
                        result['value'] = json.loads(bytes(raw))
                except Exception as failure:
                    result['error'] = failure
                finally:
                    done.set()
            def begin():
                if cancelled.is_set():
                    return
                try:
                    BrowserView.instances[window.uid].webview.evaluateJavaScript_completionHandler_(script, completed)
                except Exception as failure:
                    result['error'] = failure
                    done.set()
            AppHelper.callAfter(begin)
            if not done.wait(10):
                cancelled.set()
                raise TimeoutError('WKWebView did not complete the host request')
            if 'error' in result:
                raise result['error']
            value = result.get('value')
            if callback is not None:
                callback(value)
            return value
        window.evaluate_js = evaluate
        window._workbench_csp_bridge = True

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

    def set_window_appearance(self, theme):
        if theme not in ('system', 'light', 'dark'):
            raise ValueError('Unknown window appearance')
        if self.mac_material is None:
            from macos_material import WindowMaterial
            self.mac_material = WindowMaterial(self.window.native)
        self.material = self.mac_material.apply(self.mac_material.requested,
                                               theme=theme, glass_style='frosted')
        return self.material

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
        self.bind_script_bridge(window)

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
        from objc import super as objc_super
        self.start_service()
        origin = self.origin
        host=self
        class WorkbenchBrowserDelegate(cocoa.BrowserView.BrowserDelegate):
            def webView_decidePolicyForNavigationAction_decisionHandler_(self, view, action, handler):
                url = str(action.request().URL().absoluteString())
                parts=urlsplit(url)
                child=parts.scheme=='http' and parts.hostname=='127.0.0.1' and any(
                    row['port']==parts.port for row in host.server._fleet.children.values()) if hasattr(host.server,'_fleet') else False
                if url == 'about:blank' or same_origin(url, origin) or child:
                    objc_super(WorkbenchBrowserDelegate, self).webView_decidePolicyForNavigationAction_decisionHandler_(view, action, handler)
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
        self.bind_script_bridge(self.window)
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
            safely_closed = self.allow_close
            if hasattr(self.server,'_fleet'):
                result=self.server.fleet.close()
                if not result['ok']:
                    safely_closed = False
                    logging.error('Device workspace close unconfirmed: %s',result['error'])
            self.server.shutdown()
            self.server.server_close()
            if safely_closed:
                try:
                    from macos_runtime import stop_owned
                    stop_owned()
                except Exception:
                    logging.exception('Owned Linux controller did not stop cleanly')


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
