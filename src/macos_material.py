"""Public AppKit materials only; no screen capture or private compositor API."""
import threading


class WindowMaterial:
    def __init__(self, window):
        self.window = window
        self.original = None
        self.wrapper = None
        self.mode = 'solid'
        self.lock = threading.Lock()

    def apply(self, mode='glass'):
        if mode not in ('glass', 'solid'):
            raise ValueError('Unknown window material')
        try:
            import AppKit
            from Foundation import NSThread, NSClassFromString
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
                    if self.original is None:
                        self.original = self.window.contentView()
                    reduced = AppKit.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceTransparency()
                    glass_class = NSClassFromString('NSGlassEffectView')
                    selected = 'solid' if reduced or mode == 'solid' else 'liquid-glass' if glass_class else 'vibrancy'
                    if selected != self.mode:
                        # Restore before switching wrappers; repeated calls never nest them.
                        self.original.removeFromSuperview()
                        if self.wrapper is not None and self.mode == 'liquid-glass':
                            self.wrapper.setContentView_(None)
                        self.window.setContentView_(self.original)
                        self.wrapper = None
                        if selected != 'solid':
                            wrapper = (glass_class if selected == 'liquid-glass' else AppKit.NSVisualEffectView).alloc().initWithFrame_(self.original.frame())
                            wrapper.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
                            if selected == 'liquid-glass':
                                wrapper.setCornerRadius_(16.0)
                                wrapper.setContentView_(self.original)
                            else:
                                wrapper.setMaterial_(AppKit.NSVisualEffectMaterialUnderWindowBackground)
                                wrapper.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
                                wrapper.setState_(AppKit.NSVisualEffectStateActive)
                                wrapper.addSubview_(self.original)
                            self.window.setContentView_(wrapper)
                            self.wrapper = wrapper
                        self.mode = selected
                    self.window.setOpaque_(selected == 'solid')
                    self.window.setBackgroundColor_(AppKit.NSColor.windowBackgroundColor() if selected == 'solid' else AppKit.NSColor.clearColor())
                    self.window.setHasShadow_(True)
                    result.update(ok=True, mode=selected, external_backdrop=selected != 'solid',
                                  refraction=False, desktop_capture=False, reduced_transparency=bool(reduced),
                                  visual_acceptance_pending=True)
            except Exception as error:
                # Leave the content attached even if a material API is unavailable.
                if self.original is not None:
                    try:
                        self.original.removeFromSuperview()
                        self.window.setContentView_(self.original)
                        self.window.setOpaque_(True)
                        self.window.setBackgroundColor_(AppKit.NSColor.windowBackgroundColor())
                    except Exception:
                        pass
                self.wrapper, self.mode = None, 'solid'
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
