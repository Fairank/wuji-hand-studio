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
        self.theme, self.glass_style = "light", "frosted"
        self.chrome = (nswindow.styleMask(), nswindow.titlebarAppearsTransparent(),
                       nswindow.titleVisibility(), nswindow.appearance(),
                       nswindow.toolbar(), nswindow.toolbarStyle(), nswindow.titlebarSeparatorStyle())

    def _chrome(self, transparent):
        mask, titlebar, title, appearance, toolbar, style, separator = self.chrome
        self.window.setStyleMask_(mask | self.A.NSWindowStyleMaskFullSizeContentView if transparent else mask)
        self.window.setTitlebarAppearsTransparent_(True if transparent else titlebar)
        self.window.setTitleVisibility_(self.A.NSWindowTitleHidden if transparent else title)
        if transparent:
            name = self.A.NSAppearanceNameDarkAqua if self.theme == "dark" else self.A.NSAppearanceNameAqua
            self.window.setAppearance_(None if self.theme == "system" else self.A.NSAppearance.appearanceNamed_(name))
            # Empty NSToolbar covers the WKWebView's HTML controls on macOS 26.
            # Keep standard native titlebar/drag semantics, without an overlay.
            self.window.setToolbar_(toolbar)
            self.window.setTitlebarSeparatorStyle_(self.A.NSTitlebarSeparatorStyleNone)
        else:
            self.window.setAppearance_(appearance)
            self.window.setToolbar_(toolbar)
            self.window.setToolbarStyle_(style)
            self.window.setTitlebarSeparatorStyle_(separator)

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
            w.setCornerRadius_(12.0)
            w.setStyle_(0 if self.glass_style == "frosted" else 1)  # Public Regular / Clear styles.
            w.setTintColor_(None)
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

    def _backdrop(self, frame):
        view = self.A.NSVisualEffectView.alloc().initWithFrame_(frame)
        # This material is explicitly intended to reveal content behind the
        # window. Sidebar is optimized for a denser navigation-area fill.
        view.setMaterial_(self.A.NSVisualEffectMaterialUnderWindowBackground)
        view.setBlendingMode_(self.A.NSVisualEffectBlendingModeBehindWindow)
        view.setState_(self.A.NSVisualEffectStateActive)
        view.setWantsLayer_(True)
        view.setAutoresizingMask_(self.A.NSViewWidthSizable | self.A.NSViewHeightSizable)
        return view

    def configure(self, theme=None, glass_style=None):
        if theme is not None:
            if theme not in ("system", "light", "dark"):
                raise ValueError("Unknown window appearance")
            self.theme = theme
        if glass_style is not None:
            if glass_style not in ("frosted", "clear"):
                raise ValueError("Unknown glass style")
            self.glass_style = glass_style
        self._chrome(self.kind != "solid")
        if self.kind == "glass":
            self.wrapper.setStyle_(0 if self.glass_style == "frosted" else 1)

    def _rollback(self):
        try:  # never lose content: the original goes back as the window content view
            if self.window.contentView() != self.original:
                self.original.removeFromSuperview()
                self.window.setContentView_(self.original)
            self.wrapper, self.kind = None, "solid"
            self._chrome(False)
        except Exception:
            pass

    def _swap(self, kind):
        A, win, orig = self.A, self.window, self.original
        # Build first: if that fails, the view hierarchy has not been touched at all.
        new = None if kind == "solid" else self._build(kind, win.contentView().frame())
        backdrop = self._backdrop(win.contentView().frame()) if kind == "glass" else None
        focus = win.firstResponder()
        try:
            if self.wrapper is not None:  # un-host from the previous wrapper
                if self.kind != "vibrancy":
                    self.wrapper.setContentView_(None)
                orig.removeFromSuperview()
            self._chrome(kind != "solid")
            win.setContentView_(backdrop if backdrop is not None else (orig if new is None else new))
            if backdrop is not None:
                new.setFrame_(backdrop.bounds())
                backdrop.addSubview_(new)
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
            else:
                self._chrome(kind != "solid")
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
                      theme=self.theme, glass_style=self.glass_style,
                      backdrop="behind_window_frost" if self.kind in ("glass", "vibrancy") else "none",
                      titlebar="original" if self.kind == "solid" else "transparent",
                      full_size_titlebar=bool(self.window.styleMask() & self.A.NSWindowStyleMaskFullSizeContentView))
