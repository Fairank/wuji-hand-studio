"""State-transition checks with AppKit doubles, not a claim of visual validation."""
import sys
import types
import unittest
from unittest.mock import patch
from macos_material_core import WindowMaterial


class View:
    def __init__(self): self.parent = None; self.children = []; self.frame_value = (0, 0, 100, 100); self.identifier = None
    @classmethod
    def alloc(cls): return cls()
    def initWithFrame_(self, frame): self.frame_value = frame; return self
    def frame(self): return self.frame_value
    def bounds(self): return self.frame_value
    def setFrame_(self, frame): self.frame_value = frame
    def setIdentifier_(self, value): self.identifier = value
    def removeFromSuperview(self):
        if self.parent and self in self.parent.children: self.parent.children.remove(self)
        self.parent = None
    def addSubview_(self, view):
        view.removeFromSuperview(); self.children.append(view); view.parent = self
    def setContentView_(self, view):
        for old in list(self.children): old.removeFromSuperview()
        if view is not None: self.addSubview_(view)
    def contentView(self): return self.children[0] if self.children else None
    def setStyle_(self, value): self.effect_style = value
    def setMaterial_(self, value): self.effect_material = value
    def setBlendingMode_(self, value): self.effect_blending = value
    def window(self): return self.parent.window() if self.parent else None
    def __getattr__(self, name):
        if name.startswith('set'): return lambda *a:None
        raise AttributeError(name)


class Window(View):
    def __init__(self):
        super().__init__(); self.focus = None; self.style_mask = 4; self.titlebar_transparent = False
        self.title_visibility = 0; self.window_appearance = 'original-appearance'; self.window_toolbar = 'original-toolbar'
        self.toolbar_style = 'original-style'; self.separator_style = 'original-separator'; self.opaque = True
        self.background = 'original-background'; self.shadow = False
    def window(self): return self
    def firstResponder(self): return self.focus
    def makeFirstResponder_(self, focus): self.focus = focus
    def styleMask(self): return self.style_mask
    def setStyleMask_(self, value): self.style_mask = value
    def titlebarAppearsTransparent(self): return self.titlebar_transparent
    def setTitlebarAppearsTransparent_(self, value): self.titlebar_transparent = value
    def titleVisibility(self): return self.title_visibility
    def setTitleVisibility_(self, value): self.title_visibility = value
    def appearance(self): return self.window_appearance
    def setAppearance_(self, value): self.window_appearance = value
    def toolbar(self): return self.window_toolbar
    def setToolbar_(self, value): self.window_toolbar = value
    def toolbarStyle(self): return self.toolbar_style
    def setToolbarStyle_(self, value): self.toolbar_style = value
    def titlebarSeparatorStyle(self): return self.separator_style
    def setTitlebarSeparatorStyle_(self, value): self.separator_style = value
    def setOpaque_(self, value): self.opaque = value
    def setBackgroundColor_(self, value): self.background = value
    def setHasShadow_(self, value): self.shadow = value


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.reduced = False; self.modern = False
        self.window = Window(); self.original = View(); self.window.setContentView_(self.original)
        self.window.focus = self.original
        appkit = types.SimpleNamespace(NSWindow=Window, NSView=View, NSVisualEffectView=View, NSBox=View,
            NSWorkspace=types.SimpleNamespace(sharedWorkspace=lambda:types.SimpleNamespace(accessibilityDisplayShouldReduceTransparency=lambda:self.reduced)),
            NSColor=types.SimpleNamespace(windowBackgroundColor=lambda:'opaque', clearColor=lambda:'clear'),
            NSAppearance=types.SimpleNamespace(appearanceNamed_=lambda name: ('appearance', name)))
        for name in ('NSVisualEffectMaterialUnderWindowBackground','NSVisualEffectBlendingModeBehindWindow','NSVisualEffectStateActive','NSBoxCustom','NSNoTitle','NSViewWidthSizable','NSViewHeightSizable', 'NSWindowStyleMaskFullSizeContentView','NSWindowTitleHidden','NSTitlebarSeparatorStyleNone','NSAppearanceNameDarkAqua','NSAppearanceNameAqua'):
            setattr(appkit, name, 1)
        foundation = types.SimpleNamespace(NSClassFromString=lambda name:View if self.modern else None,
                                         NSThread=types.SimpleNamespace(isMainThread=lambda:True))
        pyobjc_tools = types.SimpleNamespace(AppHelper=types.SimpleNamespace(callAfter=lambda fn:fn()))
        self.modules = patch.dict(sys.modules, AppKit=appkit, Foundation=foundation, PyObjCTools=pyobjc_tools)
        self.modules.start(); self.addCleanup(self.modules.stop)

    def test_older_macos_uses_behind_window_vibrancy_then_solid_restores_chrome(self):
        original_chrome = (self.window.styleMask(), self.window.titlebarAppearsTransparent(), self.window.titleVisibility(),
                           self.window.appearance(), self.window.toolbar(), self.window.toolbarStyle(), self.window.titlebarSeparatorStyle())
        material = WindowMaterial(self.window)
        result = material.apply()
        self.assertEqual(result['material'], 'vibrancy')
        self.assertEqual(result['fallback_reason'], 'NSGlassEffectView_unavailable')
        self.assertTrue(result['content_attached'])
        self.assertEqual(self.window.contentView().identifier, 'appmaterial.vibrancy')
        self.assertNotEqual(self.window.styleMask(), original_chrome[0])
        self.assertIs(self.window.focus, self.original)
        result = material.apply('solid')
        self.assertEqual(result['material'], 'solid')
        self.assertIs(self.window.contentView(), self.original)
        self.assertTrue(result['content_attached'])
        self.assertEqual((self.window.styleMask(), self.window.titlebarAppearsTransparent(), self.window.titleVisibility(),
                          self.window.appearance(), self.window.toolbar(), self.window.toolbarStyle(), self.window.titlebarSeparatorStyle()), original_chrome)

    def test_macos26_uses_dynamic_glass_view_with_frosted_style(self):
        self.modern = True
        material = WindowMaterial(self.window)
        result = material.apply()
        self.assertEqual(result['material'], 'macos_glass')
        self.assertIs(self.window.contentView().children[0].contentView(), self.original)
        self.assertEqual(self.window.contentView().children[0].identifier, 'appmaterial.glass')

    def test_reduce_transparency_uses_opaque_then_restores_glass(self):
        self.modern = True
        self.reduced = True
        material = WindowMaterial(self.window)
        result = material.apply()
        self.assertEqual(result['material'], 'opaque')
        self.assertEqual(result['fallback_reason'], 'reduce_transparency')
        self.assertTrue(self.window.opaque)
        self.assertTrue(material._attached())
        self.reduced = False
        self.assertEqual(material.apply()['material'], 'macos_glass')
        self.assertTrue(material._attached())

    def test_clear_surface_keeps_real_frost_backing_and_content(self):
        self.modern = True
        material = WindowMaterial(self.window)
        material.configure('light', 'clear')
        result = material.apply()
        backing = self.window.contentView()
        self.assertEqual(backing.effect_material, 1)
        self.assertEqual(backing.effect_blending, 1)
        self.assertEqual(backing.children[0].effect_style, 1)
        self.assertEqual(result['glass_style'], 'clear')
        self.assertEqual(result['backdrop'], 'behind_window_frost')
        self.assertTrue(result['content_attached'])
        material.configure('dark', 'frosted')
        self.assertEqual(backing.children[0].effect_style, 0)
        self.assertIs(backing.children[0].contentView(), self.original)

    def test_repeated_mode_does_not_nest_or_replace_wrapper(self):
        material = WindowMaterial(self.window)
        material.apply(); wrapper = self.window.contentView()
        self.assertEqual(material.apply()['status'], 'unchanged')
        self.assertIs(self.window.contentView(), wrapper)
        self.assertEqual(wrapper.children, [self.original])

    def test_repeated_glass_mode_does_not_nest_or_replace_wrappers(self):
        self.modern = True
        material = WindowMaterial(self.window)
        material.apply(); root = self.window.contentView(); glass = root.children[0]
        self.assertEqual(material.apply()['status'], 'unchanged')
        self.assertIs(self.window.contentView(), root)
        self.assertIs(root.children[0], glass)
        self.assertEqual(glass.children, [self.original])

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
