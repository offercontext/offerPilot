# Tool Execution Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 25 model-visible legacy registry tools with one typed Tool Execution Pipeline while preserving every Provider, HTTP/SSE, HITL, Pending Action, Journal, call-count, and business-side-effect contract, and isolate the three deterministic tools behind an explicit Legacy Adapter.

**Architecture:** Build the new runtime off-path first: immutable Provider contracts, compiled JSON Schema validation, typed Specs, capability/binding audit, two-stage execution, typed outcomes, and pure compatibility/transport/Journal projectors. After all 25 Specs pass differential golden tests, perform one destructive production cutover across Agent, Provider adapter, sync/stream API, and confirmation recovery; remove the legacy model-visible registry in the same working set. The three deterministic tools remain in a separately typed Legacy Catalog and are never available to the model dispatcher.

**Tech Stack:** Python 3.10+, FastAPI, LangGraph, SQLAlchemy/SQLite, Pydantic only for existing domain serializers, `jsonschema==4.26.0` with Draft 2020-12, `referencing.Registry`, pytest, Ruff, Mypy, Vitest, PowerShell release gates.

---

## Fixed inputs and execution rules

- Worktree: `D:\Users\yuqi.chen\offerpilot\.worktrees\feat-20260818-tool-execution-pipeline`
- Branch: `feat/20260818-tool-execution-pipeline`
- Fixed comparison baseline: `30c944f3bda1d99b303f8e9875a170a552f79af7`
- Approved design: `docs/superpowers/specs/2026-08-18-tool-execution-pipeline-design.md`
- Do not push or merge.
- Do not touch the dirty root worktree.
- Use `apply_patch` for repository edits.
- Run long commands with a 30-second initial yield and poll the same process; do not restart a command merely because it is still running.
- Use TDD: add a failing assertion, verify the failure, implement the smallest coherent change, rerun, then commit.
- Use commit subjects in the form `<type>: AI <English subject>`.
- Do not switch any production request to the new Pipeline until all 25 Specs, the Legacy Catalog, and golden assets are ready.
- The final cutover is destructive: no feature flag, shadow path, implicit fallback, or dual model-visible registry may remain.

## File responsibility map

### New runtime files

- `src/offerpilot/ai/tool_runtime/__init__.py`: narrow public exports used by Agent/API.
- `src/offerpilot/ai/tool_runtime/contracts.py`: Provider contracts, Spec generics, outcomes, prepared calls, execution records, confirmation authorization.
- `src/offerpilot/ai/tool_runtime/validation.py`: duplicate-safe JSON parsing, canonical JSON, Schema reference scan, Draft 2020-12 compilation and validation, lossless typed copy.
- `src/offerpilot/ai/tool_runtime/context.py`: capabilities, runtime dependencies, current bindings, binding aggregation.
- `src/offerpilot/ai/tool_runtime/catalog.py`: generic closed Catalog validation and lookup; it must not import `tool_specs`.
- `src/offerpilot/ai/tool_runtime/pipeline.py`: `prepare_call()`, `execute_prepared()`, exception mapping, exact executor-count boundary.
- `src/offerpilot/ai/tool_runtime/rendering.py`: total pure compatibility renderer and rejection text.
- `src/offerpilot/ai/tool_runtime/transport.py`: pure existing HTTP/SSE tool-call and tool-result payload projection.
- `src/offerpilot/ai/tool_runtime/journal.py`: first-phase-only EventInput projection and execution-stage gating.
- `src/offerpilot/ai/tool_runtime/legacy.py`: generic Legacy Adapter/Catalog types only.

### New Spec files

- `src/offerpilot/ai/tool_specs/__init__.py`: composition-root exports.
- `src/offerpilot/ai/tool_specs/common.py`: shared typed domain failures and pure result serializers.
- `src/offerpilot/ai/tool_specs/applications.py`: four application contracts/Args/executors.
- `src/offerpilot/ai/tool_specs/application_events.py`: five application-event contracts/Args/executors.
- `src/offerpilot/ai/tool_specs/notes.py`: four note contracts/Args/executors.
- `src/offerpilot/ai/tool_specs/offers.py`: five offer contracts/Args/executors.
- `src/offerpilot/ai/tool_specs/resumes.py`: five resume contracts/Args/executors.
- `src/offerpilot/ai/tool_specs/jd_analyses.py`: two JD-analysis contracts/Args/executors.
- `src/offerpilot/ai/tool_specs/legacy.py`: exactly three deterministic Legacy Adapters.
- `src/offerpilot/ai/tool_specs/catalog.py`: composition root that assembles the ordered 25/3 sets and builds request contexts.

### Existing production files

- `src/offerpilot/ai/agent.py`: destructively replace registry/string execution with Catalog/Pipeline; retain LangGraph, checkpoint, selection, confirmation, and cancellation semantics.
- `src/offerpilot/ai/client.py`: accept `ProviderToolContract`, send its complete payload unchanged, remove dict-registry conversion.
- `src/offerpilot/ai/types.py`: keep persisted `Message` wire shape; add no typed outcome fields.
- `src/offerpilot/api.py`: construct runtime Context, route trusted deterministic Pending Actions, consume `ToolExecutionRecord`, and stop parsing compatibility strings.
- `src/offerpilot/repositories/chat.py`: add the narrow durable pre-executor Pending Action claim CAS used by typed confirmation recovery; preserve the public Pending Action representation.
- `src/offerpilot/models.py` and `src/offerpilot/db.py`: add the private `0025_pending_confirmation_claim` identity and 15-minute lease columns/migration; never encode claim state into Provider-controlled fields.
- `src/offerpilot/ai/tools.py`: delete after all production call sites move; no model-visible code remains here.
- `pyproject.toml`, `uv.lock`: direct exact `jsonschema==4.26.0` dependency.

### Test and evidence files

- `tests/tool_pipeline/conftest.py`: deterministic repositories, current bindings, synthetic records, recorder and executor spies.
- `tests/tool_pipeline/golden.py`: read-only golden loader and canonical comparison; no update API.
- `tests/tool_pipeline/test_golden_assets.py`: asset privacy/canonical/baseline checks.
- `tests/tool_pipeline/test_validation.py`: JSON/Schema/reference tests.
- `tests/tool_pipeline/test_catalog.py`: Provider manifest, exact 25/3 classification, dependency direction.
- `tests/tool_pipeline/test_context.py`: capability short-circuit and binding aggregation.
- `tests/tool_pipeline/test_pipeline.py`: stage ordering, executor counts, authorization, exception boundaries.
- `tests/tool_pipeline/test_applications.py`, `test_application_events.py`, `test_notes.py`, `test_offers.py`, `test_resumes.py`, `test_jd_analyses.py`: per-Spec success/failure/output/side-effect tests.
- `tests/tool_pipeline/test_legacy.py`: deterministic isolation and trusted routing.
- `tests/tool_pipeline/test_transport.py`: pure SSE/HTTP projection equivalence.
- `tests/tool_pipeline/test_journal.py`: first-phase event sequence and Trace integrity.
- `tests/tool_pipeline/test_checkpoint.py`: negative checkpoint serialization inspection.
- `tests/tool_pipeline/test_source_gates.py`: AST/source mechanical proof that old paths are gone.
- `tests/fixtures/tool_pipeline/provider_manifest_30c944f.json`: full final Provider payload.
- `tests/fixtures/tool_pipeline/tool_outcomes_30c944f.json`: synthetic visible strings and normalized business projections.
- `tests/fixtures/tool_pipeline/journal_sequences_30c944f.json`: canonical first-phase event sequences.
- Delete superseded `tests/test_ai_tools.py` registry assertions; modify `tests/test_ai_agent.py`, `tests/test_chat_api.py`, `tests/test_chat_repository.py`, `tests/test_litellm_client.py`, and `tests/test_knowledge_sources_api.py` to use the new public interfaces and durable claim boundary.
- Create `docs/reports/2026-08-18-tool-execution-pipeline-release-verification.md` only after all gates pass.

---

### Task 0: Establish the immutable scope gate

**Files:**
- External temp evidence: `%TEMP%\offerpilot-tool-pipeline-gate\baseline.txt`
- External temp evidence: `%TEMP%\offerpilot-tool-pipeline-gate\allowlist.txt`
- External temp evidence: `%TEMP%\offerpilot-tool-pipeline-gate.locator.json`

- [ ] **Step 1: Verify branch, HEAD, and clean worktree**

Run:

```powershell
git status --short --branch
git log -4 --oneline
git merge-base --is-ancestor 30c944f3bda1d99b303f8e9875a170a552f79af7 HEAD
```

Expected: branch is `feat/20260818-tool-execution-pipeline`, worktree is clean, and the ancestor command exits 0.

- [ ] **Step 2: Create the exact allowlist evidence**

Use native PowerShell to create the temp gate. The allowlist is exact; directory prefixes end with `/` and admit only descendants of that directory.

```powershell
$gate = Join-Path $env:TEMP 'offerpilot-tool-pipeline-gate'
New-Item -ItemType Directory -Force -Path $gate | Out-Null
'30c944f3bda1d99b303f8e9875a170a552f79af7' | Set-Content -LiteralPath (Join-Path $gate 'baseline.txt') -Encoding utf8
@(
  'pyproject.toml'
  'uv.lock'
  'src/offerpilot/ai/agent.py'
  'src/offerpilot/ai/client.py'
  'src/offerpilot/ai/types.py'
  'src/offerpilot/ai/tools.py'
  'src/offerpilot/ai/tool_runtime/'
  'src/offerpilot/ai/tool_specs/'
  'src/offerpilot/api.py'
  'src/offerpilot/db.py'
  'src/offerpilot/models.py'
  'src/offerpilot/repositories/chat.py'
  'src/offerpilot/smoke.py'
  'tests/tool_pipeline/'
  'tests/fixtures/tool_pipeline/'
  'tests/test_ai_tools.py'
  'tests/test_ai_agent.py'
  'tests/test_ai_client.py'
  'tests/test_chat_api.py'
  'tests/test_chat_repository.py'
  'tests/test_knowledge_sources_api.py'
  'tests/test_litellm_client.py'
  'docs/superpowers/specs/2026-08-18-tool-execution-pipeline-design.md'
  'docs/superpowers/plans/2026-08-18-tool-execution-pipeline.md'
  'docs/reports/2026-08-18-tool-execution-pipeline-release-verification.md'
) | Set-Content -LiteralPath (Join-Path $gate 'allowlist.txt') -Encoding utf8
@{
  repository_root = (Get-Location).Path
  baseline_path = (Join-Path $gate 'baseline.txt')
  allowlist_path = (Join-Path $gate 'allowlist.txt')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $env:TEMP 'offerpilot-tool-pipeline-gate.locator.json') -Encoding utf8
```

Expected: three files exist and point only at this worktree.

- [ ] **Step 3: Add a reusable scope assertion to the work log**

Use this command after every commit and before every completion claim:

```powershell
$locator = Get-Content -Raw (Join-Path $env:TEMP 'offerpilot-tool-pipeline-gate.locator.json') | ConvertFrom-Json
$baseline = (Get-Content -Raw $locator.baseline_path).Trim()
$allowed = @(Get-Content $locator.allowlist_path | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$changed = @(& git diff --name-only "$baseline..HEAD")
$changed += @(& git ls-files --others --exclude-standard)
$violations = @($changed | Sort-Object -Unique | Where-Object {
  $path = $_.Replace('\','/')
  -not ($allowed | Where-Object {
    $rule = $_.Replace('\','/')
    if($rule.EndsWith('/')) { $path.StartsWith($rule) } else { $path -eq $rule }
  })
})
if($violations){ throw "scope violations: $($violations -join ', ')" }
```

Expected: no violation.

---

### Task 1: Freeze independent baseline goldens before touching runtime behavior

**Files:**
- Create: `tests/tool_pipeline/conftest.py`
- Create: `tests/tool_pipeline/golden.py`
- Create: `tests/tool_pipeline/test_golden_assets.py`
- Create: `tests/fixtures/tool_pipeline/provider_manifest_30c944f.json`
- Create: `tests/fixtures/tool_pipeline/tool_outcomes_30c944f.json`
- Create: `tests/fixtures/tool_pipeline/journal_sequences_30c944f.json`
- Read while capturing: `src/offerpilot/ai/tools.py`, `src/offerpilot/ai/agent.py`, `src/offerpilot/ai/client.py`

- [ ] **Step 1: Write the read-only golden loader and failing asset tests**

Create a loader with no write function:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parents[1] / "fixtures" / "tool_pipeline"
BASELINE = "30c944f3bda1d99b303f8e9875a170a552f79af7"


def load_golden(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
```

In `test_golden_assets.py`, assert each asset contains `baseline == BASELINE`, round-trips to its canonical form, contains no absolute path, SQLite bytes, key/token/secret fields, real-user canaries, timestamps, or exception traceback, and has no writer/update helper in `golden.py`.

- [ ] **Step 2: Run the tests to verify missing assets fail**

Run:

```powershell
uv run pytest tests/tool_pipeline/test_golden_assets.py -q
```

Expected: FAIL because the three JSON files do not exist.

- [ ] **Step 3: Capture the final Provider payload from the baseline adapter**

Use a one-off temp script, not a committed updater. It must instantiate all repositories with synthetic data, build `offerpilot_tool_registry`, filter `model_visible`, pass each entry through the current `_openai_tool`, and emit:

```python
{
    "baseline": "30c944f3bda1d99b303f8e9875a170a552f79af7",
    "tools": provider_payloads,
    "schema_fingerprints": {
        payload["function"]["name"]: "sha256:" + sha256(
            canonical_json(payload["function"]["parameters"]).encode("utf-8")
        ).hexdigest()
        for payload in provider_payloads
    },
}
```

The emitted names must be the approved ordered 25 and must not contain the three deterministic names. Add the canonical output to `provider_manifest_30c944f.json` with `apply_patch`, then delete the temp script.

- [ ] **Step 4: Capture all 25 success strings and declared baseline failures**

Seed a fresh temporary database per scenario so IDs are stable. Record only:

```python
{
    "tool_name": name,
    "case": case,
    "arguments": canonical_arguments,
    "visible_result": visible_result,
    "business_projection": normalized_repository_projection,
    "handler_calls": handler_calls,
}
```

The exact success arguments, against a seed where every referenced record has id `1`, are:

```python
SUCCESS_ARGUMENTS = {
    "list_applications": {},
    "get_application": {"id": 1},
    "create_application": {"company_name": "Beta", "position_name": "Backend Engineer"},
    "update_application_status": {"id": 1, "status": "interviewing"},
    "list_application_events": {"application_id": 1},
    "get_application_event": {"id": 1},
    "create_application_event": {
        "application_id": 1, "event_type": "interview",
        "scheduled_at": "2026-08-20T09:00:00Z", "duration_minutes": 60,
    },
    "update_application_event": {
        "id": 1, "application_id": 1, "event_type": "interview",
        "scheduled_at": "2026-08-21T09:00:00Z", "duration_minutes": 45,
    },
    "delete_application_event": {"id": 1},
    "list_notes": {"application_id": 1},
    "add_note": {"application_id": 1, "date": "2026-08-18", "questions": "Q1"},
    "update_note": {"id": 1, "mood": "calm"},
    "delete_note": {"id": 1},
    "list_offers": {},
    "get_offer": {"id": 1},
    "compare_offers": {"ids": [1]},
    "update_offer": {"id": 1, "base_monthly": 30000},
    "save_offer_assessment": {"id": 1, "assessment": "balanced"},
    "list_resumes": {},
    "get_resume": {"id": 1},
    "resume_update_career_intent": {"id": 1, "career_intent": {"target_role": "Engineer"}},
    "resume_rewrite_highlight": {
        "id": 1, "section": "experiences", "item_index": 0,
        "highlight_index": 0, "text": "Improved latency by 20%",
    },
    "list_resume_matches": {"resume_id": 1},
    "list_jd_analyses": {"application_id": 1},
    "get_jd_analysis": {"id": 1},
}
```

The failure matrix is also closed: `missing_required_id` for every ID-bearing handler, `application_not_found` for application/event/note ownership paths, `event_not_found`, `note_not_found`, `offer_not_found`, `resume_not_found`, `jd_analysis_not_found`, `invalid_application_status`, `closed_application_without_reason`, `duplicate_company_requires_confirmation`, `unclear_note_date`, `empty_offer_comparison`, `deleted_resume`, `negative_highlight_index`, and `out_of_range_highlight_index`. Each case names its expected tool in the asset, so the test fails if a case is silently reassigned. Execute through the current `_execute_tool` boundary so exception strings become the actual visible error output. Use fixed entity values and fixed datetimes, canonicalize the projection, add it to `tool_outcomes_30c944f.json`, and remove the one-off capture script.

- [ ] **Step 5: Capture first-phase Journal sequences**

Capture canonical event `event_type` and frozen `facts` for these synthetic cases:

```python
JOURNAL_CASES = (
    "read_success",
    "executor_exception",
    "write_waiting_confirmation",
    "confirmation_rejected",
    "pre_execution_validation_failure",
    "pre_execution_stale_claim",
)
```

Expected structural projections across the logical Run, after filtering unrelated Run/Segment/model events:

```python
{
    "read_success": ["tool.proposed", "tool.started", "tool.completed"],
    "executor_exception": ["tool.proposed", "tool.started", "tool.failed"],
    "write_waiting_confirmation": ["tool.proposed", "approval.requested"],
    "confirmation_rejected": ["tool.proposed", "approval.requested", "approval.decided"],
    "pre_execution_validation_failure": ["tool.proposed"],
    "pre_execution_stale_claim": ["tool.proposed", "approval.requested", "approval.decided"],
}
```

Store only canonical synthetic events in `journal_sequences_30c944f.json`; do not copy the Journal SQLite database.

- [ ] **Step 6: Run and commit the immutable assets**

Run:

```powershell
uv run pytest tests/tool_pipeline/test_golden_assets.py -q
git diff --check
```

Expected: PASS; asset tests prove canonical/read-only/privacy boundaries.

Commit:

```powershell
git add tests/tool_pipeline tests/fixtures/tool_pipeline
git commit -m "test: AI freeze tool pipeline compatibility goldens"
```

---

### Task 2: Add exact Schema dependency, runtime contracts, and generic Catalog

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/offerpilot/ai/tool_runtime/__init__.py`
- Create: `src/offerpilot/ai/tool_runtime/contracts.py`
- Create: `src/offerpilot/ai/tool_runtime/validation.py`
- Create: `src/offerpilot/ai/tool_runtime/catalog.py`
- Create: `tests/tool_pipeline/test_validation.py`
- Create: `tests/tool_pipeline/test_catalog.py`

- [ ] **Step 1: Add failing parser, Schema, transient-value, and Catalog tests**

The tests must assert:

```python
assert parse_arguments('{"a":1}') == {"a": 1}
for raw, code in (
    ('{"a":1,"a":2}', "duplicate_argument_key"),
    ('[]', "arguments_not_object"),
    ('{"x":NaN}', "non_finite_number"),
    ('{"x":1e999}', "non_finite_number"),
    ('{', "invalid_json"),
):
    with pytest.raises(ArgumentValidationError) as error:
        parse_arguments(raw)
    assert error.value.code == code
```

Add separate initialization tests for external `$ref` and `$dynamicRef`; monkeypatch every retrieval/network hook and assert zero calls. Assert a malformed internal Schema fails during Catalog construction, not `prepare_call()`. Assert `pickle.dumps()` and the project generic serializer reject `ToolFailure`, `PreparedToolCall`, `ExecutionAuthorization`, and `ToolExecutionRecord`, and their `repr()` omits arguments/results/compatibility details.

- [ ] **Step 2: Run the tests to verify imports fail**

Run:

```powershell
uv run pytest tests/tool_pipeline/test_validation.py tests/tool_pipeline/test_catalog.py -q
```

Expected: FAIL because `offerpilot.ai.tool_runtime` does not exist.

- [ ] **Step 3: Pin the dependency and implement the closed contracts**

Add `"jsonschema==4.26.0"` to project dependencies and run `uv lock`. Define the central types with these signatures:

```python
JSONValue = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
ToolKind = Literal["read", "write"]
ConfirmationPolicy = Literal["none", "required"]
FailureCategory = Literal[
    "validation_error", "permission_denied", "confirmation_rejected",
    "stale_state", "conflict", "not_found", "provider_error", "internal_error",
]

@dataclass(frozen=True)
class ProviderToolContract:
    payload: Mapping[str, JSONValue] = field(repr=False)
    name: str
    description: str
    parameters: Mapping[str, JSONValue] = field(repr=False)

@dataclass(frozen=True)
class ToolFailure:
    category: FailureCategory
    code: str
    compatibility_detail: str = field(default="", repr=False, compare=False)

@dataclass(frozen=True)
class ToolSuccess(Generic[ResultT]):
    result: ResultT = field(repr=False)

ToolOutcome: TypeAlias = ToolSuccess[ResultT] | ToolFailure
```

Add `ToolSpec`, `PreparedToolCall`, `ConfirmationRequired`, `ReadyToExecute`, `ExecutionAuthorization`, and `ToolExecutionRecord`. `ToolSpec` contains contract, kind, decoder, capabilities, binding resolvers, confirmation policy, editable fields, preflight, mutable validator, executor, declared failure categories, and an ordered per-Spec exception map. It exposes `name` only as `return self.contract.name`; no second mutable name source exists. Add an explicit transient mixin whose pickle/state export methods raise `TypeError`.

- [ ] **Step 4: Implement canonical JSON and sealed Draft 2020-12 compilation**

Use duplicate-key detection and finite-number walking:

```python
def _object_pairs(pairs: list[tuple[str, JSONValue]]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = {}
    for key, value in pairs:
        if key in result:
            raise ArgumentValidationError("duplicate_argument_key")
        result[key] = value
    return result


def _deny_constant(_: str) -> NoReturn:
    raise ArgumentValidationError("non_finite_number")
```

Recursively scan both `$ref` and `$dynamicRef`; every value must be a string beginning with `#`. Compile with `Draft202012Validator.check_schema(schema)` and a `referencing.Registry(retrieve=_deny_retrieval)`. Do not pass a `FormatChecker`. `lossless_typed_copy()` recursively creates new dict/list objects and preserves all keys and JSON values.

- [ ] **Step 5: Implement the generic Catalog without importing Specs**

The generic API is:

```python
class ToolCatalog:
    def __init__(self, specs: Sequence[ToolSpec[Any, Any]], *, expected_names: Sequence[str]):
        ordered = tuple(specs)
        names = tuple(spec.name for spec in ordered)
        if names != tuple(expected_names) or len(set(names)) != len(names):
            raise ValueError("tool catalog names/order mismatch")
        self._specs = {spec.name: spec for spec in ordered}
        self._ordered = ordered
        self._validators = {spec.name: compile_tool_schema(spec.contract.parameters) for spec in ordered}

    def resolve(self, name: str) -> ToolSpec[Any, Any] | None:
        return self._specs.get(name)

    def validator_for(self, name: str) -> Draft202012Validator:
        return self._validators[name]

    def provider_contracts(self) -> tuple[ProviderToolContract, ...]:
        return tuple(spec.contract for spec in self._ordered)

    def write_names(self) -> frozenset[str]:
        return frozenset(spec.name for spec in self._ordered if spec.kind == "write")
```

Construction checks exact names/order, uniqueness, Spec/contract name equality, Schema validity, declared failures, confirmation policy, and one compiled validator per Spec. Add an AST test that `tool_runtime/catalog.py` contains no import whose module includes `tool_specs`.

- [ ] **Step 6: Run tests and static checks**

Run:

```powershell
uv run pytest tests/tool_pipeline/test_validation.py tests/tool_pipeline/test_catalog.py -q
uv run ruff check src/offerpilot/ai/tool_runtime tests/tool_pipeline/test_validation.py tests/tool_pipeline/test_catalog.py
uv run mypy src/offerpilot/ai/tool_runtime
```

Expected: PASS; Mypy sees no `Any` leakage in public generic signatures beyond the heterogeneous Catalog boundary.

- [ ] **Step 7: Commit**

```powershell
git add pyproject.toml uv.lock src/offerpilot/ai/tool_runtime tests/tool_pipeline/test_validation.py tests/tool_pipeline/test_catalog.py
git commit -m "feat: AI add typed tool runtime contracts"
```

---

### Task 3: Implement capability gating and audit-only binding

**Files:**
- Create: `src/offerpilot/ai/tool_runtime/context.py`
- Create: `tests/tool_pipeline/test_context.py`
- Modify: `src/offerpilot/ai/tool_runtime/contracts.py`

- [ ] **Step 1: Write failing capability order and binding aggregation tests**

Use spies to prove missing capability returns before any resolver call:

```python
outcome = evaluate_context(spec, context_without_capability)
assert outcome == ToolFailure("permission_denied", "missing_capability", expected_text)
assert binding_resolver.calls == 0
assert repository.calls == 0
```

Test the exact aggregation table:

```python
assert aggregate_binding(None, []) == "unbound"
assert aggregate_binding(7, []) == "unavailable"
assert aggregate_binding(7, [7, 7]) == "matched"
assert aggregate_binding(7, [7, 9]) == "mismatched"
assert aggregate_binding(7, [7, UNAVAILABLE]) == "unavailable"
assert aggregate_binding(7, [9, UNAVAILABLE]) == "mismatched"
```

- [ ] **Step 2: Run tests to verify failure**

```powershell
uv run pytest tests/tool_pipeline/test_context.py -q
```

Expected: FAIL because context types are missing.

- [ ] **Step 3: Implement context and audit types**

Define:

```python
class ToolCapability(StrEnum):
    APPLICATIONS_READ = "applications.read"
    APPLICATIONS_WRITE = "applications.write"
    APPLICATION_EVENTS_READ = "application_events.read"
    APPLICATION_EVENTS_WRITE = "application_events.write"
    NOTES_READ = "notes.read"
    NOTES_WRITE = "notes.write"
    OFFERS_READ = "offers.read"
    OFFERS_WRITE = "offers.write"
    RESUMES_READ = "resumes.read"
    RESUMES_WRITE = "resumes.write"
    JD_ANALYSES_READ = "jd_analyses.read"

BindingStatus = Literal["matched", "mismatched", "unbound", "unavailable"]

@dataclass(frozen=True)
class ToolExecutionContext:
    capabilities: frozenset[ToolCapability]
    current_bindings: Mapping[str, int | str]
    applications: ApplicationsRepository
    events: ApplicationEventsRepository
    notes: NotesRepository
    offers: OffersRepository
    resumes: ResumesRepository
    jd_analyses: JDAnalysesRepository
    run_recorder: RunRecorder
```

Binding resolvers may read repositories but return only internal targets; the public `BindingAudit` contains entity kind and status, never IDs. Do not inject or rewrite Args.

- [ ] **Step 4: Run tests and commit**

```powershell
uv run pytest tests/tool_pipeline/test_context.py -q
uv run ruff check src/offerpilot/ai/tool_runtime/context.py tests/tool_pipeline/test_context.py
uv run mypy src/offerpilot/ai/tool_runtime
git add src/offerpilot/ai/tool_runtime tests/tool_pipeline/test_context.py
git commit -m "feat: AI add tool capability and binding audit"
```

---

### Task 4: Implement the two-stage Pipeline and pure projectors

**Files:**
- Create: `src/offerpilot/ai/tool_runtime/pipeline.py`
- Create: `src/offerpilot/ai/tool_runtime/rendering.py`
- Create: `src/offerpilot/ai/tool_runtime/transport.py`
- Create: `src/offerpilot/ai/tool_runtime/journal.py`
- Create: `tests/tool_pipeline/test_pipeline.py`
- Create: `tests/tool_pipeline/test_transport.py`
- Create: `tests/tool_pipeline/test_journal.py`

- [ ] **Step 1: Write failing prepare-order and executor-count tests**

Record every stage into a list. Assert these exact sequences:

```python
assert read_trace == ["parse", "schema", "decode", "capability", "binding", "preflight"]
assert read_execute_trace == ["mutable", "tool.started", "executor", "tool.completed"]
assert write_execute_trace == [
    "mutable", "claim", "authorization", "authorization_match",
    "tool.started", "executor", "tool.completed",
]
```

For unknown tool, missing capability, validation, preflight, mutable failure, claim failure, and authorization mismatch, assert executor count 0 and no started/completed/failed Journal execution event. For executor `Exception`, assert count 1 and failed outcome. For `KeyboardInterrupt`, `SystemExit`, and a custom `BaseException`, assert propagation and no second call.

- [ ] **Step 2: Write failing renderer, transport, and Journal tests**

Assert `render(spec, outcome)` is total over every category. Assert transport status/evidence/resources come from typed result, with no `startswith` or `json.loads` of compatibility output. Inject projector and recorder failures and prove the original Outcome object identity and executor count remain unchanged.

Journal assertions:

```python
assert events(read_success) == ["tool.proposed", "tool.started", "tool.completed"]
assert events(executor_error) == ["tool.proposed", "tool.started", "tool.failed"]
assert events(pre_execution_failure) == ["tool.proposed"]
assert started.facts["result_contract"] == "legacy_string_v1"
```

Also simulate `tool.started` append failure; the SafeRunRecorder degraded latch must suppress the terminal event.

- [ ] **Step 3: Run tests to verify failure**

```powershell
uv run pytest tests/tool_pipeline/test_pipeline.py tests/tool_pipeline/test_transport.py tests/tool_pipeline/test_journal.py -q
```

Expected: FAIL because Pipeline and projectors do not exist.

- [ ] **Step 4: Implement prepare_call()**

Use this control shape:

```python
def prepare_call(catalog: ToolCatalog, context: ToolExecutionContext, call: ToolCall) -> PrepareResult:
    spec = catalog.resolve(call.name)
    if spec is None:
        return Rejected(ToolFailure("validation_error", "unknown_tool", unknown_text(call.name)))
    parsed = parse_arguments(call.args)
    validate_arguments(catalog.validator_for(spec.name), parsed)
    args = spec.decode_args(lossless_typed_copy(parsed))
    permission = require_capabilities(spec, context)
    if permission is not None:
        return Rejected(permission)
    audit = audit_bindings(spec, args, context)
    preflight = spec.preflight(args, context)
    if preflight is not None:
        return Rejected(preflight)
    prepared = PreparedToolCall.from_call(spec, call, args, audit)
    return ConfirmationRequired(prepared) if spec.confirmation_policy == "required" else ReadyToExecute(prepared)
```

Convert parser/Schema errors to stable validation codes without exposing raw exceptions.

- [ ] **Step 5: Implement execute_prepared() and authorization matching**

`execute_prepared()` rechecks mutable preconditions. A write then invokes the injected claimer, obtains `ExecutionAuthorization`, and compares Pending identity/revision, call ID, name, and canonical args digest. Only after all gates pass does it project `tool.started`, flip `execution_started`, and call `spec.executor` once. Map only exceptions declared by that Spec; every other ordinary exception becomes `internal_error`. Project terminal Journal events only when `execution_started` is true.

- [ ] **Step 6: Implement pure renderer and transport projector**

The renderer delegates success formatting to the Spec and uses the transient compatibility detail for failures. The transport projector calls typed result accessors for `evidence`, `affected_resources`, and `changed_entities`; it may reuse the pure renderer to produce the 500-character summary but must not infer status or fields from that string.

- [ ] **Step 7: Run tests, static checks, and commit**

```powershell
uv run pytest tests/tool_pipeline/test_pipeline.py tests/tool_pipeline/test_transport.py tests/tool_pipeline/test_journal.py -q
uv run ruff check src/offerpilot/ai/tool_runtime tests/tool_pipeline
uv run mypy src/offerpilot/ai/tool_runtime
git add src/offerpilot/ai/tool_runtime tests/tool_pipeline
git commit -m "feat: AI add typed tool execution pipeline"
```

---

### Task 5: Migrate application and application-event Specs off-path

**Files:**
- Create: `src/offerpilot/ai/tool_specs/__init__.py`
- Create: `src/offerpilot/ai/tool_specs/common.py`
- Create: `src/offerpilot/ai/tool_specs/applications.py`
- Create: `src/offerpilot/ai/tool_specs/application_events.py`
- Create: `tests/tool_pipeline/test_applications.py`
- Create: `tests/tool_pipeline/test_application_events.py`

- [ ] **Step 1: Write differential tests for the nine tool names**

The exact sets are:

```python
APPLICATION_TOOLS = (
    "list_applications", "get_application", "create_application", "update_application_status",
)
EVENT_TOOLS = (
    "list_application_events", "get_application_event", "create_application_event",
    "update_application_event", "delete_application_event",
)
```

For every success and declared failure case in `tool_outcomes_30c944f.json`, run the new Spec in an isolated seeded database and compare compatibility string plus normalized repository projection. Assert reads require read capability, writes require write capability, and create/update event additionally require application read. Assert create-application duplicate preflight is read-only and runs again as a mutable check after confirmation.

- [ ] **Step 2: Run tests to verify missing Specs fail**

```powershell
uv run pytest tests/tool_pipeline/test_applications.py tests/tool_pipeline/test_application_events.py -q
```

Expected: FAIL on missing modules.

- [ ] **Step 3: Move Provider contracts verbatim and implement typed Args/executors**

Copy the exact baseline `description` and Schema bodies into `ProviderToolContract` values; do not regenerate them. Keep JSON primitives in TypedDict Args. Replace generic `ValueError` control paths with explicit shared failures:

```python
class InvalidToolInput(Exception):
    def __init__(self, code: str, compatibility_detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.compatibility_detail = compatibility_detail

class ToolRecordNotFound(Exception):
    def __init__(self, code: str, compatibility_detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.compatibility_detail = compatibility_detail

class ToolStateConflict(Exception):
    def __init__(self, code: str, compatibility_detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.compatibility_detail = compatibility_detail
```

Each Spec declares only its actual mappings. Preserve application result formatting with `json.dumps(value, ensure_ascii=False)` and event formatting with compact separators, matching the golden bytes. Preserve existing Repository calls and ownership checks.

- [ ] **Step 4: Run tests, existing domain tests, and commit**

```powershell
uv run pytest tests/tool_pipeline/test_applications.py tests/tool_pipeline/test_application_events.py tests/test_ai_tools.py -q
uv run ruff check src/offerpilot/ai/tool_specs tests/tool_pipeline/test_applications.py tests/tool_pipeline/test_application_events.py
uv run mypy src/offerpilot/ai/tool_specs src/offerpilot/ai/tool_runtime
git add src/offerpilot/ai/tool_specs tests/tool_pipeline
git commit -m "feat: AI migrate application tool specs"
```

---

### Task 6: Migrate note and offer Specs off-path

**Files:**
- Create: `src/offerpilot/ai/tool_specs/notes.py`
- Create: `src/offerpilot/ai/tool_specs/offers.py`
- Create: `tests/tool_pipeline/test_notes.py`
- Create: `tests/tool_pipeline/test_offers.py`

- [ ] **Step 1: Write differential tests for the nine tool names**

```python
NOTE_TOOLS = ("list_notes", "add_note", "update_note", "delete_note")
OFFER_TOOLS = (
    "list_offers", "get_offer", "compare_offers", "update_offer", "save_offer_assessment",
)
```

Cover unclear/missing note data, application-linked note creation, missing note/offer, empty offer comparison, clear sentinels, and exact delete result. Assert `add_note` declares application read plus note write; all binding results remain audit-only.

- [ ] **Step 2: Run tests to verify failure**

```powershell
uv run pytest tests/tool_pipeline/test_notes.py tests/tool_pipeline/test_offers.py -q
```

Expected: FAIL on missing modules.

- [ ] **Step 3: Implement contracts, typed executors, and exact rendering**

Move current Schemas/descriptions unchanged. Use explicit domain exceptions, preserve `_note_json`/`_offer_json` field sets, preserve compact JSON separators, and retain all current Repository checks. The note date preflight is pure/read-only; the same condition is re-evaluated before the write executor.

- [ ] **Step 4: Run and commit**

```powershell
uv run pytest tests/tool_pipeline/test_notes.py tests/tool_pipeline/test_offers.py tests/test_ai_tools.py -q
uv run ruff check src/offerpilot/ai/tool_specs tests/tool_pipeline/test_notes.py tests/tool_pipeline/test_offers.py
uv run mypy src/offerpilot/ai/tool_specs
git add src/offerpilot/ai/tool_specs tests/tool_pipeline
git commit -m "feat: AI migrate note and offer tool specs"
```

---

### Task 7: Migrate resume and JD-analysis Specs off-path

**Files:**
- Create: `src/offerpilot/ai/tool_specs/resumes.py`
- Create: `src/offerpilot/ai/tool_specs/jd_analyses.py`
- Create: `tests/tool_pipeline/test_resumes.py`
- Create: `tests/tool_pipeline/test_jd_analyses.py`

- [ ] **Step 1: Write differential tests for the seven tool names**

```python
RESUME_TOOLS = (
    "list_resumes", "get_resume", "resume_update_career_intent",
    "resume_rewrite_highlight", "list_resume_matches",
)
JD_TOOLS = ("list_jd_analyses", "get_jd_analysis")
```

Cover deleted/missing resumes, missing analyses, missing match resume, career-intent object, negative indexes, absent sections/highlights, and out-of-range indexes. Compare exact compatibility strings and persisted resume content.

- [ ] **Step 2: Run tests to verify failure**

```powershell
uv run pytest tests/tool_pipeline/test_resumes.py tests/tool_pipeline/test_jd_analyses.py -q
```

Expected: FAIL on missing modules.

- [ ] **Step 3: Implement Specs and preserve JSON semantics**

Keep indexes as JSON integers after Schema validation. Do not coerce strings that the Provider Schema rejects. Preserve `normalize_resume_content`, soft-delete checks, result field sets, and compact JSON formatting. JD tools are read-only and never gain write capability.

- [ ] **Step 4: Run and commit**

```powershell
uv run pytest tests/tool_pipeline/test_resumes.py tests/tool_pipeline/test_jd_analyses.py tests/test_ai_tools.py -q
uv run ruff check src/offerpilot/ai/tool_specs tests/tool_pipeline/test_resumes.py tests/tool_pipeline/test_jd_analyses.py
uv run mypy src/offerpilot/ai/tool_specs
git add src/offerpilot/ai/tool_specs tests/tool_pipeline
git commit -m "feat: AI migrate resume and jd tool specs"
```

---

### Task 8: Assemble the exact Typed Catalog and prove Provider equivalence

**Files:**
- Create: `src/offerpilot/ai/tool_specs/catalog.py`
- Modify: `src/offerpilot/ai/tool_specs/__init__.py`
- Modify: `tests/tool_pipeline/test_catalog.py`
- Modify: `tests/tool_pipeline/test_golden_assets.py`

- [ ] **Step 1: Add failing exact-classification and Provider payload tests**

Assert:

```python
assert catalog.names == EXPECTED_MODEL_TOOL_NAMES
assert len(catalog.names) == 25
assert not set(catalog.names) & LEGACY_DETERMINISTIC_NAMES
assert canonical_json([c.payload for c in catalog.provider_contracts()]) == canonical_json(golden["tools"])
```

Add mutation tests for missing, duplicate, reordered, extra, and legacy-in-typed entries. Each must raise `ToolCatalogConfigurationError` at initialization.

- [ ] **Step 2: Run tests to verify failure**

```powershell
uv run pytest tests/tool_pipeline/test_catalog.py tests/tool_pipeline/test_golden_assets.py -q
```

Expected: FAIL because the composition root is absent.

- [ ] **Step 3: Implement the composition root**

Define the ordered tuple explicitly, not by sorting or dictionary discovery:

```python
MODEL_TOOL_SPECS = (
    *APPLICATION_SPECS,
    *APPLICATION_EVENT_SPECS,
    *NOTE_SPECS,
    *OFFER_SPECS,
    *RESUME_SPECS,
    *JD_ANALYSIS_SPECS,
)
EXPECTED_MODEL_TOOL_NAMES = tuple(spec.name for spec in MODEL_TOOL_SPECS)
MODEL_TOOL_CATALOG = ToolCatalog(MODEL_TOOL_SPECS, expected_names=EXPECTED_25_NAMES)
```

`tool_specs/catalog.py` may import runtime; runtime must not import Specs.

- [ ] **Step 4: Run tests and commit**

```powershell
uv run pytest tests/tool_pipeline -q
uv run ruff check src/offerpilot/ai/tool_runtime src/offerpilot/ai/tool_specs tests/tool_pipeline
uv run mypy src/offerpilot/ai/tool_runtime src/offerpilot/ai/tool_specs
git add src/offerpilot/ai/tool_specs tests/tool_pipeline
git commit -m "feat: AI assemble typed tool catalog"
```

---

### Task 9: Isolate the three deterministic Legacy Adapters

**Files:**
- Create: `src/offerpilot/ai/tool_runtime/legacy.py`
- Create: `src/offerpilot/ai/tool_specs/legacy.py`
- Create: `tests/tool_pipeline/test_legacy.py`
- Read: `src/offerpilot/ai/deterministic_actions.py`
- Read: `src/offerpilot/ai/tools.py`

- [ ] **Step 1: Write failing isolation and trusted-routing tests**

Assert the exact names:

```python
LEGACY_DETERMINISTIC_NAMES = frozenset({
    "save_application_jd_version",
    "create_application_submission_snapshot",
    "record_application_outcome",
})
```

Prove Provider payload has none of them; model dispatcher typed lookup returns unknown tool and never calls Legacy; direct client-supplied `tool_name` cannot select Legacy; a server-loaded Pending Action with an exact legacy name can select its adapter. Compare validate/describe/execute strings and business state to the baseline golden.

- [ ] **Step 2: Run tests to verify failure**

```powershell
uv run pytest tests/tool_pipeline/test_legacy.py -q
```

Expected: FAIL because Legacy Catalog does not exist.

- [ ] **Step 3: Move the three adapters without changing their protocol**

Define:

```python
@dataclass(frozen=True)
class LegacyDeterministicAdapter:
    name: str
    editable_fields: tuple[Mapping[str, JSONValue], ...]
    describe: Callable[[str, ToolExecutionContext], str]
    validate: Callable[[str, ToolExecutionContext], str]
    execute: Callable[[str, ToolExecutionContext], str]

class LegacyDeterministicCatalog:
    def __init__(self, adapters: Sequence[LegacyDeterministicAdapter]) -> None:
        self._adapters = {adapter.name: adapter for adapter in adapters}
        if frozenset(self._adapters) != LEGACY_DETERMINISTIC_NAMES:
            raise ValueError("legacy deterministic catalog mismatch")

    def resolve_server_loaded(self, pending: PendingAction) -> LegacyDeterministicAdapter | None:
        return self._adapters.get(pending.tool_name)
```

`resolve_server_loaded()` is callable only after the API has loaded the authoritative Pending Action and revision from `ChatRepository`; the request body's `tool_name` is never passed to it. Copy the existing three validators/describers/handlers exactly. This is the only production module allowed to retain legacy string interpretation.

- [ ] **Step 4: Run and commit**

```powershell
uv run pytest tests/tool_pipeline/test_legacy.py tests/test_application_outcomes_pilot.py tests/test_chat_api.py -q
uv run ruff check src/offerpilot/ai/tool_runtime/legacy.py src/offerpilot/ai/tool_specs/legacy.py tests/tool_pipeline/test_legacy.py
uv run mypy src/offerpilot/ai/tool_runtime/legacy.py src/offerpilot/ai/tool_specs/legacy.py
git add src/offerpilot/ai/tool_runtime src/offerpilot/ai/tool_specs tests/tool_pipeline
git commit -m "refactor: AI isolate deterministic legacy tools"
```

---

### Task 10: Perform the atomic production cutover and delete the model-visible legacy path

**Files:**
- Modify: `src/offerpilot/ai/agent.py`
- Modify: `src/offerpilot/ai/client.py`
- Modify: `src/offerpilot/api.py`
- Modify: `src/offerpilot/db.py`
- Modify: `src/offerpilot/models.py`
- Modify: `src/offerpilot/repositories/chat.py`
- Modify: `src/offerpilot/smoke.py`
- Delete: `src/offerpilot/ai/tools.py`
- Modify: `tests/test_ai_agent.py`
- Modify: `tests/test_chat_api.py`
- Modify: `tests/test_chat_repository.py`
- Modify: `tests/test_ai_tools.py`
- Modify: `tests/test_knowledge_sources_api.py`
- Create: `tests/tool_pipeline/test_checkpoint.py`
- Create: `tests/tool_pipeline/test_confirmation_claim_schema.py`
- Modify: `tests/tool_pipeline/test_transport.py`
- Modify: `tests/tool_pipeline/test_journal.py`

- [ ] **Step 1: Add failing end-to-end cutover assertions before changing production code**

Add tests for:

```python
MULTI_CALL_CASES = {
    "read_read": ["read-1", "read-2"],
    "write_read": ["write-1"],
    "read_write": ["read-1"],
    "write_write": ["write-1"],
}
```

Lock the baseline behavior when the first read fails, for both sync and stream. Add Provider spy equality, exact model/tool call counts, approve/modify/reject, missing checkpoint, SQLite checkpoint, claim race, stale authorization digest, and HTTP/SSE payload assertions. Inspect checkpoint rows and recursively reject keys/types for Outcome, typed result, capabilities, bindings, exception objects, Catalog, Context, and ExecutionRecord.

- [ ] **Step 2: Run the focused suite to establish the red cutover**

```powershell
uv run pytest tests/tool_pipeline tests/test_ai_agent.py tests/test_chat_api.py tests/test_ai_tools.py tests/test_knowledge_sources_api.py -q
```

Expected: FAIL because Agent/API still use the dict registry and string control flow.

- [ ] **Step 3: Change Provider and Agent interfaces destructively**

Change `ChatModel`/`StreamingChatModel` tools to `Sequence[ProviderToolContract]`. `_openai_tool` accepts only `ProviderToolContract` and returns a fresh JSON copy of its complete payload.

Replace tuple returns with a transient result:

```python
@dataclass(frozen=True)
class AgentTurnResult:
    added: tuple[Message, ...]
    reply: str
    pending: PendingAction | None
    records: tuple[ToolExecutionRecord[Any], ...] = field(repr=False)
```

Runner constructor receives `ToolCatalog` and `ToolExecutionContext`. Keep `_GraphState` limited to messages and existing control fields. Store current-call records only on the request-scoped runner and clear them before each invocation.

Replace `_select_tool_calls` with Catalog kind lookup while preserving the exact rule: if every call resolves to read, retain all; otherwise retain only `tool_calls[0]`. Unknown calls are not routed to Legacy.

- [ ] **Step 4: Replace confirmation callbacks with bound authorization**

Change the pre-executor callback from a void attempt sink to:

```python
ConfirmationClaimer = Callable[[PendingAction, str], ExecutionAuthorization]
```

The API implementation reads the current server Pending Action under the existing lock, validates identity/token, performs the existing claim/CAS, and returns authorization bound to Pending identity, call ID, name, and effective canonical args digest. `execute_prepared()` verifies it before `tool.started`. Rejection uses the trusted Pending Action and rejection CAS without `prepare_call()`.

- [ ] **Step 5: Replace API registry creation and string parsing**

At all five current `offerpilot_tool_registry()` call sites, inject `MODEL_TOOL_CATALOG`, a fresh `ToolExecutionContext`, and the Legacy Catalog only for trusted deterministic routing. Replace `_confirmation_result_recorder`, `_write_error_followup`, `_last_successful_tool_payload`, `_write_outcome`, `_pending_action_from_added_write_call`, and write-name helpers so they consume `ToolExecutionRecord`/Spec metadata. They may use the compatibility string only when persisting `Message.content` or returning the existing transport body.

Use `project_transport_event()` for existing tool events; do not parse the rendered string. Preserve `editable_fields`, confirmation summaries, Pending Action JSON, undo seed, CAS result persistence, timeouts, cancellation, chained pending writes, and follow-up model calls.

- [ ] **Step 6: Route trusted deterministic confirmations and delete tools.py**

Initial deterministic actions continue to be created only by `deterministic_actions.py`. On confirmation, load the server Pending Action first and resolve it in `LegacyDeterministicCatalog`. Delete `src/offerpilot/ai/tools.py` only after every import is replaced. There must be no production compatibility module for the 25 migrated handlers.

- [ ] **Step 7: Preserve Journal stage semantics in Agent integration**

The runner records `tool.proposed` on the selected call. Waiting writes record `approval.requested` and no started event. `execute_prepared()` records started immediately before executor and terminal only after executor return/ordinary exception. Pre-execution validation, capability, preflight, rejection, stale, claim, and authorization failures produce no started/completed/failed. Keep `legacy_string_v1`.

- [ ] **Step 8: Run focused equivalence suites**

Run separately to avoid one long foreground command:

```powershell
uv run pytest tests/tool_pipeline -q
uv run pytest tests/test_ai_tools.py tests/test_ai_agent.py -q
uv run pytest tests/test_chat_api.py -q
uv run pytest tests/test_knowledge_sources_api.py tests/test_application_outcomes_pilot.py -q
```

Expected: every command passes; golden Provider/tool/Journal projections are unchanged.

- [ ] **Step 9: Run static checks and commit the atomic cutover**

```powershell
uv run ruff check src/offerpilot/ai src/offerpilot/api.py tests/tool_pipeline tests/test_ai_tools.py tests/test_ai_agent.py tests/test_chat_api.py tests/test_knowledge_sources_api.py
uv run mypy src/offerpilot/ai src/offerpilot/api.py
git diff --check
git add -A src/offerpilot/ai src/offerpilot/api.py src/offerpilot/smoke.py tests/tool_pipeline tests/test_ai_tools.py tests/test_ai_agent.py tests/test_chat_api.py tests/test_knowledge_sources_api.py
git commit -m "refactor: AI cut over typed tool execution pipeline"
```

---

### Task 11: Add mechanical proof that no old path or hidden exposure remains

**Files:**
- Create: `tests/tool_pipeline/test_source_gates.py`
- Modify: `tests/tool_pipeline/test_catalog.py`

- [ ] **Step 1: Write the AST/source gate**

Parse all production Python files and assert:

```python
BANNED_SYMBOLS = {
    "offerpilot_tool_registry", "application_tool_registry", "event_tool_registry",
    "note_tool_registry", "offer_tool_registry", "resume_tool_registry", "jd_tool_registry",
    "_execute_tool", "_model_visible_tools",
}
LEGACY_PREFIX_ALLOWLIST = {
    ("src/offerpilot/ai/tool_specs/legacy.py", "save_application_jd_version"),
    ("src/offerpilot/ai/tool_specs/legacy.py", "create_application_submission_snapshot"),
    ("src/offerpilot/ai/tool_specs/legacy.py", "record_application_outcome"),
}
```

Reject dict access to `handler`, `validate`, `write`, and `model_visible` in Agent/API; imports from runtime to Specs; imports from model dispatcher to Legacy; Provider builder parameters typed as dict registry; `startswith("错误：")` outside the exact allowlist; compatibility parsing in transport; production flags/names containing `shadow`, `dual_run`, `legacy_fallback`, or `tool_pipeline_enabled`; multiple call sites of a migrated executor outside its Spec.

Also parse `pyproject.toml` and `uv.lock` and assert exact `jsonschema==4.26.0` resolution.

- [ ] **Step 2: Run the gate to find residual paths**

```powershell
uv run pytest tests/tool_pipeline/test_source_gates.py -q
```

Expected: initially FAIL on any missed legacy symbol; remove each residual production path rather than widening the allowlist.

- [ ] **Step 3: Run complete focused tests and commit**

```powershell
uv run pytest tests/tool_pipeline tests/test_ai_tools.py tests/test_ai_agent.py tests/test_chat_api.py tests/test_knowledge_sources_api.py tests/test_application_outcomes_pilot.py -q
uv run ruff check .
uv run mypy src
git diff --check
git add tests/tool_pipeline src/offerpilot/ai src/offerpilot/api.py pyproject.toml uv.lock
git commit -m "test: AI enforce typed tool pipeline cutover"
```

---

### Task 12: Run release-level verification, browser acceptance, and independent CR

**Files:**
- Create after gates: `docs/reports/2026-08-18-tool-execution-pipeline-release-verification.md`
- No production edits unless a gate or review finding requires a fix.

- [ ] **Step 1: Re-run the immutable scope assertion and focused privacy scan**

Run the Task 0 scope command, then:

```powershell
git diff --check 30c944f3bda1d99b303f8e9875a170a552f79af7..HEAD
uv run pytest tests/tool_pipeline/test_golden_assets.py tests/tool_pipeline/test_checkpoint.py tests/tool_pipeline/test_journal.py tests/tool_pipeline/test_source_gates.py -q
```

Expected: no scope violation, no private content in assets/checkpoint/Journal, no structural Trace anomaly.

- [ ] **Step 2: Request independent code review before final verification**

Use an independent subagent reviewer as required by `AGENTS.md`. Give it the fixed baseline, exact allowlist, design, plan, all commits, and these mandatory review targets:

```text
Provider full-envelope canonical equivalence and order
25 typed / 3 deterministic classification and model invisibility
Schema duplicate/non-finite/$ref/$dynamicRef/retrieval boundaries
capability short-circuit before binding/repository access
binding audit-only behavior and existing ownership checks
prepare/execute ordering and executor call counts
confirmation claim/authorization/args digest ordering
bounded stale confirmation-claim recovery, empty claim rejection, and 0024 upgrade idempotency
checkpoint absence of transient runtime values
renderer/transport/Journal failure behavior
Journal started/terminal structural sequence
absence of string status parsing and old-path fallback
HTTP/SSE/HITL/Pending Action/CAS/business side-effect equivalence
```

Resolve every P0/P1/P2. Rerun focused tests after fixes and commit each logical fix separately.

- [ ] **Step 3: Generate a fresh backend manifest without hiding duplicates**

```powershell
$locator = Get-Content -Raw (Join-Path $env:TEMP 'offerpilot-tool-pipeline-gate.locator.json') | ConvertFrom-Json
$backend = Join-Path (Split-Path $locator.baseline_path -Parent) 'backend-results'
New-Item -ItemType Directory -Force -Path $backend | Out-Null
$raw = @(& uv run pytest --collect-only -q --disable-warnings tests 2>&1)
if($LASTEXITCODE -ne 0){ throw 'backend collection failed' }
$nodes = @($raw | ForEach-Object { $line=[string]$_; if($line.Trim() -match '^(tests[\\/].+::.+)$'){ $Matches[1].Replace('/','\') } } | Where-Object { $_ })
$duplicates = @($nodes | Group-Object | Where-Object Count -gt 1)
if($duplicates.Count -gt 0){ throw "duplicate backend node ids: $($duplicates.Name -join ', ')" }
$nodes | Set-Content -LiteralPath (Join-Path $backend 'full-manifest.txt') -Encoding utf8
```

Expected: non-empty unique manifest.

- [ ] **Step 4: Run all backend and frontend groups**

```powershell
foreach($group in @('agent','domain','knowledge','proposals','misc')){
  & powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-pytest-groups.ps1 -Group $group -ResultDir $backend
  if($LASTEXITCODE -ne 0){ throw "$group failed" }
}
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-pytest-groups.ps1 -Aggregate -ResultDir $backend
if($LASTEXITCODE -ne 0){ throw 'backend aggregate failed' }

$frontend = Join-Path (Split-Path $locator.baseline_path -Parent) 'frontend-results'
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Collect -RepositoryRoot $locator.repository_root -ResultDir $frontend
foreach($group in @('components-core','components-chat','components-interview','components-offer','components-support','features','layout','lib','services','theme')){
  & powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Group $group -RepositoryRoot $locator.repository_root -ResultDir $frontend
  if($LASTEXITCODE -ne 0){ throw "$group failed" }
}
& powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Aggregate -RepositoryRoot $locator.repository_root -ResultDir $frontend
if($LASTEXITCODE -ne 0){ throw 'frontend aggregate failed' }
```

Expected: backend union equals manifest, duplicate node IDs absent, only fixed infrastructure skips remain, all frontend groups and aggregate pass with no skipped/pending tests.

- [ ] **Step 5: Run static, build, local, and controlled real-AI gates**

Run each command separately and poll long-running processes:

```powershell
uv run ruff check .
uv run mypy src
Push-Location web
try { npm.cmd run build } finally { Pop-Location }
uv run oc smoke --static-dir web/dist
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-smoke.ps1 -Port 18766
uv run oc verify --profile local --static-dir web/dist
uv run oc verify --profile real-ai --static-dir web/dist
```

Expected: every local command exits 0. Run the existing bounded real-AI profile once; record an external failure without retrying unless the user gives a new decision.

- [ ] **Step 6: Perform one real local-browser Chat acceptance loop**

Use the in-app Browser against the local app. Exercise and capture evidence for:

1. one model response with two read-only tool calls in order;
2. one write that pauses for confirmation and then succeeds;
3. one edited confirmation whose effective Args are used;
4. one rejection whose executor count remains zero;
5. the same representative read/write behavior through SSE.

Inspect UI-visible messages, network HTTP/SSE payloads, database side effects, model count, tool count, Pending Action clearing, and Journal sequence. Do not save user text, tokens, or secrets in the report.

- [ ] **Step 7: Write the release verification report and commit**

The report must include baseline, allowlist, destructive internal switch, exact 25/3 boundary, commit list, golden hashes/counts, focused tests, grouped manifest/aggregate counts, skips, Ruff/Mypy/frontend/build/local/real-AI outcomes, browser acceptance, CR findings/resolutions, remaining legacy list, and the explicit lack of cross-request exactly-once.

```powershell
git add -f docs/reports/2026-08-18-tool-execution-pipeline-release-verification.md
git commit -m "docs: AI record tool pipeline verification"
```

- [ ] **Step 8: Final scope, cleanliness, and evidence checks**

Rerun the scope assertion, then:

```powershell
git diff --check 30c944f3bda1d99b303f8e9875a170a552f79af7..HEAD
git status --short --branch
git ls-files --others --exclude-standard
```

Expected: no scope violation, no untracked file, clean worktree, and no P0/P1/P2 finding. Retain temp gate evidence until the user accepts the final report; do not push or merge.

---

## Self-review checklist

- [ ] Every approved model-visible tool appears exactly once across Tasks 5–8.
- [ ] The three deterministic names appear only in Task 9 and trusted API routing.
- [ ] Provider golden captures the final envelope before old code changes and cannot self-update.
- [ ] JSON parser rejects non-object, duplicate keys, and all non-finite values.
- [ ] `$ref` and `$dynamicRef` are local-only and Registry retrieval is disabled/tested with zero network calls.
- [ ] Capability short-circuits before binding or Repository access.
- [ ] Binding aggregation covers all three mixed-target precedence cases and remains audit-only.
- [ ] Rejection never calls `prepare_call()` or an executor.
- [ ] Approved writes order mutable check → claim/CAS → authorization → match → started → executor.
- [ ] Pre-executor failures do not produce started/completed/failed Journal events.
- [ ] Executor `Exception` produces exactly one call and a typed failure; `BaseException` propagates.
- [ ] Renderer, transport, and Journal projection failures cannot replace the Outcome or rerun the executor.
- [ ] Graph State/checkpoint contains only compatible messages and existing safe controls.
- [ ] Sync and stream share Pipeline semantics and the exact multi-call selection rule.
- [ ] Source gates prove old model-visible handler paths and prefix parsing are absent.
- [ ] Release gates include backend manifest union/duplicates/skips/aggregate, frontend aggregate, local verify, bounded real-AI verify, browser Chat closure, independent CR, allowlist, diff check, and clean status.
