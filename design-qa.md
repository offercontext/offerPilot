# Memory profile design QA

Visual/browser result: passed on 2026-11-15. Release readiness remains subject to the complete gate; this report does not promote the draft PR to a release.

## Source and actual implementation

- Approved image: `exec-8e8ca2ef-634b-4e6d-b53a-6ec694f88219.png`, 1672 × 941, actually opened from the supplied workspace path and compared with rendered implementation screenshots.
- Browser run: [43679506195](https://github.com/offercontext/offerPilot/actions/runs/43679506195), attempt 1. PR head `6ae2e596c97d513a516fc6f1f371ec75f83e55a8`; checkout merge `59c79a50ab78226aca991e3cc37dbe8a6184b87a`; tree `7fb8547d5e1fb742b8e779fedeffe2210fe31670`; clean checkout. Subsequent evidence-only commits do not change this tested product source.
- Artifact `14147349590`: 14 original PNG screenshots, report and server log. ZIP SHA-256 `724e9efa265d30da4ce679d82b569533da0c99930189d39e87d60502aa15ce84`, verified after download. Actions retention: 14 days. Machine-readable report is preserved at [browser-report.json](evidence/memory-profile/continuation/browser-report.json).
- Real downloaded Chromium renders the production build served by the actual `oc start` backend in an isolated temporary workspace. Test content is synthetic; HTTP responses and SQLite persistence are real, with no mocked route responses. Recall checks invoke the real context-source loader. No model/provider request is made.

## Observed acceptance

- 32 assertions passed: empty state, UI add, five visible desktop groups, graph/detail/source, graph-to-list selection, search/group/state filters, edit cancellation, one real POST for double submission, exactly one version increment, old-text replacement in actual recall, withdrawal/reconfirmation, version history, delete cancellation, deletion after refresh in UI/API/recall.
- 14 screenshots cover desktop Chinese dark graph/list, edit dialog, history, deletion after refresh, Chinese light, English dark/light, and 390 × 844 Chinese dark / English light overview, selection and scrolled detail.
- Desktop five-group composition and right details fit the 1672 × 941 viewport. Main text, source, date and actions were inspected in both themes/languages. Narrow layout uses vertically grouped cards; detail actions remain visible and text wraps without clipping. Horizontal navigation scroll is intentional; document horizontal overflow assertions pass.
- Page errors: 0. Console errors: 0. Unexpected external requests: 0.
- Approved direction retained: left navigation, central self, five linked browsing groups, cyan/blue/purple accents, right detail. Intentional differences: current product navigation/theme and mascot; category pills rather than the illustrative circular category artwork; no fabricated related memories, source conversation links, update dates or inference records. Displayed date is the actual last confirmation. Groups are disposable keyword projections and may misclassify text; no persisted relationships or drag-to-create edges.

## Failures and corrections retained

- Cloud preview at `terminal.local:4173` could not reach the executor's loopback service (`upstream connect error ... connection termination`). Vite on 0.0.0.0 failed with `uv_interface_addresses ... EPERM`; local Chromium failed before navigation with `process_singleton_posix.cc:297 socket() failed: Operation not permitted (1)`. No security or network permission was changed. The working alternative runs Chromium and the real backend together on a GitHub Ubuntu runner.
- Runs [43677783586](https://github.com/offercontext/offerPilot/actions/runs/43677783586) and [43678206206](https://github.com/offercontext/offerPilot/actions/runs/43678206206): refresh name matching failed due Ant Design Chinese spacing/loading accessible names. Scoped whitespace-tolerant matching and accessibility diagnostics corrected the harness.
- Run [43678445001](https://github.com/offercontext/offerPilot/actions/runs/43678445001): hidden segmented radio was not clickable. Click the visible label and assert the real radio selection. Its screenshot also verified the repaired desktop height: all five groups now fit the first viewport.
- Run [43678702735](https://github.com/offercontext/offerPilot/actions/runs/43678702735): select input was covered by its visible selection label. Click the actual visible selector; no forced clicks or synthetic state injection.
- Run [43679015074](https://github.com/offercontext/offerPilot/actions/runs/43679015074): all desktop mutation/recall assertions passed, but a real narrow-screen defect blocked clicks: navigation retained desktop `100dvh` height. The component now uses a natural-height horizontal strip below 700px. Run 43679506195 verifies the fix. See [BUGS.md](docs/BUGS.md).

## Complete-gate boundary

The unchanged official `release-gate.sh --isolate-backend-tests` was started in a separate fixed `2ba470fa` worktree. It is not represented as a latest-head pass. The first earlier non-isolated run was interrupted with exit 130 after its source snapshot changed; its partial failures are not attributed to a baseline without proof. Latest exact-head Windows full-gate status and final local-gate outcome are reported in PR #70 and its Actions checks. Skipped or cancelled workflows are not passes. No merge, publish or deployment was performed.
