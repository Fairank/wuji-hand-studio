# Ongoing maintenance and demo acceptance

Read `docs/PROJECT_STATUS.md`, `docs/QA_WORKFLOW.md` and `docs/CODE_REVIEW.md` before changing behavior. The active development baseline is recorded there; do not assume the default branch is current.

- Reuse the official CLI/SDK and the existing shared discovery, purpose-switch, session and control paths. Do not add a parallel connection manager or calibration algorithm. Preserve the established UI unless the user requests a change.
- Cover all three demo chains: single-hand connection/display/playback, glove calibration/apply/teleoperation, and multi-hand coordination. Use isolated SDK/CLI substitutes and synthetic firmware responses when hardware is unavailable; do not label them physical acceptance.
- Reproduce each failure through the real source path before fixing it. Test normal completion, failure, cancellation, interruption, explicit retry, duplicate input, service/owner/session replacement and late replies. A stale operation must not write to or stop its replacement.
- Keep the failed evidence. After a fix, rerun the original failing order and a different order. Obtain an independent review of the affected identity, ownership, persistence and cleanup boundaries.
- Validate changes to GitHub Actions workflows with the official `actionlint` tool before pushing. Keep the active workflow and its template byte-identical, and create acceptance evidence under the runner's temporary directory outside the checkout.
- Once source is frozen, run `python scripts/run_review.py` twice in separate fresh isolation directories. Both complete runs must pass without source changes, unexpected external access or lost evidence. If either fails or code changes again, fix/review and restart both passes. Also verify the affected rendered UI and real source/API flow; totals do not replace those checks.
- Repeat this cycle for every later change. Record skips and untested areas; zero observed failures within the tested scope is not a guarantee that every physical environment is fault-free.
- Keep user configuration, calibration, models, credentials and raw local evidence out of public commits. Distinguish source commits, remote CI, installers, installed programs and physical results.
