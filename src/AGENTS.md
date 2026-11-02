## Code Review Rules

- Flag discovery, selection, navigation or refresh that opens a device or starts motion without an explicit user action. Reuse the existing purpose-switch and official SDK/CLI path; read-only discovery may use the existing session without connecting another device.
- Flag missing exact device/person/side/model identity or request/session/attempt guards before a write, follow, cancel or cleanup. A stale command must not stop or alter a newer owner; unknown exit still requires exact explicit recovery.
- Flag stale responses, frames or completion records accepted after their service, owner, run or view changed. Verify the real API DTO and producer ordering; a safe unchanged identity or current newer source frame is not itself a defect.
- Flag changes that overwrite calibration/settings or lose failed-operation recovery evidence, and unrequested UI layout/navigation/copy/asset changes. Mechanical checks and test totals do not prove the affected user flow or physical success.

Review procedure and evidence requirements: [docs/CODE_REVIEW.md](../docs/CODE_REVIEW.md).
