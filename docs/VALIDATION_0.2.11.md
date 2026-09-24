# Hand Workbench 0.2.11 verification

Scope: official calibration-feedback presentation, editable numeric output matrices and a separate open-retargeting YAML draft editor. No real hand motion, glove calibration, model replacement or firmware operation was performed.

## Source and packaged checks

- 473 Python tests: 471 passed, 2 skipped. Coverage includes official progress-field decoding, unknown values, side/profile isolation, revision conflicts, corrupt-file preservation and semantic equivalence of all four exported official templates.
- Thirteen rendered component checks passed: TSV pasting, atomic invalid/oversized-paste rejection, blank fields, independent grids, literal labels, preserving edits/caret on relabel, invalid `setValue`, disabled inputs, sparse labels, separate official stability/constraint states and clearing missing diagnostics.
- Source service (8811) and packaged service (8813) passed save, export, stale-revision and wrong-binding rejection. Live output settings stayed unchanged; both services remained disconnected with motors inactive.
- Nine served UI assets match reviewed source by SHA-256 in both services. The executable bundles PyYAML, all four pinned official templates, their manifest and MIT notice.
- Browser-plugin QA at 1280×720: Connection → Glove → hand mapping → Official parameter table, edit/save/export/reopen, blank-value rejection, and language switching that preserves unfinished input. Page content rendered, with no framework overlay, relevant console errors or horizontal document overflow.
- Light/dark rendering inspected. A white selected tab with unreadable text in dark mode was corrected to use workbench theme tokens and rechecked.

The Browser fill helper uses paste; blank/partial paste is intentionally rejected. Actual keyboard deletion was separately tested and remained blank, with save rejected. This was a test-method distinction, not a value-restoration workaround.

## Delegation

One local CLI call requested and returned `claude-opus-5-5`, effort `max`, completed with exit code 0. Only a public generic matrix-editor specification was sent. Main review added language changes without replacing inputs and rejection of sparse label arrays, then integrated and tested the result. There are no pending Claude calls for this update.

## Limits

The Studio application itself is not embedded. Calibration runs the official CLI; feedback adapters are tested using the public SDK callback shape and software fixtures, not a completed real glove session. Full Studio Calibration Debug recording and the human URDF viewer are not implemented.

The official parameter table exports a configuration for the separate pinned open-source tuning tool. It does not run that optimizer or change the active SDK RetargetSession. Official configurations retain their original model/version paths; no new solver-to-device compatibility is claimed.

The bundled report was prepared before installation. The final in-place Windows upgrade succeeded and the installed application reports native WebView2 version 0.2.11. Connection-page checks at 960×680 and 1308×864 window sizes found no horizontal overflow, UI errors or closed panels occupying layout space. The app remained disconnected. Its own WebView capture was reviewed; no desktop capture or input automation was used. Acrylic remains enabled, with refraction and desktop capture disabled. The matrix interactions above were tested through the source and packaged browser surfaces; this native check covers startup and connection-page layout.

Existing hardware-control logic and parameters are unchanged. This is a software-only verification, not real glove or mechanical-hand acceptance.
