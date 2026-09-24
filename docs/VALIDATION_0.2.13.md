# Hand Workbench 0.2.13 verification

Scope: connection/calibration/mapping presentation and a display-only joint reference. No real calibration, device connection, hand movement or controller tuning was performed.

## Source checks

- Python suite: 485 checks, 483 passed and two skipped. New tests compare all 80 model indices, joint names and ranges against actual loaded MuJoCo models; validate model hashes/images and reject invalid profile/index paths.
- Reference images are generated from bundled official MJCF with `mj_forward`, never `mj_step`. All 80 images are static RGBA PNGs, so selecting a joint creates no simulation loop or motor command. Rebuild with `scripts/render_joint_reference.py` after a model change.
- Browser checks: selecting index joint 2 shows model index 5 / knuckle spread; selecting the pinky keeps the axis and shows 17. Language switching, changing selection and opening the official table preserve the typed gain 1.15. Official 5×3 target scales remain separate from the 5×4 output matrix. No document horizontal overflow or browser console errors in the checked view.
- Connection controls retain their original handlers and IDs. Calibration still obtains pose/constraint/success state from official CLI; no local clock creates a completion result. The named-user/device setup opens when required and shows why start is unavailable.
- Unverified or disconnected hardware does not produce an invented node/angle. Reference artwork, model limits and hardware feedback are labelled separately. SDK S1–S4 versus model J0–J3 remains unverified according to the current official control guide. Hand 1 generic axis names are retained rather than borrowing Hand 2 semantics.

## Delegation

One code-only local Claude call requested and returned `claude-opus-5-5 --effort max`, exit 0. Generic display-component specification only, tools disabled, no private repository/data/credentials. Main assistant reviewed input validation, image URLs, keyboard navigation, update behavior and side effects; revised model/node labels and integrated selection and layout. No pending call remains.

## Limits

No complete proprietary Studio is embedded. These anatomy references do not certify a particular physical hand's motor mapping. This release keeps native frosted chrome and adds no external screen capture/refraction. Existing controller parameters, force limits, solver selection and hardware activation semantics remain unchanged. Real-device acceptance remains separate.

## Packaged Windows verification

In-place installation reports Windows native WebView2 0.2.13, ready, using desktop Acrylic with refraction and capture disabled. App-only maintenance checked 960×680 and 1308×864 windows (content 947×644 and 1295×828): no horizontal overflow, JavaScript errors or hidden-panel layout residue; disconnected throughout. All 144 installed web files match reviewed source bytes. The installed service's new catalog and reference image rendered correctly in the browser check, preserving the user's Hand 2 right profile.

All eight localized catalog responses, 80 PNG endpoints and four invalid-input rejections passed HTTP checks. Keyboard arrows move focus; Enter changes the selected model joint. The final Chinese calibration grid shows all six cards within the checked 1280×720 viewport. No errors were reported in the final browser console. Source tests use isolated configuration; no live hand or glove calibration was started.

Final installer SHA-256: `5fca69dd47ac395fbf1836ae0882a8a961560e7732a7236e1da817ad4675df75`. An initial package was superseded before installation to prevent automatic retry storms if the optional reference catalog is unavailable; the final static payload was compared byte for byte with source. The first installed-file audit encountered a sandbox read denial, then passed with authorized read access; this was not an application error.
