# Hand Workbench 0.2.9 verification

This is an unofficial Windows software release. No physical hand was connected or actuated during these checks.

## Source and controller checks

- Windows: 465 Python tests run; 463 passed and 2 skipped.
- Built-in Linux: all 33 choreography mathematics, fake-SDK coordination and workspace tests passed.
- Left/right curves and interpolated points were checked against both native model joint limits. This does not establish inter-hand collision safety or physical motion quality.
- Tests cover the common clock domain, holding measured pose until start, retiming without acceleration, rejecting late starts, rejecting duplicate serials and mixed clocks, partial-start cleanup, independent control expiry and read-only preparation.
- Workspace tests cover persistence, unique membership, invalid inputs, offline preview, idle-only moves, safe removal and duplicate adds while connecting.

## Interface and integration

- Source HTTP checks used two separate offline left/right processes. Full playback, manual stop, expired group connection and moving an existing hand between workspaces passed.
- Actual rendered UI: selection, pair modes, explicit preview, completed playback, language switching with selection retained, new empty workspace, adding/removing an offline preview and removing the empty workspace passed.
- Light and dark layouts were inspected at 1280 pixels. No horizontal document overflow or browser script errors were reported. Native-window/package checks are recorded below when complete.
- Preview images are the actual per-session MuJoCo output. Each panel labels preview, measured feedback or an unavailable/reference state; unknown data is not synthesized as feedback.

## Issues found and corrected

- Added missing durations for internal paired preview roles.
- Corrected the delegated UI and test command envelopes to the actual API, generation labels and per-member status mapping.
- Kept the newest group control connection during refresh and handled Stop during an unfinished Start request.
- Prevented two simultaneous add attempts from assigning the same still-connecting device twice.
- Corrected inherited image sizing that clipped the new paired preview cards.
- Final member statuses distinguish stopped/completed from stop-unconfirmed instead of retaining their earlier playing labels.
- A test-service restart encountered a host process-tool error; only confirmed disconnected QA instances were restarted through the system process interface. The installed app and training were unaffected.

## Claude collaboration

Five local CLI tasks completed with actual model `claude-opus-5-5`, effort `max`, exit code 0: normalized paired motion helpers, group controls UI, workspace UI, workspace tests and bilingual instructions. All were reviewed; interface mismatches were corrected before adoption. Claude authored 13 motion-helper tests and 8 workspace tests; the main assistant added a serial-race regression and owns control logic and acceptance. Prompts contained generic interface specifications, not private weights, device recordings or credentials.

An older optional 0.2.8 test-generation call failed at the service output limit and was not adopted. It is not included in the five successful 0.2.9 calls.

## Package and hardware boundary

Windows executable and installer built successfully. The packaged executable passed the same two-session HTTP complete/stop/expiry and workspace-move checks. The existing application was upgraded in place to 0.2.9 and reopened through its own maintenance interface; native Acrylic is active, desktop capture is false, the six new UI assets match the reviewed source, and installed WebView checks reported no script errors or horizontal overflow at 1307×864 and 947×644. No device was connected. This final record was completed after building the installer; the bundled draft records build-time status. Real multi-hand and glove/calibration acceptance remains pending. Real grouped motion currently supports Hand 2 only on one Linux clock domain. No mechanical synchronization tolerance or inter-hand collision checking is claimed. No private learned models are included. Windows retains the same installer identity and user-data location.

Installer SHA-256: `ebeaf82d7837b764daa6a7037f7209f7b6480f7ef93dfb047c385f06be24f21a`.
