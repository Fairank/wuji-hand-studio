"""Opt-in Windows background capture, cropped immediately to four narrow edge tiles.

No image files, network transport, replay buffer or automatic startup. The full WGC
frame exists only in its callback. DesktopAPI exposes tiles only to our own WebView.
"""
import base64,ctypes,io,os,threading,time
from ctypes import wintypes as W
from PIL import Image

def capture_interval_ms(display_hz):
    """Match the display's current mode, capped at the requested 60–120 Hz range."""
    target=min(120,max(60,int(display_hz or 60)))
    return max(8,int(1000/target)),target

def continuous_capture_rate(times,now):
    """Changed-frame cadence for the current motion burst; silence is not slow video."""
    if not times or now-times[-1]>.1:return None,0
    count=len(times)
    return (round((count-1)/(times[-1]-times[0]),1) if count>1 and times[-1]>times[0] else None,count)

def crop_tiles(frame,rect,scale=1.):
    """rect is WebView's physical-pixel bounds relative to the capture source."""
    x,y,w,h=rect;scale=max(.75,min(4.,float(scale)))
    # Match the visible 17 CSS-pixel perimeter; never paint over the canvas.
    edge=max(10,round(17*scale));pad=round(14*scale)
    if w<200 or h<200 or w>12000 or h>8000:return []
    specs=[('top',0,0,w,edge),('bottom',0,h-edge,w,edge),
           ('left',0,edge,edge,h-2*edge),('right',w-edge,edge,edge,h-2*edge)]
    rows=[];fh,fw=frame.shape[:2]
    for name,left,top,width,height in specs:
        a,b,c,d=left-pad,top-pad,left+width+pad,top+height+pad
        sx0,sy0,sx1,sy1=max(0,x+a),max(0,y+b),min(fw,x+c),min(fh,y+d)
        if sx1<=sx0 or sy1<=sy0:continue
        # Copy only the edge, then release the WGC frame after this callback.
        import numpy as np
        tile=np.empty((d-b,c-a,3),dtype=np.uint8);tile[:]=245
        tile[sy0-y-b:sy1-y-b,sx0-x-a:sx1-x-a]=frame[sy0:sy1,sx0:sx1,2::-1]
        image=Image.fromarray(tile);buf=io.BytesIO();image.save(buf,format='JPEG',quality=83)
        rows.append(dict(side=name,rect=[left/scale,top/scale,width/scale,height/scale],
            texture_size=[c-a,d-b],pad=pad,url='data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode('ascii')))
    return rows

class Geometry:
    def __init__(self,hwnd,web_hwnd):
        self.hwnd,self.web_hwnd=hwnd,web_hwnd;self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.gdi=ctypes.WinDLL('gdi32',use_last_error=True);self.refresh_cache={}
        self.u.GetClientRect.argtypes=[W.HWND,ctypes.POINTER(W.RECT)];self.u.ClientToScreen.argtypes=[W.HWND,ctypes.POINTER(W.POINT)]
        self.u.IsIconic.argtypes=[W.HWND];self.u.GetDpiForWindow.argtypes=[W.HWND];self.u.GetDpiForWindow.restype=W.UINT
        self.u.GetWindowDisplayAffinity.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
        self.u.SetWindowDisplayAffinity.argtypes=[W.HWND,W.DWORD]
        self.u.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
        pid=W.DWORD();self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value!=os.getpid():raise ValueError('Refraction requires the owned workbench window')
        self.old_affinity=None
    def refresh_hz(self,handle):
        now=time.monotonic();cached=self.refresh_cache.get(handle)
        if cached and now-cached[0]<1:return cached[1]
        class MonitorInfoEx(ctypes.Structure):
            _fields_=[('cbSize',W.DWORD),('rcMonitor',W.RECT),('rcWork',W.RECT),('dwFlags',W.DWORD),('szDevice',ctypes.c_wchar*32)]
        info=MonitorInfoEx();info.cbSize=ctypes.sizeof(info)
        self.u.GetMonitorInfoW.argtypes=[W.HANDLE,ctypes.c_void_p]
        hz=60
        if self.u.GetMonitorInfoW(handle,ctypes.byref(info)):
            self.gdi.CreateDCW.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_void_p]
            self.gdi.CreateDCW.restype=W.HDC
            self.gdi.GetDeviceCaps.argtypes=[W.HDC,ctypes.c_int]
            self.gdi.DeleteDC.argtypes=[W.HDC]
            dc=self.gdi.CreateDCW('DISPLAY',info.szDevice,None,None)
            if dc:
                try:
                    reported=self.gdi.GetDeviceCaps(dc,116) # VREFRESH
                    if 24<=reported<=1000:hz=reported
                finally:self.gdi.DeleteDC(dc)
        self.refresh_cache[handle]=(now,hz)
        return hz
    def exclude(self):
        old=W.DWORD()
        if not self.u.GetWindowDisplayAffinity(self.hwnd,ctypes.byref(old)):raise RuntimeError('Cannot read capture exclusion')
        self.old_affinity=old.value
        if not self.u.SetWindowDisplayAffinity(self.hwnd,0x11):raise RuntimeError('Cannot exclude workbench from background capture')
    def restore(self):
        if self.old_affinity is not None:self.u.SetWindowDisplayAffinity(self.hwnd,self.old_affinity);self.old_affinity=None
    def bounds(self):
        if self.u.IsIconic(self.hwnd):return None
        point=W.POINT();rect=W.RECT()
        if not self.u.GetClientRect(self.web_hwnd,ctypes.byref(rect)) or not self.u.ClientToScreen(self.web_hwnd,ctypes.byref(point)):return None
        monitors=[];CB=ctypes.WINFUNCTYPE(W.BOOL,W.HANDLE,W.HDC,ctypes.POINTER(W.RECT),W.LPARAM)
        def collect(handle,dc,area,data):
            r=area.contents;monitors.append((handle,r.left,r.top,r.right,r.bottom));return True
        self.u.EnumDisplayMonitors.argtypes=[W.HDC,ctypes.c_void_p,CB,W.LPARAM]
        self.u.EnumDisplayMonitors(None,None,CB(collect),0)
        # The WGC library uses the same EnumDisplayMonitors enumeration (1-based).
        for index,(handle,l,t,r,b) in enumerate(monitors,1):
            if l<=point.x and t<=point.y and point.x+rect.right<=r and point.y+rect.bottom<=b:
                return (index,l,t,r-l,b-t,point.x-l,point.y-t,rect.right,rect.bottom,max(1,self.u.GetDpiForWindow(self.hwnd))/96.,self.refresh_hz(handle))
        return None # A straddling window uses Acrylic; no wrong-monitor pixels.

class Refraction:
    def __init__(self,hwnd,web_hwnd):
        self.geometry=Geometry(hwnd,web_hwnd);self.lock=threading.Lock();self.frames_ready=threading.Condition(self.lock);self.stop_event=threading.Event()
        self.thread=None;self.control=None;self.tiles=[];self.seq=0;self.reason='off';self.frame_at=0.;self.generation=0
        self.frame_times=[];self.encode_ms=[];self.capture_target_hz=60
        self.enabled=False;self.last_request=time.monotonic()
    def status(self):
        with self.lock:
            hz,count=continuous_capture_rate(self.frame_times,time.monotonic())
            return dict(enabled=self.enabled,active=bool(self.tiles),reason=self.reason,frames=self.seq,
                desktop_capture=self.enabled,storage='memory_only_edge_tiles',frame_age_s=round(time.monotonic()-self.frame_at,2) if self.frame_at else None,
                recent_capture_hz=hz,recent_capture_frames=count,capture_target_hz=self.capture_target_hz,
                mean_edge_encode_ms=round(sum(self.encode_ms)/len(self.encode_ms),2) if self.encode_ms else None)
    def start(self):
        if self.enabled:return self.status()
        self.geometry.exclude();self.enabled=True;self.stop_event.clear();self.reason='starting';self.last_request=time.monotonic()
        with self.lock:self.frame_times=[];self.encode_ms=[]
        self.thread=threading.Thread(target=self._run,name='Workbench edge backdrop',daemon=True);self.thread.start();return self.status()
    def _halt_capture(self):
        if self.control is not None:
            control,self.control=self.control,None
            control.stop()
    def _run(self):
        try:
            from windows_capture import WindowsCapture
            bounds=None
            while not self.stop_event.wait(.08):
                if time.monotonic()-self.last_request>3:break # Hidden/closed WebView no longer asks for pixels.
                current=self.geometry.bounds()
                if current!=bounds:
                    self.generation+=1;generation=self.generation;self._halt_capture();bounds=current
                    with self.frames_ready:
                        self.tiles=[];self.frame_times=[];self.encode_ms=[]
                        self.reason='window_outside_single_display' if not bounds else 'starting'
                        self.frames_ready.notify_all()
                    if bounds:
                        index,l,t,fw,fh,x,y,w,h,scale,refresh=bounds
                        interval,target=capture_interval_ms(refresh)
                        self.capture_target_hz=target
                        # This is an upper-rate request, not a measured frame-rate guarantee.
                        capture=WindowsCapture(cursor_capture=False,draw_border=True,minimum_update_interval=interval,monitor_index=index)
                        captured_bounds=bounds
                        def on_frame_arrived(frame,control,epoch=generation,b=captured_bounds):
                            if self.stop_event.is_set() or epoch!=self.generation:control.stop();return
                            if (frame.width,frame.height)!=(b[3],b[4]):return
                            began=time.perf_counter()
                            tiles=crop_tiles(frame.frame_buffer,(b[5],b[6],b[7],b[8]),b[9])
                            elapsed=(time.perf_counter()-began)*1000
                            with self.frames_ready:
                                if epoch!=self.generation:return
                                self.tiles=tiles;self.seq+=1;self.frame_at=time.monotonic();self.reason='active'
                                if self.frame_times and self.frame_at-self.frame_times[-1]>.1:self.frame_times=[]
                                self.frame_times.append(self.frame_at);self.encode_ms.append(elapsed)
                                if len(self.frame_times)>120:self.frame_times.pop(0)
                                if len(self.encode_ms)>120:self.encode_ms.pop(0)
                                self.frames_ready.notify_all()
                        def on_closed():pass
                        capture.event(on_frame_arrived);capture.event(on_closed)
                        self.control=capture.start_free_threaded()
                elif self.control is not None and self.control.is_finished():
                    self.reason='capture_ended';break
        except Exception as error:self.reason='capture_unavailable:'+type(error).__name__
        finally:
            try:self._halt_capture()
            finally:
                self.geometry.restore()
                with self.frames_ready:
                    self.tiles=[];self.enabled=False;self.frame_at=0.;self.frames_ready.notify_all()
    def read(self,last_seq=0):
        self.last_request=time.monotonic()
        with self.frames_ready:
            if self.enabled and last_seq==self.seq:self.frames_ready.wait(timeout=.25)
            hz,count=continuous_capture_rate(self.frame_times,time.monotonic())
            return dict(enabled=self.enabled,reason=self.reason,seq=self.seq,
                recent_capture_hz=hz,recent_capture_frames=count,capture_target_hz=self.capture_target_hz,
                tiles=self.tiles if self.enabled and last_seq!=self.seq else [])
    def stop(self):
        self.stop_event.set()
        with self.frames_ready:self.frames_ready.notify_all()
        if self.thread is not None:self.thread.join(timeout=2)
        with self.frames_ready:self.tiles=[];self.enabled=False;self.reason='off';self.frame_at=0.;self.frames_ready.notify_all()
        self.geometry.restore()
        return self.status()
