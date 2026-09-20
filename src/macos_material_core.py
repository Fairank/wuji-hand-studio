"""Native window material for a caller-supplied Cocoa NSWindow (public AppKit only).
NSGlassEffectView is looked up dynamically (exists only in the macOS 26+ AppKit); else
NSVisualEffectView (behind-window, active). Lazy PyObjC imports. Not device-validated."""
import platform

TAG = "appmaterial."  # NSView.identifier prefix marking a wrapper created by this module
KINDS = {  # kind -> (reported material, native view class, honest note)
    "glass": ("macos_glass", "NSGlassEffectView", "hosted via NSGlassEffectView.contentView"),
    "vibrancy": ("vibrancy", "NSVisualEffectView", "behind-window blur; not glass, no refraction"),
    "opaque": ("opaque", "NSBox", "opaque window-background fill; Reduce Transparency is on"),
    "solid": ("solid", None, "original content view restored; no effect view"),
}


def _reply(ok, status, **extra):
    return {"ok": bool(ok), "status": status, "os_version": platform.mac_ver()[0] or None, **extra}


class WindowMaterial:
    """Material state for ONE NSWindow, retained by the app and used on the main thread."""

    def __init__(self, nswindow):
        import AppKit
        from Foundation import NSClassFromString, NSThread
        if not NSThread.isMainThread():
            raise RuntimeError("WindowMaterial must be used on the AppKit main thread")
        if not isinstance(nswindow, AppKit.NSWindow):
            raise TypeError("nswindow must be an AppKit.NSWindow")
        self.A, self.window = AppKit, nswindow
        self.glass_cls = NSClassFromString("NSGlassEffectView")  # None before macOS 26
        view = nswindow.contentView()
        self.wrapper, self.kind, self.original = None, "solid", view

    def _attached(self):  # factual check: is the original view still inside this window?
        return bool(self.original is not None and self.original.window() == self.window)

    def _target(self, mode):
        ws = self.A.NSWorkspace.sharedWorkspace()
        reduce = bool(ws.accessibilityDisplayShouldReduceTransparency())  # read-only query
        if mode == "solid":
            return "solid", None, reduce
        if reduce:  # accessibility wins: choose the opaque appearance
            return "opaque", "reduce_transparency", reduce
        if self.glass_cls is None:
            return "vibrancy", "NSGlassEffectView_unavailable", reduce
        return "glass", None, reduce

    def _build(self, kind, frame):
        A = self.A
        if kind == "glass":
            w = self.glass_cls.alloc().initWithFrame_(frame)
            w.setCornerRadius_(16.0)
        elif kind == "vibrancy":
            w = A.NSVisualEffectView.alloc().initWithFrame_(frame)
            w.setMaterial_(A.NSVisualEffectMaterialUnderWindowBackground)
            w.setBlendingMode_(A.NSVisualEffectBlendingModeBehindWindow)
            w.setState_(A.NSVisualEffectStateActive)
        else:  # opaque backing; the dynamic system color follows light/dark
            w = A.NSBox.alloc().initWithFrame_(frame)
            w.setBoxType_(A.NSBoxCustom)
            w.setTitlePosition_(A.NSNoTitle)
            w.setBorderWidth_(0.0)
            w.setContentViewMargins_((0.0, 0.0))
            w.setFillColor_(A.NSColor.windowBackgroundColor())
        w.setIdentifier_(TAG + kind)
        w.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
        return w

    def _rollback(self):
        try:  # never lose content: the original goes back as the window content view
            if self.window.contentView() != self.original:
                self.original.removeFromSuperview()
                self.window.setContentView_(self.original)
            self.wrapper, self.kind = None, "solid"
        except Exception:
            pass

    def _swap(self, kind):
        A, win, orig = self.A, self.window, self.original
        # Build first: if that fails, the view hierarchy has not been touched at all.
        new = None if kind == "solid" else self._build(kind, win.contentView().frame())
        focus = win.firstResponder()
        try:
            if self.wrapper is not None:  # un-host from the previous wrapper
                if self.kind != "vibrancy":
                    self.wrapper.setContentView_(None)
                orig.removeFromSuperview()
            win.setContentView_(orig if new is None else new)  # title bar is not touched
            if kind == "vibrancy":
                orig.setFrame_(new.bounds())
                orig.setAutoresizingMask_(A.NSViewWidthSizable | A.NSViewHeightSizable)
                new.addSubview_(orig)
            elif new is not None:
                new.setContentView_(orig)  # Apple's documented containment property
        except Exception:
            self._rollback()
            raise
        self.wrapper, self.kind = new, kind
        if isinstance(focus, A.NSView):
            win.makeFirstResponder_(focus)  # re-hosting can drop keyboard focus

    def apply(self, mode="glass"):
        if mode not in ("glass", "solid") or self.original is None or self.kind not in KINDS:
            why = "original_view_not_found" if mode in ("glass", "solid") else "invalid_mode"
            return _reply(False, "error", reason=why, requested=str(mode))
        try:
            kind, why, reduce = self._target(mode)
            changed = kind != self.kind
            if changed:
                self._swap(kind)
        except Exception as exc:  # _swap has already rolled back to the original view
            return _reply(False, "error", requested=mode, reason=type(exc).__name__,
                          detail=str(exc), material=KINDS[self.kind][0],
                          content_attached=self._attached())
        self.window.setOpaque_(self.kind in ("opaque", "solid"))
        self.window.setBackgroundColor_(self.A.NSColor.windowBackgroundColor() if self.kind in ("opaque", "solid") else self.A.NSColor.clearColor())
        self.window.setHasShadow_(True)
        material, native, note = KINDS[self.kind]
        return _reply(True, "applied" if changed else "unchanged", requested=mode,
                      material=material, native_view=native, note=note, fallback_reason=why,
                      reduce_transparency=reduce, content_attached=self._attached(),
                      titlebar="standard; style mask and title bar not modified")
