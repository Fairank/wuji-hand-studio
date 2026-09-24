# Hand Workbench 0.2.12 verification

Scope: six-pose calibration grid and an optional embedded official open retargeter. No physical glove calibration, mechanical-hand movement, firmware operation or replacement of SDK user models was performed.

## Checks completed before packaging

- 481 Python checks: 479 passed, two skipped. New coverage includes per-pairing engine selection, parameter identity, typed IPC command rejection, explicit official pose completion, preview-only selection without a false applied acknowledgement, packaged controller paths and empty-deployment/status rejection.
- The unmodified pinned official optimizer ran on actual Linux dependencies for Hand 1/2, left/right. Synthetic, nondegenerate 21-point inputs produced finite 20-joint outputs; changing index target scales changed the solved outputs. Joint names/order and paired model limits were verified. This is not a glove accuracy test.
- Median solve times for this small 24-frame synthetic case were 7.08/5.54 ms (Hand 1 left/right) and 8.31/9.59 ms (Hand 2 left/right); p90 7.87/6.12/9.41/10.41 ms. These are neither sustained hardware rates nor a 1000 Hz claim.
- A fake SDK glove exercised the real optimizer subprocess: initial open solver → different parameter digest → SDK fallback → disconnect. Output source/digest changed correctly, no hand feedback or motor session opened, and the subscription/thread/process cleaned up.
- First live-frame processing initially took about 529 ms because upstream lazily imports SciPy Rotation. This was moved to adapter initialization; integrated first frames were then about 15–16 ms without relaxing the 200 ms processing timeout. Frame freshness starts before solving, so processing delay is not erased.
- Hand 1's paired URDF/MJCF round some bounds differently (up to 0.0005 rad). The adapter uses their intersection and aligns both optimizer and warm-start bounds. The fixed dependency lock uses urdfdom 4.0.1/tinyxml2 10.0.0 after a 6.0.0 binary-link mismatch was found and corrected.
- Windows controller-source deployment and actual WSL solver status returned ready. Solver dependencies are isolated from the existing SDK environment; the source/model manifest is verified before initialization.
- Installed-app QA found an inherited frozen-resource path error: the controller source was looked up one directory above the application, so an empty bundle had been accepted. Windows now resolves controller/source from the executable directory, rejects missing bootstrap/empty bundles, and preserves setup stderr instead of reporting an index error. The first failing installer was not published.
- Browser plugin at 1280×720: six cards in a three-column/two-row grid, selecting a card changes its reference guidance, English labels retain the selection, and the numeric matrix/runtime controls render. No horizontal document overflow, framework overlay or relevant console errors. Disconnected/unconfigured environments show an error instead of falsely enabling application. No real calibration was started.
- Windows executable build and self-check passed. Final installed-window verification is recorded after installation below.

## Delegation and provenance

One local CLI call requested and returned `claude-opus-5-5 --effort max`, exit 0. It received only a generic, display-only six-card component specification, with tools disabled and no private code, models, device records or credentials. The main assistant reviewed its component, integrated official state semantics and verified the application. No pending Claude call remains.

Public optimizer source is pinned to 531f6ed4250b475d2e9231f54e988fc9b1c5b4ea; current viewer-matched model source to c2cd7f8d1ef8b6dc8cb907c17daa5a88b4442d95. Original licenses/checksums are included. Hand 2 runtime uses Beta 2; standalone YAML export retains original upstream Beta 1 paths.

## Limits

This embeds the public solver and invokes official calibration through CLI; it does not embed the complete proprietary Studio or reproduce an unseen internal tuning panel. True glove fit, six-pose capture and real-hand follow still require device acceptance. The new solver environment supports the verified Linux x86_64/Python 3.12 path (including Windows built-in Linux); Mac/ARM and remote SSH setup are not integrated. Existing SDK mapping remains available.

Parameter saves and requests do not count as applied. The UI checks fresh output, selected engine and parameter digest. Selecting an engine never enables motors; following is a separate action. Native Acrylic is retained; external capture/refraction is not restored.

## Installed Windows check

The in-place update reports native WebView2 version 0.2.12, ready. Its own maintenance interface verified the connection page at window sizes 960×680 and 1308×864 (content 947×644 and 1295×828): no horizontal overflow, UI errors or closed-panel layout residue. Device state remained disconnected. Browser-plugin verification against the installed service additionally confirmed the new parameter panel and its environment-status path. Light/dark rendering, language changes and arrow-key focus movement were checked on the source UI. No desktop capture or mouse/keyboard control was used.
