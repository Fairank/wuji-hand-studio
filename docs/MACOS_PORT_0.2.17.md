# macOS 0.2.17 port acceptance

Base: `v0.2.16-windows`, commit `d8a9f49e4285c15b4739f1f96725b6cbc20d5d16`.

Target: Apple Silicon / macOS 26.6.2 / Python 3.12 / WKWebView.

## Verified software checks

- Full final suite: 503 tests, 501 passed and 2 skipped.
- Packaged executable: native arm64; all four hand profiles load 20 actuators;
  58 actions present; MuJoCo offscreen rendering returns finite 160×160 RGB.
- Deep/strict ad-hoc signature verification and all bundled Linux manifest hashes pass.
- Actual Cocoa window reports NSGlassEffectView, frosted style, attached content,
  no external desktop capture. Light/dark and solid/glass toggles settle correctly.
- At 960×680 and 1280×820: no horizontal overflow, no reported UI errors;
  header and navigation have transparent fills and no CSS backdrop blur overlay.
- Chinese and English native connection headers both show `wuji hand`.
- Native playlist editor adds an action, starts preview, closes the editor,
  displays the authored hand movement and returns after completion.
- Browser source UI: two-routine shuffle/loop, pause/resume/stop, settings and
  multi-hand manager navigation; no reported browser errors.
- All acceptance device state remains disconnected; hardware active remains false.
- Mocked shutdown tests confirm only the exact owned Running Lima guest can be
  stopped, with busy/unowned/missing/stopped environments left untouched.

No real device, SDK calibration, USB passthrough, VM boot or motor test is implied
by these software checks. The x86_64 open solver is explicitly unsupported in
the built-in Mac ARM environment. Older macOS native-material fallback is
covered by doubles, not a second physical Mac. Package is not notarized.

The original development checkout's interrupted merge is preserved separately;
this port uses an isolated branch and does not resolve or overwrite those edits.
