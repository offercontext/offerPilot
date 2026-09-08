# Mock Interview Follow-up Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every later Mock Interview round asks a new, evidence-backed follow-up and uses a fresh idempotency key unless recovering an unknown result.

**Architecture:** Keep the existing evidence-ID Provider contract. Add deterministic server validation for normalized duplicate questions and latest-answer evidence, strengthen the generation instruction by round, and reset the frontend question key only after a successful generation.

**Tech Stack:** Python, FastAPI, SQLAlchemy, React, TypeScript, Vitest, pytest

---

### Task 1: Server follow-up contract

**Files:**
- Modify: `tests/test_mock_interview_ai.py`
- Modify: `src/offerpilot/ai/mock_interview.py`

- [ ] **Step 1: Write failing AI contract tests**

Add tests that call `generate_question()` with one answered turn and assert:

```python
with pytest.raises(MockInterviewUnverifiableError, match="duplicate_question"):
    generate_question(model_returning_same_question, snapshot, turns)

with pytest.raises(MockInterviewUnverifiableError, match="missing_latest_turn_evidence"):
    generate_question(model_selecting_only_jd, snapshot, turns)

result = generate_question(model_selecting_latest_turn, snapshot, turns)
assert result["question"] == "你刚才提到异步编排，如何处理超时与部分失败？"
```

Also assert the Provider system prompt explicitly contains the latest turn number, the normalized history exclusion rule, and the latest answer evidence ID. Add a first-turn test proving JD/resume evidence remains valid when `turns=[]`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/test_mock_interview_ai.py -q
```

Expected: the duplicate and missing-latest-evidence cases do not raise, demonstrating the missing contract.

- [ ] **Step 3: Implement deterministic validation**

In `mock_interview.py`:

```python
def _normalize_question_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
```

Before returning a parsed later-round question:

```python
history = {_normalize_question_text(str(turn.get("question", ""))) for turn in turns}
if _normalize_question_text(question) in history:
    raise MockInterviewContractError("duplicate_question")
latest_path = f"/turns/{int(turns[-1]['turn_no']):03d}/answer"
latest_ids = {entry["id"] for entry in evidence_catalog if entry["source"] == "turn" and entry["path"] == latest_path}
if latest_ids and not latest_ids.intersection(parsed["evidence_ids"]):
    raise MockInterviewContractError("missing_latest_turn_evidence")
```

Generate a round-aware instruction: first turn asks an opening question; later turns must ask a new follow-up about a concrete implementation, difficulty, trade-off, result, or validation detail and select the latest answer evidence ID. Do not add either semantic category to `_FORMAT_REPAIR_CATEGORIES`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass.

### Task 2: API terminal behavior

**Files:**
- Modify: `tests/test_mock_interview_api.py`

- [ ] **Step 1: Add an API regression**

Use a controlled Provider that returns a valid first question, accepts turn 1, then returns the same question for turn 2. Assert:

```python
assert response.status_code == 502
assert response.json()["error_code"] == "mock_interview_unverifiable"
assert repository.get_turn(attempt_id, 2).turn_status == "contract_failed"
```

- [ ] **Step 2: Run the API test**

Run:

```powershell
uv run pytest tests/test_mock_interview_api.py -q
```

Expected: pass once Task 1 is implemented; no additional API behavior is required.

### Task 3: Frontend question-key lifecycle

**Files:**
- Modify: `web/src/features/interviewStudio/interviewStudioController.test.ts`
- Modify: `web/src/features/interviewStudio/InterviewStudio.test.tsx`
- Modify: `web/src/features/interviewStudio/interviewStudioController.ts`

- [ ] **Step 1: Write failing key lifecycle tests**

Assert the reducer clears a successful key:

```ts
const generating = reduceStudioState(initial, { type: 'question_submitting', questionKey: 'question-a' })
const ready = reduceStudioState(generating, { type: 'question_succeeded', turnNo: 2, question: 'new' })
expect(ready.questionKey).toBeNull()
```

In the mounted Studio test, complete two answer/question cycles and assert question-generation calls use two different keys. Add an unknown-result retry assertion showing the same key is retained.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
cd web
npm.cmd test -- --run src/features/interviewStudio/interviewStudioController.test.ts src/features/interviewStudio/InterviewStudio.test.tsx
```

Expected: successful generation retains the prior key and the distinct-key assertion fails.

- [ ] **Step 3: Clear the key only after success**

Change the `question_succeeded` reducer branch to set `questionKey: null`. Leave `question_submitting` and `result_unknown` unchanged so unknown recovery keeps the original key.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass.

### Task 4: Verification, deployment, and commit

**Files:**
- Modify: `docs/superpowers/specs/2026-08-16-mock-interview-follow-up-quality-design.md`
- Create: `docs/superpowers/plans/2026-08-16-mock-interview-follow-up-quality.md`

- [ ] **Step 1: Run affected backend suites**

```powershell
uv run pytest tests/test_mock_interview_ai.py tests/test_mock_interview_api.py tests/test_interview_practice_case_api.py tests/test_mock_interview_repository.py tests/test_mock_interview_review_drafts.py tests/test_smoke.py -q
```

- [ ] **Step 2: Run frontend tests and build**

```powershell
cd web
npm.cmd test -- --run src/features/interviewStudio/InterviewStudio.test.tsx src/features/interviewStudio/interviewStudioController.test.ts
npm.cmd run build
```

- [ ] **Step 3: Run static and real-AI checks**

```powershell
uv run ruff check src/offerpilot/ai/mock_interview.py tests/test_mock_interview_ai.py tests/test_mock_interview_api.py
uv run mypy src
uv run oc verify-mock-interview --profile real-ai --static-dir web/dist
git diff --check
```

Use one bounded real-Provider run only; do not retry repeatedly to manufacture a pass.

- [ ] **Step 4: Rebuild and restart the existing local deployment**

Build `web/dist`, restart only the OfferPilot process listening on `127.0.0.1:65470`, and confirm the local page loads. Do not delete the user's existing Attempt or other data.

- [ ] **Step 5: Commit**

```powershell
git add src/offerpilot/ai/mock_interview.py tests/test_mock_interview_ai.py tests/test_mock_interview_api.py web/src/features/interviewStudio/interviewStudioController.ts web/src/features/interviewStudio/interviewStudioController.test.ts web/src/features/interviewStudio/InterviewStudio.test.tsx
git add -f docs/superpowers/specs/2026-08-16-mock-interview-follow-up-quality-design.md docs/superpowers/plans/2026-08-16-mock-interview-follow-up-quality.md
git commit -m "fix: AI enforce mock interview follow-ups"
```
