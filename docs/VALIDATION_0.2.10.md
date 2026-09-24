# Hand Workbench 0.2.10 verification

This release reorganizes the interface. It does not modify gains, trajectories, command cadence, SDK calibration or group control behavior. No physical hand was connected or actuated during software QA.

## Source checks

- Windows: 465 Python tests run; 463 passed and 2 skipped. Changed JavaScript passed syntax checks.
- Source HTTP checks used two independent left/right preview processes. Complete playback, manual stop, group-control expiry and moving a hand between workspaces passed.
- Rendered checks passed for single-hand preview, paired preview, language switching, creating/removing an empty workspace and adding/removing an offline preview hand.
- Device management has three working tabs. Arrow-key tab switching, Escape dismissal and return of focus to the trigger were checked.
- Playlist playback remains visible and stoppable after closing its dialog. Stopping returned the preview to idle. Reduced-amplitude digit guidance stays visible when options are collapsed.
- Light/dark desktop layouts were inspected. No document horizontal overflow, visible hidden-panel boxes or browser script errors were found in the final source view.

## Issues corrected during this update

- Hidden details and inactive-page decorations could retain layout or paint artifacts. Closed content now has no rendered boxes. The exact cause of the white strip in the user's original screenshot was not conclusively reproduced; this is not proof that every native composition artifact is eliminated.
- Fixed competing navigation translations, dark-mode selected-state contrast, duplicated view-source controls and stale group request receipts. Existing device errors and measured/reference/preview source labels remain visible.
- The isolated source service briefly reported missing new assets before the delegated files were adopted. Assets were then installed and requests verified; the original build-time error log was preserved.
- The first test run also preceded those files and failed two asset checks. The complete run after adoption passed as reported above.

## Delegation and package status

The local Claude CLI supplied the generic dialog component using actual model `claude-opus-5-5`, effort `max`. The main assistant reviewed and integrated it; it has no device or network operations. Only public, bounded specifications were shared. A separate bilingual documentation task is recorded on completion.

Windows executable and installer were built successfully. The packaged service passed the same two-session complete/stop/expiry and workspace-move checks. Nine served UI assets match the reviewed source in both packaged and installed services. Final locale and layout JavaScript are external bundle assets; these were refreshed after the language regression fix and before the final installer build.

The existing application was upgraded in place to 0.2.10. Its own WebView inspection/capture interface verified the single-hand page at 947×644 and 1295×828, and the multi-hand page at 1295×828. No script errors, document horizontal overflow or closed-panel layout boxes were reported. No white strip was visible in these captures. Native Acrylic remains active, with exterior capture/refraction false. The existing user-data directory is unchanged and the hand remained disconnected.

The bundled report was prepared before installed-window verification; this repository/release report records the final result. No physical multi-hand, glove or calibration acceptance is claimed by this UI update.

Installer SHA-256: `ebbe951d6744f4347f93ac7f5ccc1d48b45454b4f451723bee5aea3f3c3e391e`.
