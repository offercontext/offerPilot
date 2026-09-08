# Release validation report: evidence-gated interview preparation

Date: 2026-07-30
Branch: `feat/20260724-evidence-gated-interview-preparation`
Validation HEAD: `056d9d6` (code under validation; this report is updated separately)
Status: not pushed, not merged

## Narrow fix in this validation round

- Normal `strengths`, `practice_points`, and `next_practice_steps` items now require at least one completed-turn evidence reference with `source="turn"`.
- JD and Resume references remain supplementary and cannot replace Turn evidence.
- Missing Turn evidence is eligible for at most one constraint-completion repair. Unknown paths, excerpt mismatches, forged references, and limits remain terminal contract failures.
- Evidence references are validated before the Turn-evidence repair category is considered; invalid source, path, or excerpt failures therefore cannot trigger a repair call.
- Repair uses the same frozen input and does not include the prior model response. Two failed validations remain `502 mock_interview_unverifiable`; no Proposal is written.

## Verification after the fix

- `uv run pytest tests/test_mock_interview_ai.py -q`: exit 0, 29 passed.
- All Mock Interview test files (`tests/test_mock_interview*.py`): exit 0, 106 passed.
- `uv run ruff check src/offerpilot/ai/mock_interview.py tests/test_mock_interview_ai.py`: exit 0.
- `uv run mypy src/offerpilot/ai/mock_interview.py`: exit 0.
- `uv run oc verify --profile real-ai --static-dir web/dist`: exit 0.
  - Interview feedback completed after two turns.
  - Other real-AI flows also completed, including Opportunity Fit, material proposal, interview preparation, interview review, and knowledge capture.
  - Mock Interview bounded attempts ended with `attempt_1:feedback:mock_interview_unverifiable:excerpt_mismatch`, `attempt_2:question_2:mock_interview_unverifiable:excerpt_mismatch`, then `attempt_3:success`; terminal failures were preserved and the successful retry used a new Attempt.
- `uv run oc verify-mock-interview --static-dir web/dist`: exit 0, `attempt_1:success`.

The earlier full release gate at `399462c` passed: backend groups covered 1630 collected node IDs with no duplicates and only the four defined Windows symlink-capability skips; Ruff, Mypy, 203 frontend suites / 657 tests, TypeScript, production build, local smoke, and isolated local verify all exited 0. On final code HEAD `7fe71d7`, the persisted five-group workflow completed and its aggregate passed: 1637 collected node IDs, no duplicates, 1633 passed, 4 allowed symlink-capability skips, and 0 failures/errors. Group totals were agent 423/0, domain 70/0, knowledge 658/4 skips, proposals 283/0, and misc 203/0. The manifest, each JUnit file, each completion marker, skip node IDs/reasons, and aggregate coverage were all validated. Ruff, Mypy, frontend tests, TypeScript, production build, local smoke, local verify, real-AI verify, and the isolated Mock Interview API verify also exited 0.

## Browser evidence

- The previously completed local read-only browser walk-through remains valid: dashboard suggestion, snooze/ignore session behavior, detail navigation, no write calls, and no console errors.
- A fresh isolated browser/CDP run completed the real two-turn flow, feedback generation, user selection, and confirmation. The review-draft confirmation returned HTTP `201`; the drawer was then closed and reopened immediately, and the read-only history displayed the confirmed review draft and frozen turn content. The CDP auditor verified the dedicated target's complete key-request sequence and local-browser allowlist; the server-side Provider egress check matched the configured Provider endpoint tuple.
- The successful run did not need the result-unknown confirmation retry. The existing retry tests and prior browser evidence cover retention of the original `attemptId`, `confirmationKey`, and `selected_blocks`; this run confirms the normal 201/history path.

## Cleanup and remaining risk

- Temporary isolated data, services, provider proxy, standalone browser, and CDP auditor were stopped and removed.
- No secret, Resume/JD content, or model output was recorded in this report.
- No push or merge was performed.
- Remaining release risk: Provider output remains externally variable, but the isolated browser confirmation/history path and complete grouped backend gate passed without weakening evidence or HITL rules. This branch remains unpushed and unmerged pending release review.
