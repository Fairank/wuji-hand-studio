import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from native_material_lifecycle import DwmMessages, MaterialLifecycle


class Event:
    def __init__(self):self.callbacks = []
    def __iadd__(self, callback):self.callbacks.append(callback);return self
    def __isub__(self, callback):self.callbacks.remove(callback);return self
    def fire(self):
        for callback in self.callbacks[:]:callback(None, None)


class Timer:
    def __init__(self):self.Tick=Event();self.running=False;self.disposed=False;self.starts=0
    def Stop(self):self.running=False
    def Start(self):self.running=True;self.starts+=1
    def Dispose(self):self.disposed=True
    def fire(self):
        if self.running:self.Tick.fire()


class MaterialLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.form=SimpleNamespace(WindowState='Normal',Visible=True,IsDisposed=False,
                                  IsHandleCreated=True,InvokeRequired=False)
        for name in ('Activated','Deactivate','Shown','HandleCreated','HandleDestroyed',
                     'VisibleChanged','Resize','FormClosed'):setattr(self.form,name,Event())
        self.system=SimpleNamespace(**{key:Event() for key in (
            'UserPreferenceChanged','DisplaySettingsChanged','PowerModeChanged')})
        self.timer=Timer();self.published=[];self.calls=[];self.dispatched=[]
        self.hook=SimpleNamespace(attach=Mock(),detach=Mock())
        def apply(form, enabled):
            self.calls.append(enabled)
            return dict(requested=enabled, external_backdrop=enabled, mode='desktop_acrylic' if enabled else 'opaque')
        self.life=MaterialLifecycle(self.form,apply,self.published.append,timer=self.timer,
             dispatch=self.dispatched.append,system_events=self.system,
             message_factory=lambda *_:self.hook)
    def tearDown(self):self.life.close()

    def test_activation_and_system_burst_coalesces(self):
        self.life.request(True)
        for event in (self.form.Deactivate,self.form.Activated,self.system.UserPreferenceChanged,
                      self.system.DisplaySettingsChanged,self.system.PowerModeChanged):event.fire()
        self.assertEqual(self.calls,[True]);self.timer.fire()
        self.assertEqual(self.calls,[True,True]);self.assertFalse(self.timer.running)
        self.assertEqual(self.published[-1]['inactive_appearance'],'system_managed')

    def test_explicit_reduce_transparency_survives_all_recovery_events(self):
        self.life.request(False);self.system.UserPreferenceChanged.fire();self.timer.fire()
        self.form.Deactivate.fire();self.timer.fire()
        self.assertEqual(self.calls,[False,False,False])
        self.assertFalse(self.published[-1]['external_backdrop'])

    def test_system_callback_is_marshaled_before_touching_timer(self):
        self.form.InvokeRequired=True;self.system.UserPreferenceChanged.fire()
        self.assertEqual(len(self.dispatched),1);self.assertEqual(self.timer.starts,0)
        self.form.InvokeRequired=False;self.dispatched.pop()();self.timer.fire()
        self.assertEqual(self.calls,[False])

    def test_no_handle_does_not_start_timer_or_force_handle_creation(self):
        self.form.IsHandleCreated=False;self.system.UserPreferenceChanged.fire()
        self.assertEqual(self.timer.starts,0);self.assertFalse(self.dispatched)
        result=self.life.request(True);self.assertTrue(result['pending_handle']);self.assertFalse(self.calls)
        self.form.IsHandleCreated=True;self.form.HandleCreated.fire();self.timer.fire()
        self.assertEqual(self.calls,[True]);self.hook.attach.assert_called_once()

    def test_handle_destruction_detaches_until_recreated(self):
        self.life.request(True);self.form.Activated.fire();self.form.IsHandleCreated=False
        self.form.HandleDestroyed.fire();self.timer.fire();self.assertEqual(self.calls,[True])
        self.hook.detach.assert_called_once()
        self.form.IsHandleCreated=True;self.form.HandleCreated.fire();self.timer.fire()
        self.assertEqual(self.calls,[True,True])

    def test_resize_drag_does_not_reapply_and_restore_does(self):
        self.life.request(True)
        for _ in range(60):self.form.Resize.fire()
        self.assertEqual(self.timer.starts,0)
        self.form.WindowState='Minimized';self.form.Resize.fire();self.form.Activated.fire();self.timer.fire()
        self.assertEqual(self.calls,[True])
        self.form.WindowState='Normal';self.form.Resize.fire();self.timer.fire()
        self.assertEqual(self.calls,[True,True])

    def test_hidden_does_not_reapply_but_visible_does(self):
        self.form.Visible=False;self.form.VisibleChanged.fire();self.assertEqual(self.timer.starts,0)
        self.form.Visible=True;self.form.VisibleChanged.fire();self.timer.fire()
        self.assertEqual(self.calls,[False])

    def test_error_can_recover_without_rewriting_preference(self):
        original=self.life.apply;self.life.apply=Mock(side_effect=RuntimeError('synthetic'))
        with self.assertLogs(level='ERROR'):result=self.life.request(True)
        self.assertFalse(result['external_backdrop']);self.assertTrue(result['requested'])
        self.life.apply=original;self.form.Activated.fire();self.timer.fire()
        self.assertTrue(self.published[-1]['external_backdrop']);self.assertEqual(self.calls,[True])

    def test_publish_failure_never_escapes_ui_timer(self):
        self.life.publish=Mock(side_effect=RuntimeError('synthetic document closed'))
        self.form.Activated.fire()
        with self.assertLogs(level='ERROR'):self.timer.fire()
        self.assertFalse(self.timer.running)

    def test_close_detaches_static_handlers_and_disposes_once(self):
        self.form.Activated.fire();self.life.close();self.life.close()
        for event in vars(self.system).values():self.assertEqual(event.callbacks,[])
        self.assertTrue(self.timer.disposed);self.assertFalse(self.timer.running)
        self.form.Activated.fire();self.system.UserPreferenceChanged.fire();self.assertFalse(self.calls)

    def test_constructor_failure_cleans_partial_bindings(self):
        timer=Timer()
        with self.assertRaises(AttributeError):
            MaterialLifecycle(self.form,Mock(),Mock(),timer=timer,dispatch=Mock(),
                              system_events=SimpleNamespace(),message_factory=Mock())
        self.assertTrue(timer.disposed);self.assertEqual(timer.Tick.callbacks,[])

    def test_compositor_messages_forward_before_notifying(self):
        order=[];hook=DwmMessages.__new__(DwmMessages)
        hook.dll=SimpleNamespace(DefSubclassProc=lambda *args:order.append(('forward',args)) or 42)
        hook.notify=lambda reason:order.append(('notify',reason))
        for code in (0x031E,0x031A,0x0006):
            order.clear();self.assertEqual(hook._message(123,code,0,0,0,0),42)
            self.assertEqual(order[0][0],'forward')
            self.assertEqual(len(order),2 if code in DwmMessages.REASONS else 1)

    def test_reapply_cannot_self_schedule_a_timer_loop(self):
        original=self.life.apply
        def apply(form, enabled):
            self.life.queue('DwmCompositionChanged')
            return original(form,enabled)
        self.life.apply=apply;self.life.request(True)
        self.assertEqual(self.timer.starts,0);self.assertFalse(self.timer.running)

    def test_queued_old_callback_after_close_is_noop(self):
        self.form.InvokeRequired=True;self.system.UserPreferenceChanged.fire()
        self.life.close();self.form.InvokeRequired=False
        self.dispatched.pop()();self.assertEqual(self.timer.starts,0);self.assertFalse(self.calls)


if __name__=='__main__':unittest.main()
