# Voice Coaching History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist user-confirmed local voice-delivery snapshots, derive deterministic personal trends, and let users return to the source interview event for a focused new Mock Interview attempt.

**Architecture:** A new immutable `VoiceCoachingSnapshot` table and focused repository own validation, idempotency, deletion, listing, and deterministic trend rules. The existing voice composer emits a confirmed local summary to the Mock Interview Drawer; a dedicated save card performs the second confirmation, while a standalone growth view reads history and navigates to a fresh Mock Interview with a local-only focus banner. Audio, PCM, interim transcripts, AI providers, Knowledge, Memory, Story, and Adaptive Practice remain outside this flow.

**Tech Stack:** Python 3.13, SQLAlchemy, SQLite, FastAPI/Pydantic, React 18, TypeScript, Axios, Ant Design, Vitest/JSDOM, CSS Modules.

---

## 0. Fixed baseline and exact file boundary

The implementation baseline is the commit that last changes this plan. After committing the plan, persist it in `%TEMP%\offerpilot-voice-coaching-history-baseline.txt`; every later PowerShell process must read the same SHA and validate it with `git cat-file -e`.

Allowed files:

```text
src/offerpilot/db.py
src/offerpilot/models.py
src/offerpilot/schemas.py
src/offerpilot/api.py
src/offerpilot/repositories/voice_coaching.py
src/offerpilot/repositories/mock_interviews.py
tests/test_voice_coaching_migrations.py
tests/test_voice_coaching_repository.py
tests/test_voice_coaching_api.py
web/src/types/voiceCoaching.ts
web/src/services/voiceCoaching.ts
web/src/services/voiceCoaching.test.ts
web/src/features/mockInterviewVoice/VoiceAnswerComposer.tsx
web/src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx
web/src/components/MockInterviewDrawer.tsx
web/src/components/MockInterviewDrawer.cleanup.interaction.test.tsx
web/src/components/MockInterviewDrawer.safety.test.ts
web/src/components/VoiceCoachingSnapshotSaveCard.tsx
web/src/components/VoiceCoachingSnapshotSaveCard.module.css
web/src/components/VoiceCoachingSnapshotSaveCard.test.tsx
web/src/components/VoiceCoachingGrowthView.tsx
web/src/components/VoiceCoachingGrowthView.module.css
web/src/components/VoiceCoachingGrowthView.test.tsx
web/src/components/InterviewV01View.tsx
web/src/components/InterviewV01View.adaptivePractice.test.tsx
web/src/components/ChatPanel/index.tsx
web/src/components/ChatPanel/VoiceCoachingPilotEntry.test.tsx
web/src/layout/AppShell.tsx
web/src/layout/AppShell.voiceCoaching.test.tsx
docs/superpowers/specs/2026-08-14-voice-coaching-history-design.md
docs/superpowers/plans/2026-08-14-voice-coaching-history.md
docs/reports/2026-08-14-voice-coaching-history-browser-acceptance.md
```

Every pre-commit scope check must combine committed, staged, unstaged, and untracked paths and fail if any path is outside this set. `README.md`, `src/offerpilot/ai/**`, existing Provider code, Knowledge, Story, Adaptive Practice, application state, and unrelated tests are forbidden.

## 1. Add migration and immutable model

**Files:**

- Modify: `src/offerpilot/models.py`
- Modify: `src/offerpilot/db.py`
- Create: `tests/test_voice_coaching_migrations.py`

- [ ] **Step 1: Write failing migration tests**

Create a fresh database and assert:

```python
tables = {row[0] for row in session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
assert "voice_coaching_snapshots" in tables
assert "0022_voice_coaching_snapshots" in migrations
assert {"0018_application_jd_versions", "0019_interview_story_library", "0020_application_outcome_feedback", "0021_adaptive_interview_practice"} <= migrations
```

Also initialize twice, assert one migration marker, inspect `PRAGMA foreign_key_list`, and prove unique `turn_id` plus global unique `idempotency_key` reject duplicates.

- [ ] **Step 2: Run RED**

Run:

```powershell
uv run pytest tests/test_voice_coaching_migrations.py -q
```

Expected: failure because the model/table/marker do not exist.

- [ ] **Step 3: Implement the model and marker**

Add `VoiceCoachingSnapshot` with the exact fields and constraints from the design. Use foreign keys with `ON DELETE CASCADE` for Attempt/Turn, `ON DELETE SET NULL` for `origin_snapshot_id`, indexes on `(created_at, id)`, `(application_id, event_id)`, and `attempt_id`, and immutable timestamp defaults. Add `_ensure_voice_coaching_schema()` after the `0021` marker in `init_database()`.

- [ ] **Step 4: Run GREEN and commit**

```powershell
uv run pytest tests/test_voice_coaching_migrations.py -q
uv run ruff check src/offerpilot/models.py src/offerpilot/db.py tests/test_voice_coaching_migrations.py
git add src/offerpilot/models.py src/offerpilot/db.py tests/test_voice_coaching_migrations.py
git commit -m "feat: AI add voice coaching snapshot schema"
```

## 2. Implement validation, idempotency, listing, and deletion

**Files:**

- Create: `src/offerpilot/repositories/voice_coaching.py`
- Create: `tests/test_voice_coaching_repository.py`

- [ ] **Step 1: Write repository RED tests**

Build real Application/Event/Attempt/Turn rows and cover:

- answered Turn creates one immutable snapshot using server question/answer text;
- request answer text is impossible because the repository signature has no such parameter;
- unsubmitted/empty Turn, cross-attempt Turn, cross-Application/Event and missing source fail closed;
- finite-number and all boundary checks from the design;
- filler Unicode code-point offsets match the confirmed answer exactly;
- same key/same fingerprint replays; same key/changed input and different key/same Turn return distinct conflicts;
- unknown-result recovery reads by exact Attempt/Turn identity;
- list is `id DESC`, cursor bounded, and physical delete is idempotent without touching Mock data.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_voice_coaching_repository.py -q
```

Expected: import failure for `offerpilot.repositories.voice_coaching`.

- [ ] **Step 3: Implement focused repository**

Define:

Define three public exceptions named `VoiceCoachingNotFound`,
`VoiceCoachingConflict`, and `VoiceCoachingValidationError`. Define a focused
`VoiceCoachingRepository` with these public operations:

```python
def create_or_replay(
    *,
    application_id: int,
    event_id: int,
    attempt_id: int,
    turn_no: int,
    idempotency_key: str,
    total_duration_ms: int,
    voiced_duration_ms: int,
    pause_count: int,
    longest_pause_ms: int,
    speech_rate_cpm: int | None,
    filler_occurrences: list[dict[str, Any]],
    reflection_text: str,
    focus_kind: str | None,
    origin_snapshot_id: int | None,
) -> tuple[dict[str, Any], bool]:
    raise NotImplementedError

def get_for_turn(
    *, application_id: int, event_id: int, attempt_id: int, turn_no: int
) -> dict[str, Any] | None:
    raise NotImplementedError

def list_snapshots(
    *, limit: int, before_id: int | None
) -> list[dict[str, Any]]:
    raise NotImplementedError

def delete_snapshot(snapshot_id: int) -> None:
    raise NotImplementedError
```

Use `BEGIN IMMEDIATE`, canonical JSON and SHA-256 helpers. Convert every input to plain bounded values before fingerprinting. Never serialize audio or log text.

- [ ] **Step 4: Run GREEN and commit**

```powershell
uv run pytest tests/test_voice_coaching_repository.py -q
uv run ruff check src/offerpilot/repositories/voice_coaching.py tests/test_voice_coaching_repository.py
git add src/offerpilot/repositories/voice_coaching.py tests/test_voice_coaching_repository.py
git commit -m "feat: AI persist confirmed voice coaching snapshots"
```

## 3. Add deterministic trends and recommendations

**Files:**

- Modify: `src/offerpilot/repositories/voice_coaching.py`
- Modify: `tests/test_voice_coaching_repository.py`

- [ ] **Step 1: Write trend RED tests**

Cover zero/one/two/ten/thirty/over-thirty records, integer median behavior, current/previous windows, exact source snapshot IDs, filler-per-minute, missing speech rate, and fixed recommendation priority:

```python
assert trend["recommendation"]["focus_kind"] == "long_pause_control"
assert trend["recommendation"]["source_snapshot_ids"] == [latest_id, prior_id]
```

Assert no recommendation when thresholds do not match and no Provider/client is accepted by the trend function.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_voice_coaching_repository.py -q -k trend
```

- [ ] **Step 3: Implement pure trend functions**

Add pure helpers for median, filler rate, direction deltas and recommendation selection, then expose `VoiceCoachingRepository.trends()`. Keep stable tie-breaking: long pause, filler reduction, pace consistency.

- [ ] **Step 4: Run GREEN and commit**

```powershell
uv run pytest tests/test_voice_coaching_repository.py -q
git add src/offerpilot/repositories/voice_coaching.py tests/test_voice_coaching_repository.py
git commit -m "feat: AI derive deterministic voice coaching trends"
```

## 4. Expose strict API contracts

**Files:**

- Modify: `src/offerpilot/schemas.py`
- Modify: `src/offerpilot/api.py`
- Create: `tests/test_voice_coaching_api.py`

- [ ] **Step 1: Write API RED tests**

Use `TestClient` and real repository state to prove all five endpoints, exact status codes, stable error codes, pagination, physical delete, and zero AI/Provider calls. Include request fields with `NaN`, `Infinity`, extra keys, oversized reflection, malformed offsets, unknown-result same-key recovery, and cross-resource IDs.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_voice_coaching_api.py -q
```

- [ ] **Step 3: Implement schemas and routes**

Create strict Pydantic input/output models with `extra="forbid"`, instantiate the repository inside `create_app`, register static trend/list routes before any dynamic path that could shadow them, and map:

```text
404 voice_coaching_source_not_found
409 voice_coaching_idempotency_conflict
409 voice_coaching_snapshot_exists
422 voice_coaching_invalid_payload
```

API diagnostics may log IDs, field counts, request fingerprint and code only.

- [ ] **Step 4: Run GREEN and commit**

```powershell
uv run pytest tests/test_voice_coaching_api.py tests/test_voice_coaching_repository.py tests/test_voice_coaching_migrations.py -q
uv run ruff check src/offerpilot tests/test_voice_coaching_*.py
uv run mypy src
git add src/offerpilot/schemas.py src/offerpilot/api.py tests/test_voice_coaching_api.py
git commit -m "feat: AI expose voice coaching history API"
```

## 5. Add frontend contracts and recoverable save card

**Files:**

- Create: `web/src/types/voiceCoaching.ts`
- Create: `web/src/services/voiceCoaching.ts`
- Create: `web/src/services/voiceCoaching.test.ts`
- Create: `web/src/components/VoiceCoachingSnapshotSaveCard.tsx`
- Create: `web/src/components/VoiceCoachingSnapshotSaveCard.module.css`
- Create: `web/src/components/VoiceCoachingSnapshotSaveCard.test.tsx`
- Modify: `web/src/features/mockInterviewVoice/VoiceAnswerComposer.tsx`
- Modify: `web/src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx`
- Modify: `web/src/components/MockInterviewDrawer.tsx`
- Modify: `web/src/components/MockInterviewDrawer.cleanup.interaction.test.tsx`

- [ ] **Step 1: Write service and component RED tests**

Prove endpoint paths and errors; Composer emits `{ confirmedText, summary }` only after explicit transcript confirmation; Drawer retains the summary through successful answer submission; the save card is absent before submission, saves with a stable key, freezes on unknown result, reads exact Turn state, replays only the original key, and discards only local summary on close.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd test -- --run src/services/voiceCoaching.test.ts src/components/VoiceCoachingSnapshotSaveCard.test.tsx src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx src/components/MockInterviewDrawer.cleanup.interaction.test.tsx
```

- [ ] **Step 3: Implement minimal frontend flow**

Add `onConfirmedVoiceReview` to Composer. Extend `MockInterviewDrawerDraft` with a serializable pending summary, focus metadata, save key/status and saved snapshot ID. Render the new card only when `answerSubmitted` and the summary belongs to the current Attempt/Turn. Do not put Blob, PCM, object URLs or interim transcript in AppShell state.

- [ ] **Step 4: Run GREEN and commit**

```powershell
npm.cmd test -- --run src/services/voiceCoaching.test.ts src/components/VoiceCoachingSnapshotSaveCard.test.tsx src/features/mockInterviewVoice/VoiceAnswerComposer.test.tsx src/components/MockInterviewDrawer.cleanup.interaction.test.tsx
git add web/src/types/voiceCoaching.ts web/src/services/voiceCoaching.ts web/src/services/voiceCoaching.test.ts web/src/components/VoiceCoachingSnapshotSaveCard* web/src/features/mockInterviewVoice/VoiceAnswerComposer* web/src/components/MockInterviewDrawer.tsx web/src/components/MockInterviewDrawer.cleanup.interaction.test.tsx
git commit -m "feat: AI save confirmed voice coaching reviews"
```

## 6. Build growth view and focused re-practice navigation

**Files:**

- Create: `web/src/components/VoiceCoachingGrowthView.tsx`
- Create: `web/src/components/VoiceCoachingGrowthView.module.css`
- Create: `web/src/components/VoiceCoachingGrowthView.test.tsx`
- Modify: `web/src/components/InterviewV01View.tsx`
- Modify: `web/src/components/InterviewV01View.adaptivePractice.test.tsx`
- Modify: `web/src/layout/AppShell.tsx`
- Create: `web/src/layout/AppShell.voiceCoaching.test.tsx`
- Modify: `web/src/components/ChatPanel/index.tsx`
- Create: `web/src/components/ChatPanel/VoiceCoachingPilotEntry.test.tsx`

- [ ] **Step 1: Write mounted RED tests**

Cover loading/error/partial/empty, trend source labels, long text, delete confirmation, deletion refresh, disabled source, exact Application/Event navigation, new Attempt draft reset, focus banner, origin snapshot propagation, and Pilot quick entry navigation with zero chat/AI calls.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd test -- --run src/components/VoiceCoachingGrowthView.test.tsx src/components/InterviewV01View.adaptivePractice.test.tsx src/layout/AppShell.voiceCoaching.test.tsx src/components/ChatPanel/VoiceCoachingPilotEntry.test.tsx
```

- [ ] **Step 3: Implement the view and navigation**

Use a restrained bright card layout consistent with the Interview Story and current dashboard surfaces. The growth view owns list/trend fetch and deletion. AppShell owns a `voiceCoachingGrowthOpen` state and a one-shot focus `{ applicationId, eventId, focusKind, originSnapshotId }`; opening the target Mock Interview clears the old draft and injects only this local focus. ChatPanel renders a local “查看表达成长” button that calls the navigation callback and never sends a message.

- [ ] **Step 4: Run GREEN, accessibility checks and commit**

```powershell
npm.cmd test -- --run src/components/VoiceCoachingGrowthView.test.tsx src/components/InterviewV01View.adaptivePractice.test.tsx src/layout/AppShell.voiceCoaching.test.tsx src/components/ChatPanel/VoiceCoachingPilotEntry.test.tsx
npm.cmd run build
git add web/src/components/VoiceCoachingGrowthView* web/src/components/InterviewV01View.tsx web/src/components/InterviewV01View.adaptivePractice.test.tsx web/src/layout/AppShell.tsx web/src/layout/AppShell.voiceCoaching.test.tsx web/src/components/ChatPanel/index.tsx web/src/components/ChatPanel/VoiceCoachingPilotEntry.test.tsx
git commit -m "feat: AI add voice coaching growth workspace"
```

## 7. Browser acceptance, independent review, and release gates

**Files:**

- Create: `docs/reports/2026-08-14-voice-coaching-history-browser-acceptance.md`

- [ ] **Step 1: Run focused and complete gates**

```powershell
uv run pytest tests/test_voice_coaching_migrations.py tests/test_voice_coaching_repository.py tests/test_voice_coaching_api.py -q
uv run ruff check .
uv run mypy src
cd web
npm.cmd test -- --run
npm.cmd run build
cd ..
uv run oc smoke --static-dir web/dist
uv run oc verify --profile local --static-dir web/dist
git diff --check "$baseline..HEAD"
```

Run the repository's current five-group backend and ten-group frontend release gates if their manifests are available; validate manifest union, duplicate IDs, allowed skips, source fingerprints and aggregate freshness rather than substituting one long timeout-prone command.

- [ ] **Step 2: Run independent code review before browser acceptance**

Review migration coexistence, text privacy, offset validation, idempotency recovery, cross-resource ownership, stale query cleanup, navigation isolation, Pilot zero-message behavior, and no audio/provider writes. Fix every P0/P1/P2 with a failing regression test first.

- [ ] **Step 3: Run browser acceptance**

Use the in-app browser, Chinese candidate “筱哲”, light mode, viewport at least `1440×900`. Inject only local Mock PCM if microphone automation is unavailable and visibly disclose it. Capture:

1. submitted answer with the “保存到成长档案” card;
2. growth overview with at least two records, trend and next drill;
3. source record detail and delete boundary;
4. focused new Mock Interview showing the local training banner;
5. Pilot local quick entry.

Audit zero audio/PCM payloads, zero AI/Provider calls for save/list/trend/delete/navigation, and zero Knowledge/Memory/Story/Application writes.

- [ ] **Step 4: Write and force-add report**

Record dimensions, screenshot hashes, mock limitation, request/write audit, cleanup, exact gate results, destructive changes and remaining risks.

```powershell
git add -f docs/reports/2026-08-14-voice-coaching-history-browser-acceptance.md
git commit -m "docs: AI record voice coaching history acceptance"
```

- [ ] **Step 5: Final scope and cleanup**

Recompute the exact changed-file set from the fixed baseline and require zero outside-allowlist paths. Confirm no service, browser, audio context, microphone stream, Provider proxy, port, temporary database, model cache created by the test, or browser tab remains. Remove the baseline locator only after every final gate succeeds. Leave the branch unpushed and unmerged unless the user explicitly requests integration.
