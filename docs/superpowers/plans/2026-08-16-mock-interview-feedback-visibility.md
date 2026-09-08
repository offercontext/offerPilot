# Mock Interview Feedback Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Mock Interview feedback generation visibly responsive and allow one bounded repair for blank required values without weakening the evidence contract.

**Architecture:** Extend the existing bounded structural-repair classifier with `blank_value`, retaining the same frozen input and strict validator. Move operation feedback out of the conversation scroller into one top-level Studio status region, add feedback-specific loading state, and focus terminal/unknown alerts when they appear.

**Tech Stack:** Python, FastAPI, React, TypeScript, Ant Design, pytest, Vitest

---

### Task 1: Bounded `blank_value` repair

**Files:**
- Modify: `tests/test_mock_interview_ai.py`
- Modify: `src/offerpilot/ai/mock_interview.py`

- [ ] **Step 1: Write failing AI contract tests**

Add a test where the first feedback response has an empty item text and the second response is valid:

```python
def test_feedback_blank_value_is_repaired_once():
    blank = _feedback(strengths=[{
        "id": "strength-1",
        "text": "",
        "evidence_refs": [{
            "source": "turn",
            "path": "/turns/001/answer",
            "excerpt": "我做过 Python 服务",
        }],
    }])
    model = _QuestionRepairModel([
        json.dumps(blank, ensure_ascii=False),
        json.dumps(_feedback(), ensure_ascii=False),
    ])

    proposal, diagnostic = generate_feedback(model, _snapshot(), _turns())

    assert proposal == _feedback()
    assert diagnostic["repair_attempted"] is True
    assert diagnostic["repair_count"] == 1
    assert model.calls == 2
    assert "blank_value" in model.messages[1][0].content
```

Add a second test proving two blank responses remain terminal and bounded:

```python
def test_feedback_repeated_blank_value_is_terminal_after_one_repair():
    blank = _feedback(strengths=[{
        "id": "strength-1",
        "text": "",
        "evidence_refs": [{
            "source": "turn",
            "path": "/turns/001/answer",
            "excerpt": "我做过 Python 服务",
        }],
    }])
    model = _QuestionRepairModel([
        json.dumps(blank, ensure_ascii=False),
        json.dumps(blank, ensure_ascii=False),
    ])

    with pytest.raises(MockInterviewUnverifiableError) as error:
        generate_feedback(model, _snapshot(), _turns())

    assert error.value.category == "blank_value"
    assert error.value.diagnostic["failure_categories"] == ["blank_value", "blank_value"]
    assert model.calls == 2
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
uv run pytest tests/test_mock_interview_ai.py -q
```

Expected: the first test raises `MockInterviewUnverifiableError` after one call because `blank_value` is not yet repairable.

- [ ] **Step 3: Implement the minimal repair classification**

Add `blank_value` to `_FORMAT_REPAIR_CATEGORIES` in `src/offerpilot/ai/mock_interview.py`:

```python
_FORMAT_REPAIR_CATEGORIES = {
    "invalid_json",
    "duplicate_key",
    "root_not_object",
    "unexpected_field",
    "field_type",
    "item_shape",
    "blank_value",
    "evidence_refs_not_array",
    "evidence_ref_not_object",
    "evidence_ref_missing_field",
    "evidence_ref_unexpected_field",
    "evidence_ref_field_type",
    "missing_turn_evidence",
}
```

Do not add `unknown_evidence_ref`, `excerpt_mismatch`, `limit_exceeded`, `missing_evidence_ref`, `duplicate_question`, or `missing_latest_turn_evidence`.

- [ ] **Step 4: Run the AI tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass and both new tests report exactly two Provider calls.

### Task 2: Top-level feedback status and focus

**Files:**
- Modify: `web/src/features/interviewStudio/InterviewStudio.test.tsx`
- Modify: `web/src/features/interviewStudio/InterviewStudio.tsx`
- Modify: `web/src/features/interviewStudio/InterviewStudio.module.css`

- [ ] **Step 1: Write a failing mounted loading-state test**

Use a deferred finish promise and render the real Studio. Submit one confirmed answer, then click the finish button:

```tsx
let resolveFinish!: (value: MockInterviewProposalResponse) => void;
serviceSpies.finish.mockReturnValueOnce(new Promise((resolve) => { resolveFinish = resolve; }));

await act(async () => { button('结束并生成复盘').click(); await Promise.resolve(); });

const status = host!.querySelector<HTMLElement>('[data-interview-studio-status]');
expect(status?.textContent).toContain('正在生成复盘，通常需要几十秒');
expect(button('正在生成复盘').getAttribute('aria-busy')).toBe('true');
expect(serviceSpies.finish).toHaveBeenCalledTimes(1);
await act(async () => { button('正在生成复盘').click(); await Promise.resolve(); });
expect(serviceSpies.finish).toHaveBeenCalledTimes(1);
```

Resolve the deferred response at the end of the test so unmount does not leave pending work.

- [ ] **Step 2: Write a failing terminal-error visibility test**

Make `finish` reject with `502 mock_interview_unverifiable`, then assert the alert is outside the conversation scroller and receives focus:

```tsx
serviceSpies.finish.mockRejectedValueOnce({
  response: { status: 502, data: { error_code: 'mock_interview_unverifiable', attempt_id: 41 } },
});

await act(async () => { button('结束并生成复盘').click(); await Promise.resolve(); });
await act(async () => { await new Promise((resolve) => window.requestAnimationFrame(resolve)); });

const status = host!.querySelector<HTMLElement>('[data-interview-studio-status]');
expect(status?.textContent).toContain('AI 输出未通过证据验证');
expect(status?.closest('[data-interview-conversation-scroll]')).toBeNull();
expect(document.activeElement).toBe(status);
expect(status?.textContent).toContain('重新开始练习');
```

Add the same location assertion for a `mock_interview_feedback_result_unknown` rejection and verify the original-key retry action remains available.

- [ ] **Step 3: Run the mounted tests and verify RED**

Run:

```powershell
cd web
npm.cmd test -- --run src/features/interviewStudio/InterviewStudio.test.tsx
```

Expected: loading copy, top-level status marker, focus, or location assertions fail because the current alerts remain inside the scroller and the button has no loading state.

- [ ] **Step 4: Implement one top-level status region**

In `InterviewStudio.tsx`, add refs and derived state:

```tsx
const studioStatusRef = useRef<HTMLDivElement | null>(null);
const feedbackGenerating = working && state?.pendingOperation === 'feedback';

useEffect(() => {
  if (!terminalFailure && state?.phase !== 'result_unknown') return;
  const frame = window.requestAnimationFrame(() => studioStatusRef.current?.focus());
  return () => window.cancelAnimationFrame(frame);
}, [state?.phase, terminalFailure]);
```

Render one status region after `</header>` and before `<main>`:

```tsx
<div
  ref={studioStatusRef}
  className={styles.studioStatus}
  data-interview-studio-status
  tabIndex={terminalFailure || state?.phase === 'result_unknown' ? -1 : undefined}
  aria-live={feedbackGenerating ? 'polite' : undefined}
>
  {feedbackGenerating ? (
    <Alert type="info" showIcon message="正在生成复盘，通常需要几十秒，请勿重复点击。" />
  ) : startError ? (
    <Alert type="warning" showIcon message={startError} action={<Button size="small" onClick={retry} disabled={working}>使用原 key 重试</Button>} />
  ) : terminalFailure ? (
    <Alert type="warning" showIcon message={terminalFailure.message} action={<Button size="small" onClick={() => void restartAfterTerminalFailure()} disabled={working}>重新开始练习</Button>} />
  ) : state?.phase === 'result_unknown' && state.error ? (
    <Alert type="warning" showIcon message={state.error} action={<Button size="small" onClick={retry} disabled={working}>使用原 key 重试</Button>} />
  ) : null}
</div>
```

Only render the wrapper when one of those states is active. Remove the three corresponding Alerts from `.conversationScroll`, add `data-interview-conversation-scroll` to that element, and leave voice-review save alerts in the conversation area because they are not business-feedback operations.

Update the finish button:

```tsx
<Button
  onClick={() => void finish()}
  loading={feedbackGenerating}
  disabled={
    !attemptId
    || !state
    || working
    || Boolean(proposal)
    || state?.phase === 'result_unknown'
    || state?.phase === 'contract_failed'
    || [
      'preflight',
      'reading_question',
      'waiting_for_speech',
      'listening',
      'end_candidate',
      'transcribing',
      'reviewing_transcript',
      'submitting_confirmed_answer',
      'generating_next_question',
      'paused',
      'result_unknown',
    ].includes(continuousState.status)
  }
>
  {feedbackGenerating ? '正在生成复盘…' : '结束并生成复盘'}
</Button>
```

In `InterviewStudio.module.css`, give `.studioStatus` full-width placement below the topbar, bounded padding, and no independent overflow:

```css
.studioStatus {
  padding: 10px clamp(16px, 2.2vw, 32px) 0;
  background: var(--op-bg-page);
}

.studioStatus:focus { outline: none; }
.studioStatus:focus-visible { box-shadow: var(--op-focus-ring); }
```

- [ ] **Step 5: Run the mounted tests and verify GREEN**

Run the Step 3 command. Expected: all tests pass; the deferred request is called once and terminal/unknown status is visible outside the scroller.

### Task 3: API regression and complete verification

**Files:**
- Modify: `tests/test_mock_interview_api.py`
- Modify: `docs/reports/2026-08-15-continuous-voice-interview-release.md`

- [ ] **Step 1: Add API repair success and terminal regressions**

Add this controlled model. It returns a valid first question, then consumes the supplied feedback outputs only for feedback calls:

```python
class _FeedbackRepairModel:
    supports_json_schema = False

    def __init__(self, feedback_outputs: list[dict[str, object]]) -> None:
        self.feedback_outputs = iter(feedback_outputs)
        self.feedback_calls = 0

    def complete(self, messages, tools, **kwargs):
        if any("mock-interview-feedback-v1" in message.content for message in messages):
            self.feedback_calls += 1
            return Assistant(content=json.dumps(next(self.feedback_outputs), ensure_ascii=False))
        return Assistant(
            content='{"question":"请结合 JD 说明你会如何准备。","evidence_ids":["ev_001"]}'
        )
```

Add a local helper for the two outputs:

```python
def _feedback_with_text(text: str) -> dict[str, object]:
    return {
        "schema_version": "mock-interview-feedback-v1",
        "proposal_status": "normal",
        "strengths": [{
            "id": "strength-1",
            "text": text,
            "evidence_refs": [{
                "source": "turn",
                "path": "/turns/001/answer",
                "excerpt": "我使用 Python 处理过线上问题。",
            }],
        }],
        "practice_points": [],
        "follow_up_questions": [],
        "next_practice_steps": [],
    }
```

For each model, create the Attempt through `_client`, submit turn 1, and call `/finish`:

```python
started = client.post(base, json={
    "resume_id": resume_id,
    "jd_version_id": 1,
    "attempt_idempotency_key": attempt_key,
    "initial_question_idempotency_key": question_key,
}).json()
attempt_id = started["attempt_id"]
answer = client.post(f"{base}/{attempt_id}/turns", json={
    "turn_no": 1,
    "answer_text": "我使用 Python 处理过线上问题。",
    "turn_idempotency_key": answer_key,
})
assert answer.status_code == 200
finished = client.post(f"{base}/{attempt_id}/finish", json={
    "feedback_idempotency_key": feedback_key,
})
```

For the repaired model, use `[_feedback_with_text(""), _feedback_with_text("回答包含线上排障事实。")]`. For the terminal model, use two `_feedback_with_text("")` objects. Assert:

```python
assert repaired.status_code == 201
assert repaired.json()["proposal_status"] in {"normal", "safe_empty"}
assert repaired_model.feedback_calls == 2

assert terminal.status_code == 502
assert terminal.json()["error_code"] == "mock_interview_unverifiable"
assert terminal_model.feedback_calls == 2
```

Reuse existing API setup helpers and exact frozen Turn evidence; do not add a second HTTP retry.

- [ ] **Step 2: Run focused backend and frontend verification**

Run:

```powershell
uv run pytest tests/test_mock_interview_ai.py tests/test_mock_interview_api.py tests/test_interview_practice_case_api.py tests/test_mock_interview_repository.py tests/test_mock_interview_review_drafts.py tests/test_mock_interview_browser_harness.py tests/test_smoke.py -q
cd web
npm.cmd test -- --run src/features/interviewStudio/InterviewStudio.test.tsx src/features/interviewStudio/interviewStudioController.test.ts
npm.cmd run build
```

Expected: all commands exit 0.

- [ ] **Step 3: Run static checks**

Run:

```powershell
uv run ruff check src/offerpilot/ai/mock_interview.py tests/test_mock_interview_ai.py tests/test_mock_interview_api.py
uv run mypy src
git diff --check
```

Expected: all commands exit 0 with no lint, type, or whitespace errors.

- [ ] **Step 4: Run one bounded real-AI acceptance**

Run exactly once:

```powershell
uv run oc verify-mock-interview --profile real-ai --static-dir web/dist
```

Record the actual result. Do not retry repeatedly if the Provider fails.

- [ ] **Step 5: Rebuild and restart the existing local deployment**

Verify port `65470` belongs to this worktree, stop only that process, then start:

```powershell
.venv\Scripts\oc.exe start --host 127.0.0.1 --port 65470
```

Reload the existing in-app browser tab and verify the page loads. Preserve Attempt 11 and all other user data; validate the fix using a new Attempt.

- [ ] **Step 6: Update the release report and commit**

Record the regression counts, real-AI outcome, browser behavior, and the fact that Attempt 11 remains unchanged. Then run:

```powershell
git add src/offerpilot/ai/mock_interview.py tests/test_mock_interview_ai.py tests/test_mock_interview_api.py web/src/features/interviewStudio/InterviewStudio.tsx web/src/features/interviewStudio/InterviewStudio.module.css web/src/features/interviewStudio/InterviewStudio.test.tsx
git add -f docs/reports/2026-08-15-continuous-voice-interview-release.md docs/superpowers/plans/2026-08-16-mock-interview-feedback-visibility.md
git commit -m "fix: AI surface mock interview feedback status"
```
