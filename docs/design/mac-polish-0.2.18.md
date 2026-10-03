# Mac 0.2.18 refinement

Design reference: `mac-polish-0.2.18.png`, generated with the built-in image tool from the installed Single hand and Settings screens. This is a design reference, never a bitmap UI or simulated desktop backdrop.

## Inventory and design system

- Preserve the existing WUJI wordmark, navigation, connection state and exact `wuji hand` name. No new decorative icon family.
- Keep Single hand controls beside the native model, independent per-device workspaces, Connection/calibration/mapping, and Settings. Preserve all existing stop and confirmation controls.
- Use the public AppKit-composited window backdrop. Remove the dark `.app` fill that covered it with 86% opacity. No desktop screenshots, pixel sampling, private compositor API or fake wallpaper in the app.
- Keep a 36 px Mac titlebar safe area, 68 px header, a compact 174–188 px rail and 22–24 px gutters. Page headings must remain visible at minimum desktop size.
- Outer chrome: a restrained single reflective rim and a translucent neutral fill. Reading surfaces: localized translucent-white/charcoal panels with legible text. The 3D view remains charcoal and labelled with its actual data source.
- SF/PingFang/system type: 14 px body, 13 px controls, 24 px headings; regular controls about 36 px high with 12 px corners, primary actions 42 px with 14 px corners. Shared styles apply to menus, settings, playlist, connections and floating viewers.
- Replace default language dropdown presentation with a keyboard-accessible `中文 / EN` capsule backed by the existing locale state/events. Keep native preference persistence and language bridge callbacks.
- Support light/dark, small/default/large interface type, reduced transparency, reduced motion and forced colors. No decorative continuous animation, duplicate timers, or changes to device/control timing.

## Motion boundaries

Chinese numbers are project-authored poses, not official Wuji motion recordings, measured human motions or universal sign language. Refine against the bundled official Hand 2 geometry and axis limits; retain fixed-wrist and regional-convention limitations. New range-exploration motions are preview-only and must be rejected before any hardware-enable/send or command-trajectory export path. Model joint limits do not prove collision-free movement or real-device safety.

## Acceptance ledger

Compare the design reference and actual browser/native screenshots in one pass. Check at least: exposed glass surround, heading position, language capsule, control type/corners, settings density, dark-mode contrast, and narrow-window overflow. Test both locales, three text scales, settings/tab keyboard behavior, playlist and preview playback. Validate finite 20-joint poses, official model limits and preview-only rejection; do not connect or actuate real hardware for this refinement.

Intentional differences from the generated reference: preserve the existing official model and icon meanings exactly; do not copy an invented background photograph or generated hand geometry into the software. Disabled hardware controls stay disabled, even where the concept accidentally makes them prominent. Native traffic lights and material follow macOS and accessibility settings, not pixels painted by CSS.

### Actual comparison, 2026-10-04

| Area | Observed result | Acceptance |
| --- | --- | --- |
| Outer glass | Removed the dense dark canvas and used public window-underlay frost plus Clear surface. The captured Mac surround still reads flat white/gray; visible external-background strength is not established. | Pending, not a claimed visual pass |
| Shell geometry | One 22 px rim, titlebar-safe header at 36 px, page/rail begin at 116 px. Native inspection reports no horizontal overflow at 960×680 and 1320×838. | Pass |
| Language selector | Explicit Chinese/English capsule, visible active segment, synchronized settings/header; actual Mac Left-key switch works. | Pass |
| Settings controls | 12 px rounded inputs/buttons, 14 px segmented containers, three compact categories. Fixed a hover cascade that made the active tab white-on-white. | Pass |
| Text sizing | Three persisted sizes use a CSS variable, not dynamic DOM mutation; measured controls are 11.7/13/14.3 px. | Pass |
| Reading density | Local translucent panels and narrower rail preserve headings/model room. Dark model view remains black for geometry contrast rather than copying the concept's invented glass hand. | Intentional difference |
| Dialogs and popovers | Playlist/dialog surfaces inherit shared radii and type; existing confirmation/stop actions and focus semantics remain. | Pass for inspected paths, not an exhaustive audit of every legacy page |
| Independent viewer | Shares locale/theme/text-size preference, neutral chrome and 36 px controls. External CSS is served under the unchanged strict CSP. Actual Mac viewer shows rounded dark controls and a moving model, approximately 19 fps in this smoke test. | Pass for this disconnected preview path |
| Motion previews | Authored digit refinement plus three range explorations use official geometry. Range actions refuse real-mode execution/export at all control boundaries. | Software pass only, no physical safety/contact claim |

Do not describe API attachment, generated concept quality, or test counts as proof that the requested external glass appearance has been reached. Keep the remaining visual item explicit in the handoff.
