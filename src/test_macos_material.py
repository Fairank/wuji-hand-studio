"""State-transition checks with AppKit doubles, not a claim of visual validation."""
import sys
import types
import unittest
from unittest.mock import patch
from macos_material_core import WindowMaterial


class View:
    def __init__(self): self.parent = None; self.children = []; self.frame_value = (0, 0, 100, 100)
    @classmethod
    def alloc(cls): return cls()
    def initWithFrame_(self, frame): self.frame_value = frame; return self
    def frame(self): return self.frame_value
    def bounds(self): return self.frame_value
    def setFrame_(self, frame): self.frame_value = frame
    def removeFromSuperview(self):
        if self.parent and self in self.parent.children: self.parent.children.remove(self)
        self.parent = None
    def addSubview_(self, view):
        view.removeFromSuperview(); self.children.append(view); view.parent = self
    def setContentView_(self, view):
        for old in list(self.children): old.removeFromSuperview()
        if view is not None: self.addSubview_(view)
    def contentView(self): return self.children[0] if self.children else None
    def window(self): return self.parent.window() if self.parent else None
    def __getattr__(self, name):
        if name.startswith('set'): return lambda *a:None
        raise AttributeError(name)


class Window(View):
    def __init__(self):
        super().__init__(); self.focus = None
    def window(self): return self
    def firstResponder(self): return self.focus
    def makeFirstResponder_(self, focus): self.focus = focus


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.reduced = False; self.modern = False
        self.window = Window(); self.original = View(); self.window.setContentView_(self.original)
        self.window.focus = self.original
        appkit = types.SimpleNamespace(NSWindow=Window, NSView=View, NSVisualEffectView=View, NSBox=View,
            NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda:types.SimpleNamespace(accessibilityDisplayShouldReduceTransparency=lambda:self.reduced)),
            NSColor=types.SimpleNamespace(windowBackgroundColor=lambda:'opaque', clearColor=lambda:'clear'))
        for name in ('NSVisualEffectMaterialUnderWindowBackground','NSVisualEffectBlendingModeBehindWindow','NSVisualEffectStateActive','NSBoxCustom','NSNoTitle','NSViewWidthSizable','NSViewHeightSizable'):
            setattr(appkit, name, 1)
        foundation = types.SimpleNamespace(NSClassFromString=lambda name:View if self.modern else None,
                                         NSThread=types.SimpleNamespace(isMainThread=lambda:True))
        self.modules = patch.dict(sys.modules, AppKit=appkit, Foundation=foundation)
        self.modules.start(); self.addCleanup(self.modules.stop)

    def test_vibrancy_to_solid_keeps_content_and_focus(self):
        material = WindowMaterial(self.window)
        result = material.apply()
        self.assertEqual(result['material'], 'vibrancy')
        self.assertTrue(result['content_attached'])
        self.assertIs(self.window.focus, self.original)
        result = material.apply('solid')
        self.assertIs(self.window.contentView(), self.original)
        self.assertTrue(result['content_attached'])

    def test_vibrancy_even_when_newer_material_is_available(self):
        self.modern = True
        material = WindowMaterial(self.window)
        result = material.apply()
        self.assertEqual(result['material'], 'vibrancy')
        self.assertIs(self.window.contentView().contentView(), self.original)

    def test_repeated_mode_does_not_nest_or_replace_wrapper(self):
        material = WindowMaterial(self.window)
        material.apply(); wrapper = self.window.contentView()
        self.assertEqual(material.apply()['status'], 'unchanged')
        self.assertIs(self.window.contentView(), wrapper)
        self.assertEqual(wrapper.children, [self.original])

    def test_accessibility_wins_and_can_be_reversed(self):
        material = WindowMaterial(self.window)
        material.apply(); self.reduced = True
        self.assertEqual(material.apply()['material'], 'opaque')
        self.assertTrue(material._attached())
        self.reduced = False
        self.assertEqual(material.apply()['material'], 'vibrancy')
        self.assertTrue(material._attached())

    def test_failed_construction_leaves_previous_view_attached(self):
        material = WindowMaterial(self.window)
        with patch.object(material, '_build', side_effect=RuntimeError('unsupported API')):
            self.assertFalse(material.apply()['ok'])
        self.assertIs(self.window.contentView(), self.original)
        self.assertTrue(material._attached())

    def test_failed_attachment_rolls_back(self):
        material = WindowMaterial(self.window)
        broken = View()
        broken.addSubview_ = lambda view: (_ for _ in ()).throw(RuntimeError('cannot attach'))
        with patch.object(material, '_build', return_value=broken):
            self.assertFalse(material.apply()['ok'])
        self.assertIs(self.window.contentView(), self.original)
        self.assertTrue(material._attached())


if __name__ == '__main__': unittest.main()
