# Evidence-Gated Material Proposal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Let a user turn an AI-generated, evidence-cited resume revision into a deliberately approved, application-linked child Resume without overwriting the source Resume.

**Architecture:** A new immutable proposal aggregate freezes internal sources, validates a constrained JSON change set from the configured model, and only writes a child Resume through a separate compare-and-swap accept transaction. The Material Kit drawer owns entry; a dedicated modal owns review; Pilot Chat remains unchanged.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy/SQLite, existing ConfiguredAIClient, React, TypeScript, TanStack Query, Ant Design, Vitest.

---

## Handoff context

- Worktree: D:\Users\yuqi.chen\offerpilot\.worktrees\feat-20260715-evidence-gated-material-proposal
- Branch: feat/20260715-evidence-gated-material-proposal
- Base: e9baa30 feat: AI merge application evidence bundle. Before coding, fetch and rebase onto current origin/main.
- Product contract: docs/superpowers/specs/2026-07-15-evidence-gated-material-proposal-design.md
- Existing patterns: src/offerpilot/repositories/evidence_bundles.py, src/offerpilot/repositories/resumes.py, src/offerpilot/repositories/material_kits.py, src/offerpilot/api.py, web/src/components/MaterialKitDrawer.tsx.

Read AGENTS.md, docs/python-rewrite-contract.md, docs/p0-release-checklist.md, and the design before changing code. Do not add a Pilot write tool, external platform access, scraping, PDF processing, automatic acceptance, or a general version graph.

### Task 1: Define failure-first proposal behavior

**Files:**

- Create: tests/test_material_revision_proposals_api.py
- Create: tests/test_material_revision_proposals_repository.py
- Create: tests/test_material_revision_proposals_ai.py

- [ ] **Step 1: Add a deterministic fake model with one valid citeable change.**

~~~
class ProposalModel:
    def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        return Assistant(content=json.dumps({
            "summary": "针对 Acme 后端岗位的材料建议",
            "changes": [{
                "id": "change-fastapi",
                "path": "/experience/0/highlights/0",
                "before": "Built APIs",
                "after": "Built FastAPI APIs for internal workflow automation",
                "rationale": "突出既有 API 经验",
                "evidence_refs": [{
                    "source": "resume",
                    "path": "/experience/0/highlights/0",
                    "excerpt": "Built APIs",
                }],
            }],
        }))
~~~

- [ ] **Step 2: Write the failing create and accept contract test.**

~~~
created = client.post(
    f"/api/applications/{app_id}/material-revision-proposals",
    json={"instructions": "突出后端 API 经验", "user_assertions": []},
)
assert created.status_code == 201
proposal = created.json()
assert proposal["status"] == "draft"

accepted = client.post(
    f"/api/applications/{app_id}/material-revision-proposals/{proposal['id']}/accept",
    json={
        "expected_proposal_sha256": proposal["proposal_sha256"],
        "selected_change_ids": ["change-fastapi"],
    },
)
assert accepted.status_code == 201
assert accepted.json()["result_resume"]["parent_resume_id"] == resume_id
~~~

- [ ] **Step 3: Add negative tests.** Cover invalid model citation/path as 502, no selected changes as 422, source mutation as 409 with no writes, repeated accept idempotency, reject-after-accept 409, and hidden Application 404.

- [ ] **Step 4: Run the new tests to prove the gap.**

Run: uv run pytest tests/test_material_revision_proposals_api.py tests/test_material_revision_proposals_repository.py tests/test_material_revision_proposals_ai.py -q

Expected: tests fail because proposal modules/routes do not exist.

- [ ] **Step 5: Commit tests.**

~~~
git add tests/test_material_revision_proposals_api.py tests/test_material_revision_proposals_repository.py tests/test_material_revision_proposals_ai.py
git commit -m "test: AI define material proposal contract"
~~~

### Task 2: Persist immutable proposal records

**Files:**

- Modify: src/offerpilot/models.py
- Modify: src/offerpilot/db.py
- Modify: src/offerpilot/schemas.py
- Modify: tests/test_material_revision_proposals_repository.py

- [ ] **Step 1: Add MaterialRevisionProposal immediately after ApplicationEvidenceBundle.** It needs application_id, material_kit_id, source_resume_id, source_fingerprint_sha256, source_snapshot_json, proposal_json, proposal_sha256, status, accepted_change_ids_json, result_resume_id, timestamps, a (application_id, created_at) index, and unique result_resume_id. Use the foreign-key behavior defined in the design.

- [ ] **Step 2: Record additive migration marker 0007_material_revision_proposals.**

~~~
_record_migration(
    engine,
    "0007_material_revision_proposals",
    "Add evidence-gated material revision proposals",
)
~~~

Base.metadata.create_all(engine) handles clean DBs. Do not add destructive reset behavior for this additive table.

- [ ] **Step 3: Add structured output schemas.**

~~~
class MaterialRevisionProposalSummaryOut(BaseModel):
    id: int
    application_id: int
    material_kit_id: int
    source_resume_id: int | None
    status: Literal["draft", "accepted", "rejected"]
    summary: str
    proposal_sha256: str
    result_resume_id: int | None
    created_at: datetime

class MaterialRevisionProposalOut(MaterialRevisionProposalSummaryOut):
    changes: list[dict[str, Any]]
    source: dict[str, Any]
    accepted_change_ids: list[str]
    accepted_at: datetime | None
    rejected_at: datetime | None
~~~

- [ ] **Step 4: Verify persistence.**

Run: uv run pytest tests/test_material_revision_proposals_repository.py -q

Expected: schema/persistence assertions pass without dropping a table.

- [ ] **Step 5: Commit.**

~~~
git add src/offerpilot/models.py src/offerpilot/db.py src/offerpilot/schemas.py tests/test_material_revision_proposals_repository.py
git commit -m "feat: AI add material revision proposal model"
~~~

### Task 3: Create canonical source and model-output validators

**Files:**

- Create: src/offerpilot/repositories/json_contract.py
- Create: src/offerpilot/ai/material_proposals.py
- Modify: src/offerpilot/repositories/evidence_bundles.py
- Modify: tests/test_material_revision_proposals_ai.py
- Modify: tests/test_evidence_bundles_repository.py

- [ ] **Step 1: Move canonical JSON primitives without behavior change.** Export canonical_json, sha256_text, and strict parse_json_object from json_contract.py; change evidence bundles to import them. Existing evidence-bundle hash tests must remain unchanged and pass.

- [ ] **Step 2: Implement strict change validation.**

~~~
def validate_material_proposal(
    payload: dict[str, Any], source_snapshot: dict[str, Any]
) -> ValidatedProposal:
    """Validate ids, allowed scalar paths, before values, non-overlap,
    evidence references and excerpts; derive content from source plus changes."""
~~~

Only permit /career_intent/target_roles/<index>, /experience/<index>/highlights/<index>, /projects/<index>/highlights/<index>, /skills/<index>, and /raw_text. Decode JSON Pointer ~0/~1, reject -, negative/non-canonical indices and unknown paths. Do not use a generic pointer implementation as the authorization decision. Apply valid changes to a deep copy of frozen source Resume content.

- [ ] **Step 3: Build a prompt and model wrapper.** Reuse complete_json; prompt with frozen source snapshot, say JD is not a candidate-fact source, require the design JSON, prohibit new numerical/date/employer/role facts, and permit empty changes. Convert malformed or unverifiable output to MaterialProposalModelError.

- [ ] **Step 4: Run validators and regression tests.**

Run: uv run pytest tests/test_material_revision_proposals_ai.py tests/test_evidence_bundles_repository.py -q

Expected: invalid citation/path cases fail safely; existing hashes remain stable.

- [ ] **Step 5: Commit.**

~~~
git add src/offerpilot/repositories/json_contract.py src/offerpilot/ai/material_proposals.py src/offerpilot/repositories/evidence_bundles.py tests/test_material_revision_proposals_ai.py tests/test_evidence_bundles_repository.py
git commit -m "feat: AI validate evidence-gated material changes"
~~~

### Task 4: Implement generate/read/accept/reject repository transactions

**Files:**

- Create: src/offerpilot/repositories/material_revision_proposals.py
- Modify: tests/test_material_revision_proposals_repository.py

- [ ] **Step 1: Build a current source snapshot.** build_source_snapshot(session, application_id, user_assertions) loads visible Application, exactly one kit, visible linked Resume, non-empty jd_snapshot, and latest evidence bundle if any. It returns snapshot plus sha256_text(canonical_json(snapshot)).

- [ ] **Step 2: Implement this repository surface.**

~~~
class MaterialRevisionProposalsRepository:
    def create_generated(self, application_id: int, instructions: str, user_assertions: list[str], model: ChatModel) -> MaterialRevisionProposal: ...
    def list(self, application_id: int) -> list[MaterialRevisionProposal]: ...
    def get(self, application_id: int, proposal_id: int) -> MaterialRevisionProposal | None: ...
    def accept(self, application_id: int, proposal_id: int, expected_proposal_sha256: str, selected_change_ids: list[str]) -> tuple[MaterialRevisionProposal, Resume, bool]: ...
    def reject(self, application_id: int, proposal_id: int) -> MaterialRevisionProposal: ...
~~~

Generation validates output before inserting draft; failure writes no proposal. List/detail join Application and filter deleted_at IS NULL.

- [ ] **Step 3: Implement accept in one session transaction.** Rebuild source and compare server-computed fingerprint, apply selected frozen changes, create child Resume, update only kit.resume_id, set proposal acceptance fields, add event, then commit. For a source without string raw_text, carry forward parsed_data; otherwise use resulting raw_text.

~~~
event = ApplicationEvent(
    application_id=application_id,
    event_type="custom",
    subtype="material_proposal_accepted",
    status="done",
    notes="用户确认创建 AI 材料建议的新简历版本",
)
event.tags = ["material_proposal", f"proposal:{proposal.id}", f"resume:{result.id}"]
~~~

An already accepted proposal returns its saved Resume with created=False; a rejected proposal conflicts. Roll back every exception and test that conflict creates no Resume/event/kit update.

- [ ] **Step 4: Run repository tests.**

Run: uv run pytest tests/test_material_revision_proposals_repository.py -q

Expected: partial selection, conflict, idempotency, rollback, rejection and soft-delete tests pass.

- [ ] **Step 5: Commit.**

~~~
git add src/offerpilot/repositories/material_revision_proposals.py tests/test_material_revision_proposals_repository.py
git commit -m "feat: AI persist approved material revisions"
~~~

### Task 5: Expose HTTP without a Pilot tool

**Files:**

- Modify: src/offerpilot/api.py
- Modify: src/offerpilot/schemas.py
- Modify: tests/test_material_revision_proposals_api.py

- [ ] **Step 1: Construct the repository in create_app and reuse _chat_model(chat_model, resolved_data_dir) for generation.** This preserves fake-model tests and configured provider behavior.

- [ ] **Step 2: Add the five design routes after evidence-bundle routes.** Detail serialization includes summary, source labels/excerpts, changes and status only; never return source snapshot verbatim. Validate user assertions (nonempty, <=500 chars, <=10) and selected ids before repository calls.

- [ ] **Step 3: Map errors exactly.**

~~~
except MaterialProposalNotFound:
    return error_response(404, "Material revision proposal not found")
except MaterialProposalValidationError as exc:
    return error_response(422, str(exc))
except MaterialProposalConflictError as exc:
    return error_response(409, str(exc))
except MaterialProposalModelError:
    return error_response(502, "模型返回无法核验的材料建议，请重试")
~~~

- [ ] **Step 4: Verify routes and commit.**

Run: uv run pytest tests/test_material_revision_proposals_api.py tests/test_material_kits_api.py tests/test_evidence_bundles_api.py -q

Expected: all route/status/no-write-on-error assertions pass.

~~~
git add src/offerpilot/api.py src/offerpilot/schemas.py tests/test_material_revision_proposals_api.py
git commit -m "feat: AI expose material proposal review API"
~~~

### Task 6: Add typed client and review UI

**Files:**

- Create: web/src/types/materialRevisionProposal.ts
- Create: web/src/services/materialRevisionProposals.ts
- Create: web/src/services/materialRevisionProposals.test.ts
- Create: web/src/components/MaterialProposalReviewModal.tsx
- Create: web/src/components/MaterialProposalReviewModal.module.css
- Create: web/src/components/MaterialProposalReviewModal.test.tsx
- Modify: web/src/components/MaterialKitDrawer.tsx
- Modify: web/src/components/MaterialKitDrawer.module.css
- Modify: web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx

- [ ] **Step 1: Define client contract and exact calls.**

~~~
export type MaterialProposalStatus = 'draft' | 'accepted' | 'rejected';
export interface MaterialRevisionChange {
  id: string; path: string; before: string; after: string; rationale: string;
  evidence_refs: Array<{ source: 'resume' | 'evidence_bundle' | 'user_assertion'; path: string; excerpt: string }>;
}
~~~

Service tests must assert:

~~~
expect(postMock).toHaveBeenCalledWith('/applications/7/material-revision-proposals/3/accept', {
  expected_proposal_sha256: 'abc', selected_change_ids: ['change-fastapi'],
});
~~~

- [ ] **Step 2: Write modal tests before implementation.** Assert warning label, evidence excerpt, user-assertion label, default selection, disabled zero selection, selected ids/SHA on accept, 409 retry guidance, and reject not calling accept.

- [ ] **Step 3: Implement MaterialProposalReviewModal.** Keep selection local and reset only when proposal.id changes. Render text, never raw HTML. The accept confirmation says: 将创建新的派生简历版本，不会覆盖源简历。

- [ ] **Step 4: Integrate a single generation action in MaterialKitDrawer.** Enable only if application, existing kit, resume and JD exist. On acceptance invalidate:

~~~
['resumes']
['application-material-kit', applicationID]
['application-evidence-bundle-preview', applicationID]
['application-evidence-bundles', applicationID]
['application-events', applicationID]
~~~

The browser may call only local /api; no direct model call.

- [ ] **Step 5: Run web tests and commit.**

Run: Set-Location web; npm.cmd test -- --run src/services/materialRevisionProposals.test.ts src/components/MaterialProposalReviewModal.test.tsx src/components/MaterialKitDrawer.evidenceBundles.test.tsx

Expected: PASS.

~~~
git add web/src/types/materialRevisionProposal.ts web/src/services/materialRevisionProposals.ts web/src/services/materialRevisionProposals.test.ts web/src/components/MaterialProposalReviewModal.tsx web/src/components/MaterialProposalReviewModal.module.css web/src/components/MaterialProposalReviewModal.test.tsx web/src/components/MaterialKitDrawer.tsx web/src/components/MaterialKitDrawer.module.css web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx
git commit -m "feat: AI review evidence-gated material changes"
~~~

### Task 7: Verify, validate with real AI, and obtain review

**Files:**

- Modify only the smallest relevant files if verification finds a defect.

- [ ] **Step 1: Run targeted and full gates.**

~~~
uv run pytest tests/test_material_revision_proposals_api.py tests/test_material_revision_proposals_repository.py tests/test_material_revision_proposals_ai.py tests/test_material_kits_api.py tests/test_resumes_api.py tests/test_evidence_bundles_api.py -q
uv run ruff check .
uv run mypy src
Set-Location web; npm.cmd test -- --run
Set-Location web; npm.cmd run build
Set-Location ..; uv run pytest
Set-Location ..; uv run oc smoke --static-dir web/dist
~~~

- [ ] **Step 2: Walk through a local browser scenario.** Generate a two-change proposal, deselect one, accept one, confirm child Resume/kit linkage/source immutability, then mutate a source and see 409. Confirm no request goes to a recruitment platform or browser automation endpoint.

- [ ] **Step 3: Run real AI only under already configured and user-approved provider settings.**

~~~
uv run oc verify --profile real-ai --static-dir web/dist
~~~

Never print keys or resume content. If config is absent, report it as an unrun optional integration check, not a pass.

- [ ] **Step 4: Obtain a fresh review.** Review canonical hashes, snapshot visibility, transaction/rollback/idempotency, pointer whitelist, soft delete, event semantics, API/type alignment, evidence wording, and 409 UI. Fix Critical/Important findings, rerun affected tests, then commit separately.

~~~
git add <only-reviewed-fix-files>
git commit -m "fix: AI address material proposal review"
~~~

## Completion criteria

- [ ] Every accepted change has a server-verified, visible evidence reference.
- [ ] Generation has no Resume/Material Kit write side effect.
- [ ] Acceptance creates exactly one non-master child Resume and never changes the source.
- [ ] Source drift yields 409 with zero partial writes.
- [ ] No Pilot tool, platform integration or automatic application behavior exists.
- [ ] Full local evidence, browser walk-through, real-AI status and independent review are reported honestly.
