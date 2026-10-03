"""Public AppKit materials only; no screen capture or private compositor API."""
import threading


class WindowMaterial:
    def __init__(self, window):
        self.window = window
        self.mode = 'solid'
        self.requested = 'glass'
        self.core = None
        self.lock = threading.Lock()

    def apply(self, mode='glass', *, theme=None, glass_style=None):
        if mode not in ('glass', 'solid'):
            raise ValueError('Unknown window material')
        if theme is not None and theme not in ('system', 'light', 'dark'):
            raise ValueError('Unknown window appearance')
        if glass_style is not None and glass_style not in ('frosted', 'clear'):
            raise ValueError('Unknown glass style')
        try:
            import AppKit
            from Foundation import NSThread
            from PyObjCTools import AppHelper
        except ImportError:
            return dict(ok=False, mode='solid', external_backdrop=False, refraction=False,
                        desktop_capture=False, error='AppKit unavailable')
        done, cancelled, result = threading.Event(), threading.Event(), {}
        def change():
            if cancelled.is_set():
                return
            try:
                with self.lock:
                    from macos_material_core import WindowMaterial as CoreMaterial
                    if self.core is None:
                        self.core = CoreMaterial(self.window)
                    self.core.configure(theme, glass_style)
                    native = self.core.apply(mode)
                    self.requested = mode
                    chosen = {'macos_glass':'liquid-glass','vibrancy':'vibrancy'}.get(native.get('material'), 'solid')
                    self.mode = chosen
                    result.update(native, mode=chosen, external_backdrop=chosen != 'solid',
                                  refraction=None if chosen == 'liquid-glass' else False,
                                  desktop_capture=False, visual_acceptance_pending=True)
            except Exception as error:
                # Leave the content attached even if a material API is unavailable.
                if self.core is not None:
                    try:
                        self.core._rollback()
                        self.window.setOpaque_(True)
                        self.window.setBackgroundColor_(AppKit.NSColor.windowBackgroundColor())
                    except Exception:
                        pass
                self.mode = 'solid'
                result.update(ok=False, mode='solid', external_backdrop=False, refraction=False,
                              desktop_capture=False, error=str(error))
            finally:
                done.set()
        if NSThread.isMainThread():
            change()
        else:
            AppHelper.callAfter(change)
            if not done.wait(5):
                cancelled.set()
                return dict(ok=False, mode=self.mode, external_backdrop=self.mode != 'solid',
                            refraction=False, desktop_capture=False, error='AppKit update timed out')
        return result
