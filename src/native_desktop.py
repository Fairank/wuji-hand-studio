"""Windows desktop host. Embedded WebView2, owned local service, no browser fallback."""
import ctypes
import base64
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
from urllib.parse import urlsplit
from runtime_paths import DATA, RESOURCE, EDITION
from desktop_lifecycle import needs_confirmation, close_sessions

class Instance:
    def __init__(self,name,title):
        from ctypes import wintypes
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype=wintypes.HANDLE
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.kernel.CreateMutexW(None,False,'Local\\'+name)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        self.acquired=ctypes.get_last_error()!=183
        if not self.acquired:
            user=ctypes.WinDLL('user32',use_last_error=True)
            user.FindWindowW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR];user.FindWindowW.restype=wintypes.HWND
            user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int];user.SetForegroundWindow.argtypes=[wintypes.HWND]
            hwnd=user.FindWindowW(None,title)
            if not hwnd:hwnd=user.FindWindowW(None,'Hand Workbench')
            if hwnd:user.ShowWindow(hwnd,9);user.SetForegroundWindow(hwnd)
    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None

def same_origin(url,origin):
    p=urlsplit(url);expected=urlsplit(origin)
    return (p.scheme,p.hostname,p.port)==(expected.scheme,expected.hostname,expected.port)

class NativeDesktop:
    def __init__(self):
        self.server=None;self.window=None;self.viewer=None;self.allow_close=False;self.closing=False
        self.language='zh';self.session_lock=threading.Lock();self.origin='';self.bounds={}
        self.title='灵巧手工作台 · Hand Workbench'
        self.data_dir=DATA;self.edition=EDITION;self.inspect_lock=threading.Lock()

    def start_service(self):
        from console_server import ConsoleHTTPServer,Handler,Controller
        from pose_view import PoseView
        ports=list(range(8781,8801))
        explicit=os.environ.get('WUJI_STUDIO_PORT')
        if explicit:ports=[int(explicit)]
        else:
            try:
                saved=json.loads((DATA/'native-window.json').read_text(encoding='utf-8'))
                self.bounds=saved
                port=saved.get('port')
                if port in ports:ports.remove(port);ports.insert(0,port)
            except (OSError,ValueError,TypeError):pass
        for port in ports:
            try:self.server=ConsoleHTTPServer(('127.0.0.1',port),Handler);break
            except OSError:
                if explicit:raise
        if self.server is None:raise RuntimeError('No free local service port')
        self.origin=f'http://127.0.0.1:{port}'
        # Handler origin validation uses the actual listening port.
        import console_server
        console_server.PORT=port
        console_server.ORIGIN=self.origin
        self.server.controller=Controller();self.server.pose=PoseView(self.server.controller)
        self.server.desktop=self
        self.service_thread=threading.Thread(target=self.server.serve_forever,name='Wuji local service',daemon=True)
        self.service_thread.start()

    def info(self):
        from desktop_tools import OPERATIONS
        return dict(native=True,platform='windows',engine='WebView2',edition=EDITION['name'],version=EDITION['version'],
                    api_version=1,operations=OPERATIONS,data_dir=str(DATA),pid=os.getpid(),
                    ready=bool(self.window and self.window.events.loaded.is_set()))

    def inspect(self):
        if not self.info()['ready']:raise ValueError('Desktop window is still starting')
        return dict(ok=True,desktop=self.info(),ui=self.window.evaluate_js((RESOURCE/'web/desktop_inspect.js').read_text(encoding='utf-8')))

    def navigate(self,page):
        self.window.evaluate_js('location.hash='+json.dumps('#'+page))
        return dict(ok=True,page=page)

    def appearance(self,key,value):
        self.window.evaluate_js('window.WujiDesktop.setAppearance('+json.dumps(key)+','+json.dumps(value)+')')
        return dict(ok=True)

    def menu(self,opened):
        self.window.evaluate_js('window.WujiDesktop.setMenu('+json.dumps(opened)+')')
        return dict(ok=True)

    def capture(self):
        """Capture only this application's WebView, never the desktop."""
        if not self.info()['ready']:raise ValueError('Desktop window is still starting')
        from System import Action
        from System.IO import MemoryStream
        from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
        with self.inspect_lock:
            stream=MemoryStream();tasks=[]
            try:
                def begin():
                    tasks.append(self.window.native.webview.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream))
                self.window.native.Invoke(Action(begin))
                if not tasks[0].Wait(10000):
                    # Keep the stream alive until WebView finishes its asynchronous write.
                    pending=stream
                    def finish():
                        try:tasks[0].Wait()
                        finally:pending.Dispose()
                    threading.Thread(target=finish,daemon=True).start()
                    stream=None
                    raise TimeoutError('Application capture timed out')
                return dict(ok=True,mime='image/png',scope='application_webview',image=base64.b64encode(bytes(stream.ToArray())).decode('ascii'))
            finally:
                if stream is not None:stream.Dispose()

    def open_viewer(self):
        import webview
        if self.viewer is not None:
            try:self.viewer.restore();self.viewer.show();return dict(ok=True)
            except Exception:self.viewer=None
        self.viewer=webview.create_window('MuJoCo · Hand Workbench',self.origin+'/viewer?desktop=1',
                  width=780,height=650,min_size=(480,360),background_color='#f5f5f7')
        viewer=self.viewer
        def cleared():self.viewer=None
        viewer.events.closed+=cleared
        viewer.events.loaded+=lambda:self.secure_window(viewer)
        return dict(ok=True)

    def open_data_folder(self):
        os.startfile(str(DATA));return dict(ok=True)

    def select_key_file(self):
        import webview
        paths=self.window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=False,file_types=('SSH key (*.*)',))
        return str(paths[0]) if paths else None

    def set_language(self,lang):
        if lang not in ('zh','en'):raise ValueError('Unsupported language')
        self.language=lang
        self.window.set_title('Hand Workbench' if lang=='en' else '灵巧手工作台 · Hand Workbench')
        return dict(ok=True)

    def import_model_dialog(self):
        import webview
        from model_pack import install
        paths=self.window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=False,file_types=('Model pack (*.zip)',))
        return install(paths[0]) if paths else dict(ok=False,cancelled=True)

    def request_close(self):
        self.window.destroy();return dict(ok=True)

    def stop_and_close(self,require_idle=False):
        if not self.session_lock.acquire(blocking=False):return dict(ok=False,error='正在结束会话 / Closing session')
        try:
            from desktop_tools import idle
            with self.server.controller.lock:
                if require_idle and not idle(self.server.controller.snapshot(),self.server.controller.doctor.snapshot()):
                    return dict(ok=False,error='Disconnect devices and finish active work first')
                self.server.controller.desktop_closing=True
            result=close_sessions(self.server.controller)
            if result['ok']:
                self.allow_close=True
                threading.Timer(.15,self.window.destroy).start()
            return result
        except Exception:
            logging.exception('Desktop exit failed')
            return dict(ok=False,error='连接尚未结束，请检查工作台。 / Session has not closed; check the workbench.')
        finally:
            if not self.allow_close:self.server.controller.desktop_closing=False
            self.session_lock.release()

    def on_closing(self):
        if self.allow_close:return True
        if self.closing:return False
        self.closing=True
        def decide():
            try:
                state=self.server.controller.snapshot()
                busy=state.get('parameter_sync',{}).get('busy') or self.server.controller.doctor.snapshot().get('running')
                if needs_confirmation(state) or busy:
                    self.window.evaluate_js('window.WujiDesktop?.confirmClose()')
                else:self.stop_and_close()
            finally:self.closing=False
        threading.Thread(target=decide,daemon=True).start()
        return False

    def secure_window(self,window):
        """Keep the native bridge on the owned local origin; docs open externally."""
        from System import Action
        def bind():
            core=window.native.webview.CoreWebView2
            if getattr(window,'_wuji_navigation_guard',False):return
            def guard(sender,args):
                if not same_origin(str(args.Uri),self.origin):args.Cancel=True
            core.NavigationStarting+=guard
            window._wuji_navigation_guard=guard
            core.Settings.AreDefaultContextMenusEnabled=False
            core.Settings.AreDevToolsEnabled=False
            core.Settings.IsStatusBarEnabled=False
        window.native.Invoke(Action(bind))

    def save_bounds(self):
        try:
            state=dict(width=max(960,min(2560,self.window.width)),height=max(680,min(1600,self.window.height)),port=self.server.server_port)
            (DATA/'native-window.json').write_text(json.dumps(state),encoding='utf-8')
        except Exception:logging.exception('Could not persist window size')

    def run(self):
        import webview
        self.start_service()
        width=self.bounds.get('width',1320);height=self.bounds.get('height',900)
        if type(width) is not int or type(height) is not int:width,height=1320,900
        width=max(960,min(width,2560));height=max(680,min(height,1600))
        # Native caption and resizing preserve Windows snapping and keyboard controls.
        self.window=webview.create_window(self.title,self.origin+'/?desktop=1#library',js_api=DesktopAPI(self),
            width=width,height=height,min_size=(960,680),background_color='#f5f5f7',text_select=True,zoomable=False)
        self.window.events.closing+=self.on_closing
        self.window.events.loaded+=lambda:self.secure_window(self.window)
        self.window.events.resized+=self.save_bounds
        def closed():
            if self.viewer is not None:self.viewer.destroy()
            self.server.shutdown()
        self.window.events.closed+=closed
        webview.settings['ALLOW_DOWNLOADS']=True
        webview.settings['ALLOW_FILE_URLS']=False
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=True
        try:
            webview.start(gui='edgechromium',private_mode=False,storage_path=str(DATA/'webview'),
                          icon=str(RESOURCE/'web/favicon.ico'))
        finally:
            self.server.shutdown();self.server.server_close()

class DesktopAPI:
    """Small explicit UI bridge. No arbitrary files, shell commands or motor API."""
    def __init__(self,host):self._host=host
    def info(self):return self._host.info()
    def open_viewer(self):return self._host.open_viewer()
    def open_data_folder(self):return self._host.open_data_folder()
    def select_key_file(self):return self._host.select_key_file()
    def set_language(self,lang):return self._host.set_language(lang)
    def import_model_dialog(self):return self._host.import_model_dialog()
    def request_close(self):return self._host.request_close()
    def stop_and_close(self):return self._host.stop_and_close()

def main():
    app=NativeDesktop();name='HandWorkbench'
    instance=Instance(name,app.title)
    try:
        if not instance.acquired:return 0
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Fairank.'+name)
        logging.basicConfig(filename=str(DATA/'desktop.log'),encoding='utf-8',level=logging.INFO)
        app.run();return 0
    except Exception:
        logging.exception('Native desktop startup failed')
        ctypes.windll.user32.MessageBoxW(None,'桌面窗口启动失败。请确认已安装 Microsoft WebView2 Runtime，并查看 desktop.log。\nDesktop startup failed. Check WebView2 Runtime and desktop.log.',app.title,0x10)
        return 1
    finally:instance.close()
