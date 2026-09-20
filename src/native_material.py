"""Windows compositor backdrop on OUR HWND only; never reads/captures desktop pixels.

Desktop Acrylic is backdrop blur/tint, not Apple's refractive Liquid Glass.
Documented DWM attributes require Windows 11 22621+. Older systems stay opaque.
"""
import ctypes
import sys

class Margins(ctypes.Structure):
    _fields_=[(x,ctypes.c_int) for x in ('left','right','top','bottom')]

def apply(form,enabled):
    from System.Drawing import Color,ColorTranslator
    build=sys.getwindowsversion().build
    result=dict(requested=bool(enabled),mode='opaque',supported=build>=22621,
                build=build,external_backdrop=False,refraction=False,desktop_capture=False)
    if build<22621:return result
    dwm=ctypes.WinDLL('dwmapi',use_last_error=True)
    set_attr=dwm.DwmSetWindowAttribute;set_attr.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint];set_attr.restype=ctypes.c_long
    get_attr=dwm.DwmGetWindowAttribute;get_attr.argtypes=set_attr.argtypes;get_attr.restype=ctypes.c_long
    extend=dwm.DwmExtendFrameIntoClientArea;extend.argtypes=[ctypes.c_void_p,ctypes.POINTER(Margins)];extend.restype=ctypes.c_long
    hwnd=int(form.Handle.ToInt64())
    value=ctypes.c_int(3 if enabled else 1) # DWMSBT_TRANSIENTWINDOW / NONE
    hr=int(set_attr(hwnd,38,ctypes.byref(value),4))
    frame_hr=int(extend(hwnd,ctypes.byref(Margins(*([-1]*4 if enabled and hr==0 else [0]*4)))))
    actual=ctypes.c_int();read_hr=int(get_attr(hwnd,38,ctypes.byref(actual),4))
    active=bool(enabled and hr==0 and frame_hr==0 and read_hr==0 and actual.value==3)
    # GDI black in an extended DWM frame leaves the compositor backdrop exposed.
    # WebView2 supplies transparent pixels only where CSS leaves the outer chrome clear.
    form.BackColor=Color.Black if active else ColorTranslator.FromHtml('#f5f5f7')
    form.webview.DefaultBackgroundColor=Color.Transparent if active else ColorTranslator.FromHtml('#f5f5f7')
    form.Invalidate()
    result.update(mode='desktop_acrylic' if active else 'opaque',external_backdrop=active,
                  attribute_hresult=hr,frame_hresult=frame_hr,read_hresult=read_hr,attribute=actual.value,
                  webview_alpha=int(form.webview.DefaultBackgroundColor.A))
    return result
