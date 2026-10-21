"""Restore our backdrop after native lifecycle changes; never changes focus or policy.

WinForms/pywebview can overwrite DWMWA_SYSTEMBACKDROP_TYPE during theme
changes. Keep the last requested preference and reapply on finite events,
coalesced on the form's UI thread. There is no animation or polling loop.
"""
import ctypes
import logging


class MaterialLifecycle:
    def __init__(self, form, apply, publish, *, timer=None, dispatch=None, system_events=None, message_factory=None):
        self.form = form
        self.apply = apply
        self.publish = publish
        self.enabled = False
        self.closed = False
        self.bindings = []
        self.pending_reason = None
        self.restore_count = 0
        self.restoring = False
        self.messages = None
        self.last_state = str(form.WindowState)
        if timer is None:
            from System.Windows.Forms import Timer
            timer = Timer()
        self.timer = timer
        self.timer.Interval = 150
        if dispatch is None:
            from System import Action
            dispatch = lambda callback: form.BeginInvoke(Action(callback))
        self.dispatch = dispatch
        try:
            self._bind(timer, 'Tick', self._tick)
            for name in ('Activated', 'Deactivate', 'Shown'):
                self._bind(form, name, self._handler(name))
            self._bind(form, 'HandleCreated', self._handle_created)
            self._bind(form, 'HandleDestroyed', self._handle_destroyed)
            self._bind(form, 'VisibleChanged', self._visible)
            self._bind(form, 'Resize', self._resize)
            self._bind(form, 'FormClosed', self.close)
            if system_events is None:
                from Microsoft.Win32 import SystemEvents
                system_events = SystemEvents
            for name in ('UserPreferenceChanged', 'DisplaySettingsChanged', 'PowerModeChanged'):
                self._bind(system_events, name, self._handler(name))
            self.messages = (message_factory or DwmMessages)(form, self.queue)
        except Exception:
            self.close()
            raise

    def _bind(self, owner, name, callback):
        event = getattr(owner, name)
        event += callback
        self.bindings.append((event, callback))

    def _handler(self, reason):
        return lambda *_: self.queue(reason)

    def request(self, enabled):
        """Called on the form thread by the existing, typed appearance bridge."""
        if type(enabled) is not bool:
            raise ValueError('Expected boolean material preference')
        if self.closed:
            raise RuntimeError('Window is closed')
        self.enabled = enabled
        self.timer.Stop()
        self.pending_reason = None
        if not self.form.IsHandleCreated:
            self.pending_reason = 'HandleCreated'
            return dict(requested=enabled, mode='opaque', external_backdrop=False,
                        refraction=False, desktop_capture=False, pending_handle=True)
        return self._restore('preference')

    def queue(self, reason):
        if self.closed or self.form.IsDisposed or self.restoring:
            return
        # InvokeRequired can be false while no HWND exists. Never create the
        # handle (or touch the UI timer) from a SystemEvents worker thread.
        if not self.form.IsHandleCreated:
            self.pending_reason = reason
            return
        if self.form.InvokeRequired:
            try:
                self.dispatch(lambda: self.queue(reason))
            except Exception:
                if not self.closed and not self.form.IsDisposed:
                    logging.exception('Could not queue backdrop recovery')
            return
        # Restart the short timer to run after framework theme handlers. It also
        # combines activation + visible + restore notifications into one apply.
        self.pending_reason = reason
        self.timer.Stop()
        self.timer.Start()

    def _handle_created(self, *_):
        if self.messages is not None:
            try:
                self.messages.attach()
            except Exception:
                logging.exception('Could not rebind compositor notifications')
        self.queue('HandleCreated')

    def _handle_destroyed(self, *_):
        self.timer.Stop()
        self.pending_reason = 'HandleCreated'
        if self.messages is not None:
            self.messages.detach()

    def _visible(self, *_):
        if self.form.Visible:
            self.queue('VisibleChanged')

    def _resize(self, *_):
        state = str(self.form.WindowState)
        previous, self.last_state = self.last_state, state
        # Do not repaint the material on every mouse-drag resize frame.
        if state != previous and state != 'Minimized':
            self.queue('WindowRestored')

    def _tick(self, *_):
        self.timer.Stop()
        reason, self.pending_reason = self.pending_reason, None
        if self.closed or self.form.IsDisposed or not self.form.IsHandleCreated or str(self.form.WindowState) == 'Minimized':
            return
        if reason:
            self._restore(reason)

    def _restore(self, reason):
        self.restoring = True
        try:
            result = self.apply(self.form, self.enabled)
        except Exception as error:
            logging.exception('System backdrop unavailable')
            result = dict(requested=self.enabled, mode='opaque', external_backdrop=False,
                          refraction=False, desktop_capture=False, error=type(error).__name__)
        finally:
            self.restoring = False
        self.restore_count += 1
        result.update(recovery_reason=reason, recovery_count=self.restore_count,
                      inactive_appearance='system_managed')
        try:
            self.publish(result)
        except Exception:
            logging.exception('Could not publish backdrop recovery')
        return result

    def close(self, *_):
        if self.closed:
            return
        self.closed = True
        self.timer.Stop()
        self.pending_reason = None
        if self.messages is not None:
            self.messages.detach()
        for event, callback in reversed(self.bindings):
            try:
                event -= callback
            except Exception:
                logging.exception('Could not remove backdrop lifecycle handler')
        self.bindings.clear()
        self.timer.Dispose()


class DwmMessages:
    """A subclass on our own HWND only; forward every message unchanged."""
    REASONS = {0x031E: 'DwmCompositionChanged', 0x031A: 'ThemeChanged'}

    def __init__(self, form, notify):
        self.form, self.notify, self.hwnd = form, notify, None
        self.identity = id(self)
        self.dll = ctypes.WinDLL('comctl32', use_last_error=True)
        prototype = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                                     ctypes.c_size_t, ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_size_t)
        self.callback = prototype(self._message)
        self.dll.SetWindowSubclass.argtypes = [ctypes.c_void_p, prototype, ctypes.c_size_t, ctypes.c_size_t]
        self.dll.SetWindowSubclass.restype = ctypes.c_int
        self.dll.RemoveWindowSubclass.argtypes = [ctypes.c_void_p, prototype, ctypes.c_size_t]
        self.dll.RemoveWindowSubclass.restype = ctypes.c_int
        self.dll.DefSubclassProc.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self.dll.DefSubclassProc.restype = ctypes.c_ssize_t
        self.attach()

    def attach(self):
        if self.form.IsDisposed or not self.form.IsHandleCreated:
            return
        hwnd = int(self.form.Handle.ToInt64())
        if hwnd == self.hwnd:
            return
        self.detach()
        if not self.dll.SetWindowSubclass(hwnd, self.callback, self.identity, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = hwnd

    def detach(self):
        if self.hwnd is not None:
            self.dll.RemoveWindowSubclass(self.hwnd, self.callback, self.identity)
            self.hwnd = None

    def _message(self, hwnd, message, wparam, lparam, _identity, _data):
        result = self.dll.DefSubclassProc(hwnd, message, wparam, lparam)
        if message in self.REASONS:
            try:
                self.notify(self.REASONS[message])
            except Exception:
                logging.exception('Could not schedule compositor recovery')
        return result
