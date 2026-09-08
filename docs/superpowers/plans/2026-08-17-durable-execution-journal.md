# Durable Execution Journal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a three-table, privacy-bounded, fail-open Durable Execution Journal that records Agent Run causality without changing chat, confirmation, SSE, Provider, tool, or business-write behavior.

**Architecture:** `AgentRunRepository` owns short SQLite transactions and immutable event ordering; `RunRecorderFactory` creates a per-segment `SafeRunRecorder` or `NullRunRecorder`; `events.py` owns strict canonicalization, reference normalization, HMAC fingerprints, bounded manifests, and digest formulas. API routes create or resume logical Runs, while `LangGraphAgentRunner` emits model/tool lifecycle facts through the recorder. The Journal remains a side channel: existing Conversation, Pending Action, confirmation, lease/CAS/fencing, SSE, and business repositories remain authoritative.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, SQLite, LangGraph, Pydantic-compatible JSON validation, pytest, PowerShell release gates.

---

## 0. Fixed execution boundary

Work only in:

```text
D:\Users\yuqi.chen\offerpilot\.worktrees\feat-20260817-durable-execution-journal
```

The source stack starts at `b0a5697`; the reviewed design is finalized through commit `c741bde`. At implementation start, the plan commit itself becomes the immutable implementation baseline.

### Exact positive allowlist

```text
src/offerpilot/models.py
src/offerpilot/db.py
src/offerpilot/repositories/agent_runs.py
src/offerpilot/agent_runtime/__init__.py
src/offerpilot/agent_runtime/events.py
src/offerpilot/agent_runtime/journal.py
src/offerpilot/agent_runtime/keyring.py
src/offerpilot/agent_runtime/trace.py
src/offerpilot/api.py
src/offerpilot/ai/agent.py
tests/test_agent_run_migrations.py
tests/test_agent_runs_repository.py
tests/test_agent_run_journal.py
tests/test_agent_run_keyring.py
tests/test_agent_run_trace.py
tests/test_ai_agent.py
tests/test_chat_api.py
tests/test_settings_api.py
tests/test_smoke.py
docs/reports/2026-08-17-durable-execution-journal-release-verification.md
```

The design and this plan are read-only after bootstrap. Any required path outside this list stops implementation and requires a plan revision before continuing. In particular, do not modify `web/**`, `src/offerpilot/ai/tools.py`, `src/offerpilot/config.py`, `src/offerpilot/sse.py`, existing business repositories, public API schemas, or `README.md`.

### Task 0: Persist baseline and allowlist

**Files:**
- Read: `docs/superpowers/specs/2026-08-17-durable-execution-journal-design.md`
- Read: `docs/superpowers/plans/2026-08-17-durable-execution-journal.md`
- Create outside repository: `%TEMP%\offerpilot-durable-journal-gate\baseline.txt`
- Create outside repository: `%TEMP%\offerpilot-durable-journal-gate\allowlist.txt`
- Create outside repository: `%TEMP%\offerpilot-durable-journal-gate.locator.json`

- [ ] **Step 1: Verify the worktree and capture the plan commit once**

Run from the worktree:

```powershell
$ErrorActionPreference = 'Stop'
$repoRoot = (Get-Location).Path
if ((git status --short).Count -ne 0) { throw 'implementation must start from a clean worktree' }
$baseline = (git log -1 --format=%H -- docs/superpowers/plans/2026-08-17-durable-execution-journal.md).Trim()
if (-not $baseline) { throw 'plan baseline is missing' }
if ((git rev-parse HEAD).Trim() -ne $baseline) { throw 'HEAD must equal the reviewed plan commit at bootstrap' }
if ((git cat-file -t $baseline).Trim() -ne 'commit') { throw 'baseline is not a commit' }
```

Expected: no output and exit code 0.

- [ ] **Step 2: Persist one immutable allowlist source**

```powershell
$gateRoot = Join-Path $env:TEMP 'offerpilot-durable-journal-gate'
$locator = Join-Path $env:TEMP 'offerpilot-durable-journal-gate.locator.json'
New-Item -ItemType Directory -Force -Path $gateRoot | Out-Null
$allowlist = @(
  'src/offerpilot/models.py',
  'src/offerpilot/db.py',
  'src/offerpilot/repositories/agent_runs.py',
  'src/offerpilot/agent_runtime/__init__.py',
  'src/offerpilot/agent_runtime/events.py',
  'src/offerpilot/agent_runtime/journal.py',
  'src/offerpilot/agent_runtime/keyring.py',
  'src/offerpilot/agent_runtime/trace.py',
  'src/offerpilot/api.py',
  'src/offerpilot/ai/agent.py',
  'tests/test_agent_run_migrations.py',
  'tests/test_agent_runs_repository.py',
  'tests/test_agent_run_journal.py',
  'tests/test_agent_run_keyring.py',
  'tests/test_agent_run_trace.py',
  'tests/test_ai_agent.py',
  'tests/test_chat_api.py',
  'tests/test_settings_api.py',
  'tests/test_smoke.py',
  'docs/reports/2026-08-17-durable-execution-journal-release-verification.md'
)
$baselinePath = Join-Path $gateRoot 'baseline.txt'
$allowlistPath = Join-Path $gateRoot 'allowlist.txt'
$baseline | Set-Content -LiteralPath $baselinePath -Encoding ascii -NoNewline
$allowlist | Set-Content -LiteralPath $allowlistPath -Encoding utf8
[ordered]@{
  repository_root = $repoRoot
  baseline_path = $baselinePath
  baseline_sha = $baseline
  allowlist_path = $allowlistPath
  allowlist_sha256 = (Get-FileHash -Algorithm SHA256 $allowlistPath).Hash.ToLowerInvariant()
} | ConvertTo-Json | Set-Content -LiteralPath $locator -Encoding utf8
```

Expected: locator, baseline, and allowlist exist outside the worktree.

- [ ] **Step 3: Run the reusable scope assertion**

Every later independent PowerShell process must first load and validate the locator, then evaluate committed, staged, unstaged, and untracked paths:

```powershell
$locator = Get-Content -Raw (Join-Path $env:TEMP 'offerpilot-durable-journal-gate.locator.json') | ConvertFrom-Json
if ((Get-Location).Path -ne [string]$locator.repository_root) { throw 'wrong worktree' }
$baseline = (Get-Content -Raw -LiteralPath $locator.baseline_path).Trim()
if ($baseline -ne [string]$locator.baseline_sha) { throw 'baseline file changed' }
if ((git cat-file -t $baseline).Trim() -ne 'commit') { throw 'baseline no longer resolves' }
if ((Get-FileHash -Algorithm SHA256 $locator.allowlist_path).Hash.ToLowerInvariant() -ne [string]$locator.allowlist_sha256) { throw 'allowlist changed' }
$allowed = @{}
Get-Content -LiteralPath $locator.allowlist_path | ForEach-Object { $allowed[$_.Trim().Replace('\','/')] = $true }
$paths = @(
  git diff --name-only "$baseline..HEAD"
  git diff --cached --name-only
  git diff --name-only
  git ls-files --others --exclude-standard
) | Where-Object { $_ } | ForEach-Object { $_.Trim().Replace('\','/') } | Sort-Object -Unique
$outside = @($paths | Where-Object { -not $allowed.ContainsKey($_) })
if ($outside.Count -gt 0) { throw "outside allowlist: $($outside -join ', ')" }
```

Expected: exit code 0. Do not recompute `$baseline` after this task.

---

### Task 1: Journal key domain and backup exclusion

**Files:**
- Create: `src/offerpilot/agent_runtime/keyring.py`
- Create: `tests/test_agent_run_keyring.py`
- Modify: `src/offerpilot/api.py:10014-10027`
- Modify: `tests/test_settings_api.py:497-555`
- Create: `src/offerpilot/agent_runtime/__init__.py`

- [ ] **Step 1: Write key lifecycle failure tests**

Add tests with these concrete assertions:

```python
def test_journal_key_round_trips_from_dedicated_file(tmp_path):
    first = load_or_create_journal_key(tmp_path)
    second = load_or_create_journal_key(tmp_path)
    assert first is not None
    assert second == first
    assert first.key_id == str(UUID(first.key_id))
    assert len(first.secret) == 32
    payload = json.loads((tmp_path / JOURNAL_KEY_FILENAME).read_text("utf-8"))
    assert payload == {
        "schema_version": 1,
        "key_id": first.key_id,
        "secret": base64.urlsafe_b64encode(first.secret).decode("ascii").rstrip("="),
    }


def test_journal_key_persist_failure_disables_journal_without_ephemeral_key(tmp_path):
    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("synthetic write failure with private path")

    assert load_or_create_journal_key(tmp_path, replace_file=fail_replace) is None
    assert not (tmp_path / JOURNAL_KEY_FILENAME).exists()
```

On POSIX, assert `stat.S_IMODE(path.stat().st_mode) == 0o600`. On Windows, inject `platform_name="nt"` and a `chmod_file` spy, assert the file remains inside the resolved data directory and the POSIX chmod callback is not invoked.

- [ ] **Step 2: Run the key tests and verify red**

Run:

```powershell
uv run pytest tests/test_agent_run_keyring.py -q
```

Expected: FAIL because `offerpilot.agent_runtime.keyring` does not exist.

- [ ] **Step 3: Implement the dedicated key file**

Create these public module contracts:

```python
JOURNAL_KEY_FILENAME = "agent-journal-key.json"


@dataclass(frozen=True)
class JournalKeyDomain:
    key_id: str
    secret: bytes


def load_or_create_journal_key(
    data_dir: Path,
    *,
    replace_file: Callable[[Path, Path], None] = os.replace,
    platform_name: str = os.name,
    chmod_file: Callable[[Path, int], None] = os.chmod,
) -> JournalKeyDomain | None:
    """Return a persisted key domain, or None without raising when persistence is unavailable."""
```

Implementation rules:

```text
existing file -> require schema_version=1, canonical lowercase UUID, 32 decoded bytes
missing file  -> generate uuid4 + secrets.token_bytes(32), write sibling temp, fsync, atomic replace
POSIX         -> chmod temp and final path to 0600
Windows       -> inherit the current-user data-directory ACL; never broaden it
any ordinary read/validation/write/replace Exception -> delete the temp best-effort and return None
BaseException -> propagate unchanged
```

Export only `JOURNAL_KEY_FILENAME`, `JournalKeyDomain`, and `load_or_create_journal_key` from `agent_runtime/__init__.py`. Do not place Secret or key ID in `Config`.

- [ ] **Step 4: Make both backup paths exclude the key file**

In `_build_backup_archive`, skip the exact relative path before any file read:

```python
if archive_path == JOURNAL_KEY_FILENAME:
    continue
```

Extend `tests/test_settings_api.py` so `/api/settings` and `/api/settings/backup` omit the filename, Secret, and current key ID. For `/api/backups/export`, assert the ZIP omits `agent-journal-key.json` and the Secret while `data.db` still contains the non-secret historical `fingerprint_key_id` required to interpret old rows. Update settings once and assert the key file bytes are unchanged.

- [ ] **Step 5: Verify green and commit**

Run:

```powershell
uv run pytest tests/test_agent_run_keyring.py tests/test_settings_api.py -q
uv run ruff check src/offerpilot/agent_runtime/keyring.py src/offerpilot/api.py tests/test_agent_run_keyring.py tests/test_settings_api.py
```

Expected: all selected tests pass and Ruff exits 0.

Commit with separate commands:

```powershell
git add src/offerpilot/agent_runtime/__init__.py src/offerpilot/agent_runtime/keyring.py src/offerpilot/api.py tests/test_agent_run_keyring.py tests/test_settings_api.py
git commit -m "feat: AI add durable journal key domain"
```

---

### Task 2: Three-table schema and `0024` migration

**Files:**
- Modify: `src/offerpilot/models.py:1336-1430`
- Modify: `src/offerpilot/db.py:34-230`
- Create: `tests/test_agent_run_migrations.py`

- [ ] **Step 1: Write migration and foreign-key tests**

Create tests that inspect all three tables and their constraints:

```python
def test_fresh_database_has_durable_journal_schema(tmp_path):
    session_factory = init_database(tmp_path / "data.db")
    engine = session_factory.kw["bind"]
    inspector = inspect(engine)
    assert {"agent_runs", "agent_events", "agent_context_snapshots"} <= set(inspector.get_table_names())
    versions = {row[0] for row in engine.connect().execute(text("SELECT version FROM schema_migrations"))}
    assert "0024_durable_execution_journal" in versions


def test_deleting_input_message_sets_null_but_deleting_conversation_cascades(tmp_path):
    run_id, conversation_id, message_id = seed_run_with_initial_events(tmp_path)
    delete_message(message_id)
    assert load_run(run_id).input_message_id is None
    delete_conversation(conversation_id)
    assert load_run(run_id) is None
    assert count_events(run_id) == 0
    assert count_snapshots(run_id) == 0
```

Also create a database at migration `0023_immersive_interview_studio`, reopen it with `init_database`, and assert `0024` is recorded once after two opens.

- [ ] **Step 2: Run migration tests and verify red**

```powershell
uv run pytest tests/test_agent_run_migrations.py -q
```

Expected: FAIL because the tables and migration marker are absent.

- [ ] **Step 3: Add the SQLAlchemy models**

Implement exactly three classes:

```python
class AgentRun(Base):
    __tablename__ = "agent_runs"


class AgentEvent(Base):
    __tablename__ = "agent_events"


class AgentContextSnapshot(Base):
    __tablename__ = "agent_context_snapshots"
```

Freeze these database rules from the design:

```text
AgentRun.conversation_id -> conversations.id ON DELETE CASCADE
AgentRun.input_message_id -> chat_messages.id ON DELETE SET NULL, nullable
AgentEvent.run_id -> agent_runs.id ON DELETE CASCADE
AgentContextSnapshot.run_id -> agent_runs.id ON DELETE CASCADE
UNIQUE(agent_events.run_id, agent_events.seq)
UNIQUE(agent_events.run_id, agent_events.dedupe_key)
UNIQUE(agent_context_snapshots.run_id, agent_context_snapshots.snapshot_key)
partial UNIQUE(agent_runs.conversation_id, agent_runs.waiting_tool_call_id)
CHECK payload UTF-8 blob length <= 4096
CHECK manifest UTF-8 blob length <= 16384
CHECK seq > 0, last_seq >= 0, recording_error_count >= 0
CHECK manifest_schema_version = 1
```

Use 36-character lowercase UUID strings for Run/Event/Snapshot/Segment/Model Call/Key Domain identities.

- [ ] **Step 4: Record migration `0024` and add a low-wait session factory**

After `Base.metadata.create_all(engine)`, record:

```python
_record_migration(
    engine,
    "0024_durable_execution_journal",
    "Add fail-open durable Agent Run journal tables",
)
```

Add an internal factory that opens the same SQLite file with an independent one-connection pool and `timeout=0.05`:

```python
def journal_session_factory_for_data_dir(data_dir: Path) -> SessionFactory:
    return _journal_session_factory(data_dir / "data.db")
```

Enable foreign keys on this engine. Do not call migrations or `create_all` from the Journal factory; startup remains owned by `init_database`.

- [ ] **Step 5: Verify green and commit**

```powershell
uv run pytest tests/test_agent_run_migrations.py tests/test_database.py -q
uv run ruff check src/offerpilot/models.py src/offerpilot/db.py tests/test_agent_run_migrations.py
```

Expected: all selected tests pass.

```powershell
git add src/offerpilot/models.py src/offerpilot/db.py tests/test_agent_run_migrations.py
git commit -m "feat: AI add durable agent journal schema"
```

---

### Task 3: Strict event, HMAC, context, and manifest contracts

**Files:**
- Create: `src/offerpilot/agent_runtime/events.py`
- Create: `tests/test_agent_run_journal.py`
- Modify: `src/offerpilot/agent_runtime/__init__.py`

- [ ] **Step 1: Write red tests for normalization and privacy**

Cover these exact cases:

```python
@pytest.mark.parametrize(
    ("context_type", "context_ref", "expected_type", "expected_entity"),
    [
        ("workspace", "private free text", "workspace", None),
        ("global", "https://private.example/path", "global", None),
        ("application", "37", "application", 37),
        ("application", "../../etc/passwd", "application", None),
        ("custom-private-type", "candidate secret", "unknown", None),
    ],
)
def test_context_identity_never_persists_arbitrary_strings(...):
    normalized = normalize_context_identity(context_type, context_ref, application_visible=lambda value: value == 37, key=KEY)
    assert normalized.context_type == expected_type
    assert normalized.entity_id == expected_entity
    assert "private" not in json.dumps(asdict(normalized), ensure_ascii=False)
    assert "candidate secret" not in json.dumps(asdict(normalized), ensure_ascii=False)
```

Add cases for valid/invalid UUID, database integer, controlled Tool Call ID, operation ID, transport UUID, free model ID, non-finite floats, functions, sets, cycles, oversized strings, unknown payload keys, and forbidden sensitive keys.

- [ ] **Step 2: Write red digest and bounded-manifest tests**

Assert the stable-envelope rule directly:

```python
def test_fact_digest_ignores_telemetry_but_not_segment_or_step():
    base = event_draft("model.completed", segment_id=SEGMENT_A, model_step=1, model_call_id=CALL_A)
    assert digest(base.with_telemetry(duration_ms=10)).fact_digest == digest(base.with_telemetry(duration_ms=90)).fact_digest
    assert digest(base.with_segment(SEGMENT_B)).fact_digest != digest(base).fact_digest
    assert digest(base.with_model_step(2)).fact_digest != digest(base).fact_digest
    assert digest(base.with_model_call_id(CALL_B)).fact_digest != digest(base).fact_digest
```

Build 10,000 message IDs, 100 tools, 100 attachments, and 100 domain sources. Assert manifest JSON remains below 16 KiB, includes counts and ordered digests, retains only the last 16 message IDs, 32 Provider-ordered tools, 16 attachments, and 32 domain sources, and has `manifest_schema_version == 1`.

- [ ] **Step 3: Run tests and verify red**

```powershell
uv run pytest tests/test_agent_run_journal.py -q
```

Expected: FAIL because `events.py` is absent.

- [ ] **Step 4: Implement strict pure contracts**

Create these types and functions:

```python
@dataclass(frozen=True)
class NormalizedContextIdentity:
    context_type: Literal["workspace", "global", "application", "mode", "unknown"]
    entity_id: int | str | None
    ref_fingerprint: str | None


@dataclass(frozen=True)
class ContextManifestInput:
    conversation_message_ids: tuple[int, ...]
    tool_names: tuple[str, ...]
    attachment_refs: tuple[Mapping[str, object], ...]
    domain_source_refs: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class PreparedSnapshot:
    manifest_json: str
    manifest_digest: str
    logical_input_fingerprint: str
    fingerprint_key_id: str


@dataclass(frozen=True)
class EventDraft:
    event_type: str
    schema_version: int
    execution_segment_id: str
    model_step: int | None
    model_call_id: str | None
    source_ref_type: str | None
    source_ref_id: str | None
    fingerprint_key_id: str | None
    payload_json: str
    payload_digest: str
    fact_digest: str
    dedupe_key: str
```

Implement `normalize_context_identity`, `normalize_source_reference`, `prepare_context_snapshot`, and `prepare_event`. Canonical JSON must sort object keys, preserve array order, reject non-JSON or non-finite values, use UTF-8 byte limits, and never call arbitrary `str()`/getters. HMAC formula is exactly:

```python
hmac.new(
    key.secret,
    b"offerpilot-agent-input-v1\0" + canonical_input_utf8,
    hashlib.sha256,
).hexdigest()
```

`fact_digest` must hash stable envelope plus `facts`; `payload_digest` must hash the whole `{facts, telemetry}` payload. `pending_identity_fingerprint` must use domain `offerpilot-agent-pending-v1\0`; `model_id_fingerprint` must use domain `offerpilot-agent-model-v1\0`; both carry `fingerprint_key_id`.

- [ ] **Step 5: Verify green and commit**

```powershell
uv run pytest tests/test_agent_run_journal.py -q
uv run ruff check src/offerpilot/agent_runtime/events.py tests/test_agent_run_journal.py
uv run mypy src/offerpilot/agent_runtime/events.py
```

Expected: all selected commands exit 0.

```powershell
git add src/offerpilot/agent_runtime/__init__.py src/offerpilot/agent_runtime/events.py tests/test_agent_run_journal.py
git commit -m "feat: AI add durable journal event contracts"
```

---

### Task 4: Atomic repository, ordering, and bounded lock behavior

**Files:**
- Create: `src/offerpilot/repositories/agent_runs.py`
- Create: `tests/test_agent_runs_repository.py`

- [ ] **Step 1: Write atomicity, idempotency, and concurrency tests**

Use two independently created Journal session factories against one SQLite file. Cover:

```python
def test_create_run_atomically_creates_initial_events(repository):
    created = repository.create_run_and_initial_segment(RUN_INPUT)
    assert created.run.last_seq == 2
    assert [(event.seq, event.event_type) for event in created.events] == [
        (1, "run.started"),
        (2, "segment.started"),
    ]


def test_capture_context_rolls_back_snapshot_when_event_insert_fails(repository):
    repository.fail_next_event_insert = True
    with pytest.raises(SyntheticJournalFailure):
        repository.capture_context(RUN_ID, SNAPSHOT_INPUT)
    assert repository.list_snapshots(RUN_ID) == []


def test_same_dedupe_and_same_facts_returns_existing_event(repository):
    first = repository.append_event(RUN_ID, DRAFT_WITH_DURATION_10)
    second = repository.append_event(RUN_ID, DRAFT_WITH_DURATION_90)
    assert second.id == first.id
    assert repository.count_events(RUN_ID, DEDUPE_KEY) == 1
```

Add tests for different stable envelope conflict, concurrent seq allocation without gaps/duplicates, CAS fallback limited to two attempts, strict status transitions, terminal immutability, waiting partial unique index, atomic suspended disposition, atomic terminal disposition, and `updated_at` changes after Event/Snapshot/state writes.

- [ ] **Step 2: Write controlled lock-budget tests**

Hold `BEGIN IMMEDIATE` on a second SQLite connection, invoke one repository write through the 50 ms Journal engine, and assert it raises an operational lock failure before 250 ms wall time. Occupy the one-connection Journal pool and assert checkout fails within the same bound. Do not use sleeps to decide correctness; synchronize acquisition with `threading.Event` barriers and use elapsed time only as an upper safety bound.

- [ ] **Step 3: Run repository tests and verify red**

```powershell
uv run pytest tests/test_agent_runs_repository.py -q
```

Expected: FAIL because `AgentRunRepository` is absent.

- [ ] **Step 4: Implement one-owner repository transactions**

Create these result and repository methods:

```python
@dataclass(frozen=True)
class StartedRun:
    run: AgentRun
    events: tuple[AgentEvent, AgentEvent]


@dataclass(frozen=True)
class CapturedContext:
    snapshot: AgentContextSnapshot
    event: AgentEvent


class JournalConflictError(RuntimeError):
    pass


class AgentRunRepository:
    def create_run_and_initial_segment(self, command: StartRunCommand) -> StartedRun: ...
    def attach_input_message(self, run_id: str, message_id: int) -> AgentRun: ...
    def start_segment(self, command: StartSegmentCommand) -> AgentEvent: ...
    def append_event(self, run_id: str, draft: EventDraft) -> AgentEvent: ...
    def capture_context(self, run_id: str, command: CaptureContextCommand) -> CapturedContext: ...
    def converge_disposition(self, run_id: str, command: DispositionCommand) -> tuple[AgentEvent, ...]: ...
    def find_waiting_run(self, conversation_id: int, tool_call_id: str) -> AgentRun | None: ...
```

Inside each transaction, query an existing dedupe/snapshot key before validating a new transition. Return the existing row when stable facts match; raise `JournalConflictError` when facts or Key Domain conflict. `attach_input_message` may set a currently null ID once, returns the existing Run for the same ID, and conflicts on a different non-null ID. Allocate seq using `UPDATE ... RETURNING`; when the injected capability probe reports unsupported, use an expected-`last_seq` CAS loop with at most two attempts. Never write a partial Run/Snapshot/disposition.

- [ ] **Step 5: Verify green and commit**

```powershell
uv run pytest tests/test_agent_run_migrations.py tests/test_agent_runs_repository.py -q
uv run ruff check src/offerpilot/repositories/agent_runs.py tests/test_agent_runs_repository.py
uv run mypy src/offerpilot/repositories/agent_runs.py
```

Expected: all selected commands exit 0.

```powershell
git add src/offerpilot/repositories/agent_runs.py tests/test_agent_runs_repository.py
git commit -m "feat: AI add atomic durable journal repository"
```

---

### Task 5: Safe recorder budgets and deterministic trace reconstruction

**Files:**
- Create: `src/offerpilot/agent_runtime/journal.py`
- Create: `src/offerpilot/agent_runtime/trace.py`
- Create: `tests/test_agent_run_trace.py`
- Modify: `tests/test_agent_run_journal.py`
- Modify: `src/offerpilot/agent_runtime/__init__.py`

- [ ] **Step 1: Write recorder fail-open and budget tests**

Inject a manual monotonic clock and deterministic repository failures. Assert:

```python
def test_segment_budget_includes_preprocessing_and_stops_nonterminal_writes():
    clock = ManualClock()
    recorder = make_recorder(clock=clock, prepare_event=lambda value, deadline: clock.advance(0.151))
    recorder.append_event(EVENT_INPUT)
    assert recorder.recording_status == "degraded"
    assert recorder.repository.append_calls == 0
    assert recorder.diagnostics == ["journal_budget_exhausted"]


def test_safe_recorder_does_not_swallow_base_exception():
    recorder = make_recorder(repository=RepositoryRaising(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        recorder.append_event(EVENT_INPUT)
```

Also prove: key unavailable returns `NullRunRecorder`; exception diagnostics never include `str(exc)`; degraded latch is irreversible; `mark_degraded` failure does not recurse; final 50 ms convergence attempts suspended and terminal dispositions once; `fingerprint_key_domain_changed` does not block business callbacks.

- [ ] **Step 2: Write trace tests**

Create persisted traces for normal completion, waiting confirmation, stale open, sequence gap, missing model completion, missing tool completion, missing segment finish, missing terminal event, known degraded, and semantic anomaly. Assert the three independent outputs:

```python
assert trace.lifecycle_status == "waiting_confirmation"
assert trace.completion_status == "suspended"
assert trace.integrity_status == "healthy"
assert trace.anomalies == ()
```

For stale calculation, place `started_at` far in the past but `updated_at` and latest Event inside the threshold; assert `open`, not `stale_open`.

- [ ] **Step 3: Run tests and verify red**

```powershell
uv run pytest tests/test_agent_run_journal.py tests/test_agent_run_trace.py -q
```

Expected: FAIL because safe recorder and trace modules are absent.

- [ ] **Step 4: Implement recorder interfaces**

Use these internal protocols:

```python
class RunRecorder(Protocol):
    run_id: str | None
    segment_id: str | None
    def start_segment(self, command: StartSegmentCommand) -> None: ...
    def capture_context(self, logical_input: object, manifest: ContextManifestInput, *, snapshot_kind: str) -> str | None: ...
    def append_event(self, event: EventInput) -> None: ...
    def suspend(self, command: SuspendedDisposition) -> None: ...
    def finish(self, command: TerminalDisposition) -> None: ...


class NullRunRecorder:
    run_id = None
    segment_id = None


class SafeRunRecorder:
    def __init__(
        self,
        repository: AgentRunRepository,
        key: JournalKeyDomain,
        run_id: str,
        segment_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        segment_budget_seconds: float = 0.150,
    ) -> None: ...


class RunRecorderFactory:
    def start_run(self, command: StartRunCommand) -> RunRecorder: ...
    def resume_waiting_run(
        self,
        conversation_id: int,
        waiting_tool_call_id: str,
        command: StartSegmentCommand,
    ) -> RunRecorder: ...
```

`RunRecorderFactory` must:

```text
env OFFERPILOT_AGENT_JOURNAL_ENABLED=false -> NullRunRecorder
key unavailable -> NullRunRecorder + fixed journal_secret_unavailable diagnostic
new logical request -> create Run and initial Segment atomically
confirmation -> find by conversation_id + waiting_tool_call_id, then start a new Segment
missing old Run or changed key domain -> NullRunRecorder + fixed diagnostic
```

The 150 ms deadline starts before schema/canonical/manifest/HMAC work. Preprocessors receive the same deadline and check it while traversing bounded collections and while chunking large strings. Repository calls receive the remaining budget; no retry may extend it. `except Exception` is allowed; `except BaseException` is forbidden.

- [ ] **Step 5: Implement deterministic trace reconstruction**

Create dataclasses for Run, Segment, Model Step, Tool, Approval, and anomaly views. Implement:

```python
def reconstruct_agent_run(
    repository: AgentRunRepository,
    run_id: str,
    *,
    as_of: datetime,
    stale_after: timedelta | None,
) -> AgentRunTrace:
    """Read Journal rows only; never mutate or drive recovery."""
```

Use the exact lifecycle/completion/integrity priority from the design. Never infer missing Message IDs from text or timestamps.

- [ ] **Step 6: Verify green and commit**

```powershell
uv run pytest tests/test_agent_run_journal.py tests/test_agent_run_trace.py -q
uv run ruff check src/offerpilot/agent_runtime tests/test_agent_run_journal.py tests/test_agent_run_trace.py
uv run mypy src/offerpilot/agent_runtime
```

Expected: all selected commands exit 0.

```powershell
git add src/offerpilot/agent_runtime/__init__.py src/offerpilot/agent_runtime/journal.py src/offerpilot/agent_runtime/trace.py tests/test_agent_run_journal.py tests/test_agent_run_trace.py
git commit -m "feat: AI add safe journal recorder and trace"
```

---

### Task 6: Instrument model and tool lifecycle in the Agent runner

**Files:**
- Modify: `src/offerpilot/ai/agent.py:135-205,282-450,466-610,614-670`
- Modify: `tests/test_ai_agent.py`

- [ ] **Step 1: Write Agent lifecycle tests before production edits**

Add a `RecordingRunRecorder` fake and assert exact event order for:

```text
one model answer:
  context.captured -> model.requested -> model.completed

read tool loop:
  model.requested -> model.completed -> tool.proposed -> tool.started -> tool.completed
  -> context.captured -> model.requested -> model.completed

write tool before confirmation:
  model.requested -> model.completed -> tool.proposed
  and no tool.started/tool.completed

provider exception:
  model.requested -> model.failed, then original exception propagates

tool handler returns legacy error string:
  tool.started -> tool.failed, while existing Agent behavior still supplies the same Tool Result to the next model step
```

Assert `model_call_id` is one stable UUID across requested/completed or requested/failed, and model steps increment from 1.

- [ ] **Step 2: Run selected tests and verify red**

```powershell
uv run pytest tests/test_ai_agent.py -q
```

Expected: new tests fail because `run_turn` and `resume_after_confirm` do not accept `run_recorder`.

- [ ] **Step 3: Add an optional recorder without altering existing sinks**

Extend `LangGraphAgentRunner`, `run_turn`, and `resume_after_confirm` with:

```python
run_recorder: RunRecorder | None = None
```

Default to `NullRunRecorder`. Preserve `event_sink` exactly for existing SSE events. In `_complete_model`, capture a `model_input` Snapshot and append `model.requested` before calling the Provider; append completed/failed afterward. In `_handle_tool`, record proposal, actual start, and completed/failed based on the existing legacy string result without persisting the string. Do not move `_execute_tool`, confirmation checks, interrupts, checkpoint logic, or cancellation checks.

- [ ] **Step 4: Verify Agent behavior and commit**

```powershell
uv run pytest tests/test_ai_agent.py -q
uv run ruff check src/offerpilot/ai/agent.py tests/test_ai_agent.py
uv run mypy src/offerpilot/ai/agent.py
```

Expected: all Agent tests pass; existing event-sink assertions remain unchanged.

```powershell
git add src/offerpilot/ai/agent.py tests/test_ai_agent.py
git commit -m "feat: AI instrument agent model and tool runs"
```

---

### Task 7: Integrate initial sync, stream, deterministic, and replay routes

**Files:**
- Modify: `src/offerpilot/api.py:1058-1395,4469-4875,9846-10035`
- Modify: `tests/test_chat_api.py`

- [ ] **Step 1: Write route-level causal and behavior-equivalence tests**

Create tests for:

```text
invalid body / missing Conversation -> no AgentRun
ordinary sync response -> one Run, one Segment, model Snapshot, assistant.persisted, completed
ordinary stream response -> same Journal semantics; SSE event names/ids/seq unchanged
deterministic Pilot action -> route.selected(deterministic), zero model events, suspended confirmation
existing Pending Action replay -> original Run gets pending_replay Segment; no orphan Run
arbitrary context_type/context_ref -> original strings absent from all Journal JSON and identity columns
Journal disabled -> identical HTTP/SSE/messages/business rows and Provider/tool counts
Journal create failure -> identical HTTP/SSE/messages/business rows and Provider/tool counts
```

Capture the `ChatMessage` returned by the existing `append_message` call for `input_message_id`. For deterministic helpers that do not return a Message ID, assert it remains null; do not query by text or time.

- [ ] **Step 2: Run chat tests and verify red**

```powershell
uv run pytest tests/test_chat_api.py -q
```

Expected: the new Journal assertions fail while existing API assertions still pass.

- [ ] **Step 3: Construct the recorder factory once in `create_app`**

Add an internal optional dependency for deterministic testing:

```python
def create_app(
    data_dir: Path | None = None,
    chat_model: ChatModel | None = None,
    *,
    run_recorder_factory: RunRecorderFactory | None = None,
) -> FastAPI:
```

When not injected, build it from the dedicated key domain and low-wait Journal session factory. Failure produces a Null factory and a fixed safe diagnostic; it never prevents app creation.

- [ ] **Step 4: Instrument initial routes after existing ingress validation**

For model routes:

```text
1. validate HTTP body and Conversation
2. append the existing user message and retain its returned ID
3. start logical Run + initial Segment
4. record route.selected(model) and initial context
5. pass recorder into run_turn
6. after existing message/pending persistence succeeds, record assistant IDs or suspended disposition
7. after the existing response is finalized, record completed/failed/cancelled/timed_out disposition
```

For deterministic routes, split the existing nested helper only at a read-only classification seam; do not move its business writes. Classify before execution. If it is a Pending Action replay, find the original Run and append a `pending_replay` Segment without creating a new Run. Otherwise start the deterministic Run before executing the helper, and pass a private `on_user_message_persisted(ChatMessage)` callback so a directly returned input Message ID can be attached once; if the helper path does not append a Message, leave the ID null. Provider call count must remain zero.

For streaming routes, keep `SseRun.run_id` and `SseRun.seq` unchanged. Journal uses separate UUIDs and seq. Cancellation still raises/handles `ChatRunCancelled` exactly as before.

- [ ] **Step 5: Make persisted assistant IDs explicit without changing repository contracts**

Change only the internal API helper `_persist_ai_messages` to return the IDs directly returned by `chat.append_message`; callers may ignore the list. Record `assistant.persisted` only from this returned list. Do not alter `ChatRepository.append_message`, `persist_pending_action`, or `resolve_pending_confirmation` return values.

- [ ] **Step 6: Verify routes and commit**

```powershell
uv run pytest tests/test_chat_api.py tests/test_ai_agent.py -q
uv run ruff check src/offerpilot/api.py tests/test_chat_api.py
uv run mypy src/offerpilot/api.py
```

Expected: all selected tests pass, including byte-for-byte SSE envelope assertions.

```powershell
git add src/offerpilot/api.py tests/test_chat_api.py
git commit -m "feat: AI journal chat execution lifecycle"
```

---

### Task 8: Confirmation resume, approval ordering, and disposition convergence

**Files:**
- Modify: `src/offerpilot/api.py:4877-5450`
- Modify: `tests/test_chat_api.py`
- Modify: `tests/test_agent_run_journal.py`

- [ ] **Step 1: Write confirmation ordering tests**

Cover ordinary and streaming confirmation, approved, edited, rejected, stale token, missing Pending Action, repeated display, second pending write, and missing Journal Run. Assert:

```text
invalid token/identity -> no confirmation Segment and no approval.decided
valid claim -> new Segment on original agent_run_id
approval.decided occurs before tool.started
rejected -> approval.decided but no tool.started
new pending write -> same Run returns to waiting_confirmation with a new tool_call_id
missing Journal Run -> existing confirmation result unchanged + journal_run_missing diagnostic
suspended convergence -> tool.proposed, approval.requested, waiting field, run.waiting_confirmation, segment.finished(suspended) atomically present
```

Use one test where the initial recorder exhausts its 150 ms budget before approval persistence; allow the 50 ms convergence call and assert confirmation later resolves the same logical Run.

- [ ] **Step 2: Run selected confirmation tests and verify red**

```powershell
uv run pytest tests/test_chat_api.py -k "confirm or pending" -q
uv run pytest tests/test_agent_run_journal.py -k "disposition or suspended" -q
```

Expected: new ordering and convergence assertions fail.

- [ ] **Step 3: Resume only after existing business identity checks**

In `/api/chat/confirm` and `/api/chat/confirm/stream`:

```text
1. validate payload, Conversation, Pending Action, token, and edited args with existing code
2. find original Journal Run by conversation_id + waiting_tool_call_id
3. start confirmation Segment only when the business identity is valid
4. compose the existing confirmation_attempt_sink with recorder.approval_decided
5. pass the same recorder to resume_after_confirm
6. record resumed/tool/model/message facts only after their current business callbacks succeed
7. converge to suspended or terminal after the existing persistence result is known
```

Do not create an orphan Run when lookup fails. Do not move confirmation token comparison, Pending Action CAS, fallback claim, timeout handling, or `_persist_confirmation_continuation`.

- [ ] **Step 4: Verify confirmation behavior and commit**

```powershell
uv run pytest tests/test_chat_api.py tests/test_agent_run_journal.py tests/test_ai_agent.py -q
uv run ruff check src/offerpilot/api.py src/offerpilot/ai/agent.py tests/test_chat_api.py tests/test_agent_run_journal.py
uv run mypy src
```

Expected: all selected tests and Mypy pass.

```powershell
git add src/offerpilot/api.py tests/test_chat_api.py tests/test_agent_run_journal.py
git commit -m "feat: AI journal confirmation resume lifecycle"
```

---

### Task 9: End-to-end privacy, failure injection, and internal acceptance

**Files:**
- Modify: `tests/test_smoke.py`
- Modify: `tests/test_chat_api.py`
- Modify: `tests/test_agent_run_trace.py`
- Modify: `tests/test_settings_api.py`

- [ ] **Step 1: Add a controlled complete causal-chain test**

Use a local deterministic `ChatModel` that returns: read tool, write tool requiring confirmation, then final answer. Execute sync and streaming variants and assert:

```text
one logical Run across initial + confirmation Segments
one Snapshot per real model call
Provider call count unchanged by Journal
tool/approval/run order matches the design
Trace lifecycle=completed, completion=terminal, integrity=healthy
SSE IDs remain SseRun IDs and Journal seq is independent
```

- [ ] **Step 2: Add a complete secret canary scan**

Seed canaries in user message, arbitrary context ref, attachment label, JD/resume context, tool args, model output, confirmation token, idempotency key, Provider URL, and exception text. Query every text column in all three Journal tables, inspect app logs, `/api/settings`, `/api/settings/backup`, and exported ZIP entries. Assert no canary appears. Assert expected HMAC fields exist with the correct `fingerprint_key_id`.

- [ ] **Step 3: Add fail-open equivalence tests**

Run the same fixture with Journal enabled, disabled, key persistence failing, repository create failing, append failing, Snapshot failing, and disposition failing. Compare:

```python
assert actual.http_status == control.http_status
assert actual.response_json == control.response_json
assert actual.sse_events == control.sse_events
assert actual.chat_rows == control.chat_rows
assert actual.business_rows == control.business_rows
assert actual.provider_calls == control.provider_calls
assert actual.tool_calls == control.tool_calls
```

Only Journal rows and fixed safe diagnostics may differ.

- [ ] **Step 4: Run the focused acceptance matrix**

```powershell
uv run pytest tests/test_agent_run_migrations.py tests/test_agent_run_keyring.py tests/test_agent_runs_repository.py tests/test_agent_run_journal.py tests/test_agent_run_trace.py tests/test_ai_agent.py tests/test_chat_api.py tests/test_settings_api.py tests/test_smoke.py -q
uv run ruff check .
uv run mypy src
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit focused acceptance tests**

```powershell
git add tests/test_smoke.py tests/test_chat_api.py tests/test_agent_run_trace.py tests/test_settings_api.py
git commit -m "test: AI verify durable journal equivalence"
```

---

### Task 10: Independent review, complete gates, and release report

**Files:**
- Create: `docs/reports/2026-08-17-durable-execution-journal-release-verification.md`
- Read: all allowlisted product and test files

- [ ] **Step 1: Re-run the scope assertion before review**

Run the reusable Task 0 scope assertion. Expected: zero outside-allowlist paths. Reload the fixed baseline in the same PowerShell process, then run:

```powershell
$locator = Get-Content -Raw (Join-Path $env:TEMP 'offerpilot-durable-journal-gate.locator.json') | ConvertFrom-Json
$baseline = (Get-Content -Raw -LiteralPath $locator.baseline_path).Trim()
git diff --check "$baseline..HEAD"
```

Expected: exit code 0.

- [ ] **Step 2: Request independent code review before the report commit**

Use the repository `requesting-code-review` workflow and give the reviewer:

```text
baseline SHA and exact allowlist
design and plan paths
all changed files
focused test outputs
special review targets: arbitrary context leakage, key export, event digest envelope,
atomic Run/Snapshot/disposition writes, 150/50 ms budgets, BaseException propagation,
confirmation ordering, SSE identity isolation, and business behavior equivalence
```

Resolve every P0/P1. Fix P2 unless the user explicitly accepts a documented residual risk. After fixes, rerun the focused acceptance matrix and scope assertion.

- [ ] **Step 3: Generate a fresh backend manifest without hiding duplicates**

```powershell
$locator = Get-Content -Raw (Join-Path $env:TEMP 'offerpilot-durable-journal-gate.locator.json') | ConvertFrom-Json
$backend = Join-Path (Split-Path $locator.baseline_path -Parent) 'backend-results'
New-Item -ItemType Directory -Force -Path $backend | Out-Null
$raw = @(& uv run pytest --collect-only -q --disable-warnings tests 2>&1)
if ($LASTEXITCODE -ne 0) { throw 'backend collection failed' }
$nodes = @($raw | ForEach-Object { $line=[string]$_; if($line.Trim() -match '^(tests[\\/].+::.+)$'){ $Matches[1].Replace('/','\') } } | Where-Object { $_ })
$duplicates = @($nodes | Group-Object | Where-Object Count -gt 1)
if ($duplicates.Count -gt 0) { throw "duplicate backend node ids: $($duplicates.Name -join ', ')" }
$nodes | Set-Content -LiteralPath (Join-Path $backend 'full-manifest.txt') -Encoding utf8
```

Expected: non-empty manifest, no duplicate node ID.

- [ ] **Step 4: Run all five backend groups and aggregate**

```powershell
foreach($group in @('agent','domain','knowledge','proposals','misc')) {
  & powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-pytest-groups.ps1 -Group $group -ResultDir $backend
  if($LASTEXITCODE -ne 0){ throw "$group failed" }
}
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-pytest-groups.ps1 -Aggregate -ResultDir $backend
if($LASTEXITCODE -ne 0){ throw 'backend aggregate failed' }
```

Expected: five completion markers, only the script's four fixed Windows symlink skips, aggregate coverage equal to the manifest.

- [ ] **Step 5: Run all ten frontend groups and aggregate**

```powershell
$frontend = Join-Path (Split-Path $locator.baseline_path -Parent) 'frontend-results'
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Collect -RepositoryRoot $locator.repository_root -ResultDir $frontend
if($LASTEXITCODE -ne 0){ throw 'frontend collection failed' }
foreach($group in @('components-core','components-chat','components-interview','components-offer','components-support','features','layout','lib','services','theme')) {
  & powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Group $group -RepositoryRoot $locator.repository_root -ResultDir $frontend
  if($LASTEXITCODE -ne 0){ throw "$group failed" }
}
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Aggregate -RepositoryRoot $locator.repository_root -ResultDir $frontend
if($LASTEXITCODE -ne 0){ throw 'frontend aggregate failed' }
```

Expected: ten completion markers, no skipped or pending test records, aggregate source fingerprint and coverage valid.

- [ ] **Step 6: Run static, build, local, and bounded real-AI gates**

```powershell
uv run ruff check .
uv run mypy src
Push-Location web
try { npm.cmd run build } finally { Pop-Location }
uv run oc smoke --static-dir web/dist
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-smoke.ps1 -Port 18765
uv run oc verify --profile local --static-dir web/dist
```

Expected: every command exits 0. After all local gates pass, execute at most one existing bounded real-AI verification:

```powershell
uv run oc verify --profile real-ai --static-dir web/dist
```

Record the actual result; do not retry an external Provider failure without a new user decision. No product screenshot is required because this phase adds no UI.

- [ ] **Step 7: Write and commit the release report**

The report must include: baseline, allowlist, commits, schema/migration result, backend manifest and aggregate counts, frontend manifest/source hash/counts, focused privacy and equivalence results, static/build/local/real-AI outcomes, CR findings and resolutions, skipped tests, external failures, and remaining risks. It must not include key IDs, fingerprints, model/user/tool text, local secret paths, tokens, or exception strings.

```powershell
git add -f docs/reports/2026-08-17-durable-execution-journal-release-verification.md
git commit -m "docs: AI record durable journal verification"
```

- [ ] **Step 8: Revalidate after the report commit and clean temporary evidence**

Reload the locator; rerun the Task 0 scope assertion, `git diff --check $baseline..HEAD`, and `git status --short --branch`. Confirm the worktree is clean and the report is tracked. Only after all checks pass, remove the gate directory and locator with native PowerShell `Remove-Item -LiteralPath ... -Recurse -Force`, then verify both paths no longer exist. If any final check fails, retain the gate evidence for repair and do not claim completion.

---

## Spec coverage checklist

- [ ] Three tables only; no public API, CLI, UI, or SSE schema change.
- [ ] Run, Segment, transport, Model Step, Model Call, Tool Call, approval attempt, Message, and Operation identities remain distinct.
- [ ] Arbitrary Conversation context and other free strings never enter the Journal verbatim.
- [ ] Manifest schema v1 is bounded and versioned independently from canonicalizer/token estimator versions.
- [ ] Event fact digest includes the complete stable envelope; telemetry differences remain idempotent.
- [ ] Journal key creation and persistence are fail-open; no ephemeral write key is used.
- [ ] Settings responses exclude the key file, Secret, and current Key Domain ID; full backup excludes the key file and Secret but retains historical non-secret `fingerprint_key_id` values in `data.db`.
- [ ] Run creation, Context capture, and suspended/terminal dispositions are atomic Journal transactions.
- [ ] Total 150 ms and final 50 ms budgets are deterministic and include preprocessing.
- [ ] Recorder catches `Exception` only and preserves cancellation/process signals.
- [ ] Confirmation resumes the original logical Run without changing existing business claim/CAS semantics.
- [ ] Trace reports lifecycle, completion, and integrity separately and never drives recovery.
- [ ] Journal enabled, disabled, and failed paths are behaviorally equivalent outside Journal data and safe diagnostics.
