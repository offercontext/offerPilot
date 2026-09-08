# 证据门控的面试复盘建议实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ] ) syntax for tracking. Execute inline in this worktree; do not dispatch subagents.

**Goal:** 将 InterviewNote 绑定到同投递的面试事件，并通过严格证据门控生成可审阅、不可变的 AI 复盘建议。

**Architecture:** 后端以可空 `InterviewNote.application_event_id` 和新增 `InterviewReviewProposal` 快照表承载绑定、历史和幂等；AI 模块只接收冻结复盘字段与最小事件元数据，严格校验逐字证据引用。前端沿用 ApplicationDetail/ReviewFormDrawer 原生复盘流程，新增结构化建议抽屉和历史只读查看；Pilot 只导航到该原生流程，不新增跨领域写入。

**Tech Stack:** Python 3、FastAPI、SQLAlchemy/SQLite、Pydantic、pytest、React/TypeScript、Ant Design、TanStack Query、Vitest、Codex 内置浏览器。

**Design source:** `docs/superpowers/specs/2026-07-22-evidence-gated-interview-review-proposal-design.md`

---

### Task 1: 增量数据库结构与模型

**Files:**
- Modify: `src/offerpilot/models.py:101-120, 333-361`
- Modify: `src/offerpilot/db.py:55-230`
- Modify: `src/offerpilot/schemas.py:80-105`
- Create: `tests/test_interview_review_migrations.py`

- [ ] **Step 1: Write the failing migration tests**

在 `tests/test_interview_review_migrations.py` 添加：

~~~python
def test_interview_review_schema_is_created_and_idempotent(tmp_path):
    first = init_database(tmp_path / "data.db")
    first.kw["bind"].dispose()

    second = init_database(tmp_path / "data.db")
    with second() as session:
        note_columns = {
            row[1] for row in session.execute(text("PRAGMA table_info(interview_notes)"))
        }
        proposal_columns = {
            row[1]
            for row in session.execute(text("PRAGMA table_info(interview_review_proposals)"))
        }
        indexes = {
            row[1] for row in session.execute(text("PRAGMA index_list(interview_notes)"))
        }
    second.kw["bind"].dispose()

    assert "application_event_id" in note_columns
    assert {
        "id", "note_id", "application_event_id", "idempotency_key",
        "input_snapshot_json", "source_fingerprint", "proposal_json",
        "proposal_hash", "created_at",
    } <= proposal_columns
    assert "idx_notes_event" in indexes
    assert "uq_interview_notes_event_main" in indexes
~~~

同时添加已有库升级测试：先建立只有旧 interview_notes 列的数据库并写入一条未绑定复盘，再调用 init_database，断言原复盘仍存在且新列为 NULL。

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_interview_review_migrations.py -q`

Expected: FAIL because application_event_id and interview_review_proposals do not exist.

- [ ] **Step 3: Add the model and migration**

在 InterviewNote 增加：

~~~python
application_event_id: Mapped[int | None] = mapped_column(
    ForeignKey("application_events.id", ondelete="SET NULL"),
    nullable=True,
)
~~~

不要把 idx_notes_event 放入 InterviewNote.__table_args__，因为旧库的 interview_notes 可能尚无 application_event_id，Base.metadata.create_all() 会尝试先建引用缺失列的索引。新增 InterviewReviewProposal，字段严格对应设计文档：note_id、可空快照 application_event_id、idempotency_key、input_snapshot_json、source_fingerprint、proposal_json、proposal_hash、created_at；增加 idx_interview_review_proposals_note、(note_id, idempotency_key) 唯一约束和 note_id ON DELETE CASCADE。

在 init_database() 中先调用 Base.metadata.create_all(engine)，让全新数据库一次创建带 application_event_id 的 interview_notes 和 proposal 表；随后对已存在且缺列的旧 interview_notes 调用 _ensure_column(engine, "interview_notes", "application_event_id", "INTEGER REFERENCES application_events(id) ON DELETE SET NULL")。最后用显式 SQL 创建 idx_notes_event 和部分唯一索引，确保两条路径都只在列存在后建索引：

~~~sql
CREATE INDEX IF NOT EXISTS idx_notes_event
ON interview_notes(application_event_id)
~~~

~~~sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_interview_notes_event_main
ON interview_notes(application_event_id)
WHERE application_event_id IS NOT NULL
~~~

记录未占用的唯一版本 0010_interview_review_proposals，重复启动不得重复报错或丢失数据。迁移测试必须断言 schema_migrations 中存在 0010_interview_review_proposals。为 API 响应增加 InterviewNoteOut.application_event_id，并新增 proposal 的嵌套输出类型，拒绝额外字段。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_interview_review_migrations.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add src/offerpilot/models.py src/offerpilot/db.py src/offerpilot/schemas.py tests/test_interview_review_migrations.py
git commit -m "feat: AI add interview review proposal schema"
~~~

### Task 2: 复盘绑定、更新字段所有权与软删除边界

**Files:**
- Modify: `src/offerpilot/repositories/notes.py`
- Modify: `src/offerpilot/api.py:630, 5720-5800`
- Modify: `tests/test_notes_api.py`
- Create: `tests/test_notes_repository_visibility.py`

- [ ] **Step 1: Write failing repository and API tests**

覆盖以下确定行为：

~~~python
def test_put_bound_note_preserves_application_and_event_when_fields_omitted(client):
    response = client.put(f"/api/notes/{note_id}", json={"questions": "新记录"})
    assert response.status_code == 200
    assert response.json()["application_id"] == app_id
    assert response.json()["application_event_id"] == event_id

def test_put_note_explicit_null_event_unbinds_without_deleting_note(client):
    response = client.put(
        f"/api/notes/{note_id}",
        json={"application_event_id": None, "questions": "保留复盘"},
    )
    assert response.status_code == 200
    assert response.json()["application_event_id"] is None
    assert response.json()["application_id"] == app_id

def test_bound_note_rejects_application_id_null_or_other_application(client):
    assert client.put(f"/api/notes/{note_id}", json={"application_id": None}).status_code == 422
    assert client.put(f"/api/notes/{note_id}", json={"application_id": other_app_id}).status_code == 422
~~~

再增加非 interview、跨投递、重复主复盘的 422/409 测试；Application 软删除后，GET /api/notes 不返回绑定复盘，application-scoped list/update/delete 均返回 404，独立复盘仍可读写。

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `uv run pytest tests/test_notes_api.py tests/test_notes_repository_visibility.py -q`

Expected: FAIL because the current update payload erases application ownership, the response removes application_id, and notes are not joined against visible Applications.

- [ ] **Step 3: Implement a presence-aware note update**

在 notes.py 引入 UNSET sentinel 和 NoteUpdate，使 application_id、application_event_id 能区分“省略”和显式 null。普通 PUT 对已绑定复盘省略 application id 时保留原值，显式 null/其他值返回稳定 422；对独立复盘仅允许保持 NULL。事件字段省略保留，显式 null 解绑，显式 ID 必须在同一事务中检查：

1. 事件存在且所属 Application 可见；
2. event_type == "interview"；
3. 事件 Application 与复盘 application_id 相同；
4. 事件未被另一条主复盘占用。

让 list/get/update/delete/delete_if_matches 对绑定复盘统一 join Application.deleted_at IS NULL；全局 list 只返回独立复盘和可见投递复盘。API 不再从响应剔除 application_id，并将关系错误映射为 404/422/409。事件删除继续依赖外键 SET NULL，不删除 note。

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `uv run pytest tests/test_notes_api.py tests/test_notes_repository_visibility.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add src/offerpilot/repositories/notes.py src/offerpilot/api.py tests/test_notes_api.py tests/test_notes_repository_visibility.py
git commit -m "fix: AI preserve interview note ownership"
~~~

### Task 3: 严格 AI 输入快照、输出契约与安全重试

**Files:**
- Create: `src/offerpilot/ai/interview_review_proposals.py`
- Create: `tests/test_interview_review_proposals_ai.py`

- [ ] **Step 1: Write failing contract tests**

在 tests/test_interview_review_proposals_ai.py 创建可控 fake model，覆盖：

- 合法输出通过；快照只含 note 的 company/position/round/date/questions/self_reflection/difficulty_points/mood 和事件的 id/application_id/event_type/subtype/round/scheduled_at/duration_minutes/status，不含 location、事件 notes、JD、Resume、Memory。
- /questions、/self_reflection、/difficulty_points、/mood 的逐字连续 excerpt 通过；伪造 excerpt、事件来源、空 excerpt 失败。
- 缺少 summary.evidence_refs 的普通摘要失败；精确等于 INTERVIEW_REVIEW_UNGROUNDED_SUMMARY_V1 才允许空引用。
- 非 allowlist 的无引用问题失败；三个 INTERVIEW_REVIEW_UNGROUNDED_QUESTIONS_V1 原文通过。
- observations/clarifications/practice_focuses/next_questions 分别第 11 项失败；任一 evidence_refs 第 6 项失败。
- fenced JSON、额外字段、重复键、NaN/Infinity/-Infinity 失败。
- 首次非法、第二次合法时 model 调用两次并成功；两次非法抛 InterviewReviewModelError；Provider 异常只调用一次。

- [ ] **Step 2: Run the AI tests to verify they fail**

Run: `uv run pytest tests/test_interview_review_proposals_ai.py -q`

Expected: FAIL because the module and validator do not exist.

- [ ] **Step 3: Implement the contract and generator**

在新模块定义版本化常量：

~~~python
INTERVIEW_REVIEW_UNGROUNDED_SUMMARY_V1 = (
    "本次复盘记录不足以形成有依据的表现判断，请先补充待澄清问题。"
)
INTERVIEW_REVIEW_UNGROUNDED_QUESTIONS_V1 = (
    "请补充本次面试中未记录的具体问题与回答。",
    "请补充你希望进一步澄清的内容。",
    "请补充你希望下次练习的具体场景。",
)
MAX_REVIEW_ITEMS = 10
MAX_EVIDENCE_REFS = 5
~~~

实现 build_interview_review_snapshot(note, event)、validate_interview_review(payload, snapshot) 和 generate_interview_review_proposal(model, snapshot)。校验必须拒绝顶层/对象额外字段、非字符串字段、非法 source/path、非空引用不逐字匹配，以及超出固定上限的数组。仅固定摘要和完整问题 allowlist 可无引用；所有观察、练习重点和上下文问题都必须满足证据规则。

系统 prompt 明确只使用冻结快照、禁止面试官评价/能力结论/外部事实，并要求 raw JSON。生成器最多两次调用：只有 JSON/结构/字段类型/证据格式失败才发一次 invalid_change_shape 修复提示；Provider 异常不重试。错误类别只保留安全分类，不记录原始模型输出。

- [ ] **Step 4: Run the AI tests to verify they pass**

Run: `uv run pytest tests/test_interview_review_proposals_ai.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add src/offerpilot/ai/interview_review_proposals.py tests/test_interview_review_proposals_ai.py
git commit -m "feat: AI add evidence-gated interview review contract"
~~~

### Task 4: 不可变建议仓储、历史来源状态与幂等生命周期

**Files:**
- Create: `src/offerpilot/repositories/interview_review_proposals.py`
- Create: `tests/test_interview_review_proposals_repository.py`

- [ ] **Step 1: Write failing repository tests**

使用 init_database 建立可见 Application、interview event 和绑定 note，添加以下测试：

- test_create_generated_freezes_snapshot_and_reuses_same_key：相同 (note_id, key) 返回同一 proposal，fake model 只调用一次，第二条不产生。
- test_create_generated_rechecks_fingerprint_before_insert：fake model 调用期间修改 note，抛来源冲突且 proposal 表为空。
- test_deleted_event_history_is_source_changed_but_generation_is_event_required：删除事件后历史 list/detail 仍返回快照和 source_changed；新生成抛 InterviewReviewEventRequired，不是 NotFound。
- test_non_null_missing_event_id_is_not_found：保留异常非空事件 ID 时抛 InterviewReviewNotFound。
- test_history_source_algorithm_does_not_require_current_event_when_id_changed：当前绑定为空或 ID 变化时直接 source_changed，不尝试重算。
- test_soft_deleted_application_hides_notes_and_proposals：Application 软删除后历史和生成均不可见。
- test_edit_unbind_and_rebind_do_not_overwrite_old_proposals：旧快照保留，来源变化，下一次确认必须用新 key。
- test_begin_immediate_serializes_visibility_check_and_insert：第二短事务以 BEGIN IMMEDIATE 检查 note/Application/event、幂等键、插入并提交。

- [ ] **Step 2: Run the repository tests to verify they fail**

Run: `uv run pytest tests/test_interview_review_proposals_repository.py -q`

Expected: FAIL because the repository and exceptions do not exist.

- [ ] **Step 3: Implement repository boundaries**

新增以下公开接口和异常：

~~~python
class InterviewReviewNotFound(Exception): ...
class InterviewReviewEventRequired(ValueError): ...
class InterviewReviewValidationError(ValueError): ...
class InterviewReviewConflictError(ValueError): ...

class InterviewReviewProposalsRepository:
    def list(self, note_id: int) -> list[InterviewReviewProposal]: ...
    def get(self, note_id: int, proposal_id: int) -> InterviewReviewProposal | None: ...
    def create_generated(
        self, note_id: int, idempotency_key: str, model: ChatModel
    ) -> tuple[InterviewReviewProposal, bool]: ...
~~~

冻结阶段使用短 session 读取可见 note/Application 和当前有效 interview event，构建快照后立即关闭 session。AI 调用期间不持有 SQLite 连接。写入阶段新建 session，执行 BEGIN IMMEDIATE，重新验证 note/Application 和事件关系；application_event_id is NULL 抛 InterviewReviewEventRequired，非空但不可读抛 InterviewReviewNotFound，指纹或绑定关系漂移抛 409 异常。相同 key 先返回既有记录，唯一约束处理并发重复插入。

历史读取只检查 note/Application 可见，再按设计文档第 4.2 节五步算法计算动态 source_status；事件被删除、不可见或绑定变化只标记 source_changed，不阻断历史快照。

- [ ] **Step 4: Run the repository tests to verify they pass**

Run: `uv run pytest tests/test_interview_review_proposals_repository.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add src/offerpilot/repositories/interview_review_proposals.py tests/test_interview_review_proposals_repository.py
git commit -m "feat: AI persist interview review proposals safely"
~~~

### Task 5: API 路由、错误码与安全响应

**Files:**
- Modify: `src/offerpilot/api.py:35-110, 620-650, 1570-1685`
- Modify: `src/offerpilot/schemas.py`
- Create: `tests/test_interview_review_proposals_api.py`

- [ ] **Step 1: Write failing API tests**

使用注入 fake ChatModel 的 create_app 覆盖：

- POST /api/notes/{note_id}/interview-review-proposals 合法返回 201，响应包含冻结 proposal、hash、fingerprint、source_status。
- 相同 key 第二次返回 200 且 fake model 只调用一次。
- GET list/detail 返回历史列表/快照；事件删除后 source_status=source_changed。
- 事件删除后 POST 返回 422、error_code=interview_review_event_required；note/Application 不可见返回 404、interview_review_not_found；异常非空事件 ID 才返回 404。
- 模型 Provider 异常和不可验证输出分别返回带稳定码的 502，且数据库无 proposal。
- 任何错误响应不包含复盘正文、事件 location、模型原文或 API Key。

- [ ] **Step 2: Run API tests to verify they fail**

Run: `uv run pytest tests/test_interview_review_proposals_api.py -q`

Expected: FAIL because the routes and serializers do not exist.

- [ ] **Step 3: Implement the routes**

在 create_app 注入 InterviewReviewProposalsRepository。新增：

~~~text
GET  /api/notes/{note_id}/interview-review-proposals
GET  /api/notes/{note_id}/interview-review-proposals/{proposal_id}
POST /api/notes/{note_id}/interview-review-proposals
~~~

POST 只接受 {"idempotency_key": "..."}，校验非空字符串；先解析模型配置，再调用 repository。异常映射固定为：

- note/Application 不可见：404 interview_review_not_found
- application_event_id is NULL：422 interview_review_event_required
- 来源漂移：409 interview_review_source_conflict
- Provider：502 interview_review_provider_error
- 严格校验：502 interview_review_unverifiable

日志只写安全类别和内部 ID。序列化 proposal 时动态附加 source_status，不把原始模型回复、事件 location 或未冻结字段返回给前端。

- [ ] **Step 4: Run API tests to verify they pass**

Run: `uv run pytest tests/test_interview_review_proposals_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add src/offerpilot/api.py src/offerpilot/schemas.py tests/test_interview_review_proposals_api.py
git commit -m "feat: AI expose interview review proposal API"
~~~

### Task 6: 前端复盘绑定和安全 API 客户端

**Files:**
- Modify: `web/src/types/note.ts`
- Modify: `web/src/services/notes.ts`
- Create: `web/src/types/interviewReviewProposal.ts`
- Create: `web/src/services/interviewReviewProposals.ts`
- Modify: `web/src/components/ReviewFormDrawer.tsx`
- Modify: `web/src/components/ApplicationDetail.tsx`
- Modify: `web/src/components/ReviewManagementView.tsx`
- Create: `web/src/components/ReviewFormDrawer.test.tsx`
- Create: `web/src/services/interviewReviewProposals.test.ts`

- [ ] **Step 1: Write failing frontend tests**

覆盖：

- 面试事件只能选择 event_type=interview；
- 已绑定复盘编辑时投递归属只读，更新请求省略 application_id 和 application_event_id；
- 显式解绑通过确认路径发送 application_event_id: null；
- proposal service 只按安全错误码/HTTP 状态抛出安全中文错误，不展示 Axios message 或服务端原文。

- [ ] **Step 2: Run frontend tests to verify they fail**

Run: `cd web; npm.cmd test -- --run src/components/ReviewFormDrawer.test.tsx src/services/interviewReviewProposals.test.ts`

Expected: FAIL because event binding fields, service and tests do not exist.

- [ ] **Step 3: Implement typed services and binding UI**

在 note.ts 增加 nullable application_event_id；把更新 payload 改为可省略字段。新增 proposal 类型，精确表达 summary、观察、问题、练习重点、evidence_refs、hash、source_status。

ReviewFormDrawer 加载 application-scoped events，过滤 interview 事件；新建复盘可绑定事件，编辑已绑定复盘不允许改变 application_id，事件解绑必须显式确认。ApplicationDetail 的事件卡片显示“记录复盘/查看复盘”，并把事件 ID 传入原生表单。独立复盘不显示生成建议入口。

interviewReviewProposals.ts 实现 list/detail/create；错误映射只允许以下固定中文提示：

~~~ts
const SAFE_ERRORS = {
  interview_review_event_required: '请先绑定有效的面试事件。',
  interview_review_not_found: '面试复盘已不可见，请重新打开投递。',
  interview_review_source_conflict: '复盘来源已变化，请重新核对后再生成。',
  interview_review_provider_error: 'AI 服务暂不可用，请稍后重试。',
  interview_review_unverifiable: 'AI 建议未通过证据校验，原复盘未受影响，请重试。',
};
~~~

未知错误使用统一中文兜底，不透传 response.data.error、Axios message 或 Error.message。

- [ ] **Step 4: Run frontend tests to verify they pass**

Run: `cd web; npm.cmd test -- --run src/components/ReviewFormDrawer.test.tsx src/services/interviewReviewProposals.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add web/src/types/note.ts web/src/services/notes.ts web/src/types/interviewReviewProposal.ts web/src/services/interviewReviewProposals.ts web/src/components/ReviewFormDrawer.tsx web/src/components/ApplicationDetail.tsx web/src/components/ReviewManagementView.tsx web/src/components/ReviewFormDrawer.test.tsx web/src/services/interviewReviewProposals.test.ts
git commit -m "feat: AI connect interview review proposal flow"
~~~

### Task 7: 结构化建议卡片、历史快照与只读边界

**Files:**
- Create: `web/src/components/InterviewReviewProposalDrawer.tsx`
- Create: `web/src/components/InterviewReviewProposalDrawer.test.tsx`
- Create: `web/src/components/InterviewReviewProposalDrawer.module.css`
- Modify: `web/src/components/ApplicationDetail.tsx`
- Modify: `web/src/components/ReviewManagementView.tsx`

- [ ] **Step 1: Write failing component tests**

在 InterviewReviewProposalDrawer.test.tsx 覆盖：

- 首次生成先出现“本次复盘内容与面试事件信息将发送给当前配置的 AI 服务”的确认弹窗，取消不调用 service；
- 历史列表按创建时间展示，点击某项才加载详情；
- source_changed 的旧建议只读显示“来源已变化”，不提供对旧建议的接受、编辑或跨领域写入动作；当当前 note 仍绑定有效 interview event 时，显示独立的、需确认的“重新生成复盘建议”入口并创建新 idempotency key；当事件已删除/绑定为空时禁用该入口并显示“请先绑定有效的面试事件。”；
- “用户记录”“AI 建议”“待澄清问题”“下次可追问”分区展示；
- evidence source 固定映射为“复盘问题/自我反思/困难点/情绪记录”，path/excerpt 保留原文；
- 生成返回 422/409/502 和未知错误时显示安全中文，不显示原始服务端文本；
- proposal 为空观察/练习时显示澄清问题，不创建事件、提醒、题库、Knowledge、Memory 或 Application 状态；
- 删除事件后历史仍能查看，但生成按钮禁用。

- [ ] **Step 2: Run component tests to verify they fail**

Run: `cd web; npm.cmd test -- --run src/components/InterviewReviewProposalDrawer.test.tsx`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement the drawer and integrate it**

组件只接收 noteId、eventId、open、onClose，通过 service 获取历史和详情；不复制 AI prompt，不渲染原始 JSON，不提供跨领域动作按钮。首次生成和 source_changed 后的重新生成都使用明确确认；重新生成必须创建新的 idempotency key，旧建议仍只读保留。结果未知保留同一个 idempotency key，显示“结果待确认/使用原尝试重试”，不自动重试。事件删除后由 API 的 422 映射为固定中文提示并禁用生成。

在 ApplicationDetail 的事件/复盘时间线增加“查看复盘/生成复盘建议”入口；ReviewManagementView 对有绑定事件的复盘提供同一原生抽屉。复盘正文、事件原文、公司/岗位名保持原文；固定 UI 文案全部使用中文。

- [ ] **Step 4: Run component tests to verify they pass**

Run: `cd web; npm.cmd test -- --run src/components/InterviewReviewProposalDrawer.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add web/src/components/InterviewReviewProposalDrawer.tsx web/src/components/InterviewReviewProposalDrawer.test.tsx web/src/components/InterviewReviewProposalDrawer.module.css web/src/components/ApplicationDetail.tsx web/src/components/ReviewManagementView.tsx
git commit -m "feat: AI add interview review proposal cards"
~~~

### Task 8: Pilot 仅导航到原生复盘流程

**Files:**
- Modify: `web/src/features/pilot/PilotOpportunityFitCard.tsx`
- Modify: `web/src/layout/AppShell.tsx`
- Modify: `web/src/components/ApplicationDetail.tsx`
- Create: `web/src/layout/AppShell.interviewReview.test.tsx`

- [ ] **Step 1: Write failing Pilot integration tests**

覆盖：

- 有 Application 上下文时 Pilot 卡片显示“打开面试复盘”；
- 点击后只打开 ApplicationDetail 的原生复盘入口，传递 applicationId，不直接调用 proposal API；
- 没有 Application 上下文时不显示该入口；
- 关闭/切换投递后上下文清理，不留下独立的复盘表单或跨领域 handoff；
- Pilot 入口不创建 Note、Event、Proposal、Question、Knowledge、Memory 或 Application 状态。

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web; npm.cmd test -- --run src/layout/AppShell.interviewReview.test.tsx`

Expected: FAIL because the Pilot callback and native review focus state do not exist.

- [ ] **Step 3: Implement the navigation-only integration**

在 AppShell 增加受控的 pilotInterviewReviewApplicationId/一次性 focus 状态，按 Application 上下文键控；Pilot 回调只调用现有 ApplicationDetail 打开逻辑并聚焦复盘表单/抽屉。ApplicationDetail 消费后立即清除 focus，不把任何 AI 输入或 proposal 对象交给 Pilot。不要新增工具调用、聊天消息、数据库表或跨领域写入。

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web; npm.cmd test -- --run src/layout/AppShell.interviewReview.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

~~~powershell
git add web/src/features/pilot/PilotOpportunityFitCard.tsx web/src/layout/AppShell.tsx web/src/components/ApplicationDetail.tsx web/src/layout/AppShell.interviewReview.test.tsx
git commit -m "feat: AI open interview review from Pilot"
~~~

### Task 9: 隔离真实 AI smoke 与浏览器闭环

**Files:**
- Modify: `src/offerpilot/smoke.py`
- Modify: `tests/test_smoke.py`
- Modify: `scripts/pilot-real-ai-browser-harness.ps1`

- [ ] **Step 1: Write failing smoke tests**

新增：

- test_real_ai_interview_review_smoke_allows_empty_evidence_questions：fake HTTP client 返回合法结构，断言允许空引用问题且不泄漏完整复盘/事件。
- test_real_ai_interview_review_smoke_isolates_and_cleans_note_event_proposal：sourceData 的 config 被复制到临时目录；合成 Application、Resume、interview event、note、proposal 只写入 temp；finally 清理 note/event/proposal/Application/Resume 后残留断言为零，sourceData 内容不变。
- test_local_profile_does_not_call_real_interview_review_provider：local profile 不执行新真实 AI 步骤。
- 每条原生命令失败都必须在 harness 中显式检查 $LASTEXITCODE 并 throw，finally 仍执行。

- [ ] **Step 2: Run smoke tests to verify they fail**

Run: `uv run pytest tests/test_smoke.py -q`

Expected: FAIL because no interview review smoke step、数据清理和 subprocess exit-code 检查存在。

- [ ] **Step 3: Implement the isolated real-AI path**

在 smoke.py 增加 real-ai 专用的合成数据创建、proposal API 调用和安全响应校验；允许模型生成无观察/练习但必须满足严格契约。扩展 scoped cleanup，按合成 application_id 删除 InterviewReviewProposal、InterviewNote、ApplicationEvent、关联 Application/Resume，并在删除后检查临时库无残留；不得通过全表清理触碰 sourceData。

在 pilot-real-ai-browser-harness.ps1 中复用临时数据目录和现有配置复制逻辑，启动隔离服务后浏览器从本地入口进入 ApplicationDetail，创建合成 interview event、保存复盘、确认发送 AI、生成并查看建议，检查证据卡片和无跨领域动作；删除事件后验证历史 source_changed 和生成 422，软删除 Application 后验证 404。每个 uv/npm/powershell 原生命令后立即检查 $LASTEXITCODE；只停止 harness 自己的进程树，最后执行 scoped cleanup 和残留断言。

- [ ] **Step 4: Run smoke tests to verify they pass**

Run: `uv run pytest tests/test_smoke.py -q`

Expected: PASS. Real provider browser run is executed separately only with configured real-ai profile and isolated data.

- [ ] **Step 5: Commit**

~~~powershell
git add src/offerpilot/smoke.py tests/test_smoke.py scripts/pilot-real-ai-browser-harness.ps1
git commit -m "test: AI add interview review real AI smoke"
~~~

### Task 10: 全量验证与交付门禁

**Files:**
- No new implementation files; update only tests discovered by the preceding tasks.

- [ ] **Step 1: Run backend focused tests**

~~~powershell
uv run pytest tests/test_interview_review_migrations.py tests/test_notes_api.py tests/test_notes_repository_visibility.py tests/test_interview_review_proposals_ai.py tests/test_interview_review_proposals_repository.py tests/test_interview_review_proposals_api.py tests/test_smoke.py -q
~~~

Expected: PASS.

- [ ] **Step 2: Run frontend focused tests and build**

~~~powershell
Set-Location web
npm.cmd test -- --run src/components/ReviewFormDrawer.test.tsx src/services/interviewReviewProposals.test.ts src/components/InterviewReviewProposalDrawer.test.tsx src/layout/AppShell.interviewReview.test.tsx
npm.cmd run build
Set-Location ..
~~~

Expected: selected tests pass and production build succeeds.

- [ ] **Step 3: Run repository quality gates**

~~~powershell
uv run pytest
uv run ruff check src tests
uv run mypy src
Set-Location web
npm.cmd test -- --run
npm.cmd run build
Set-Location ..
uv run oc smoke --static-dir web/dist
uv run oc verify --profile local --static-dir web/dist
uv run oc verify --profile real-ai --static-dir web/dist
~~~

Expected: 新增及受影响的专项测试、ruff、mypy、前端测试和构建通过。全量 uv run pytest 与既有 smoke/verify 命令照常执行并逐项记录退出码；当前分支已知 Windows symlink 权限和 Knowledge fixture hash 基线失败时，不得将其误报为本任务通过，也不得为了本功能改写这些基线。只有新增或受影响测试全绿且没有新增失败，才可交付复审。任何命令输出都不得包含 secrets、完整复盘、事件元数据或模型原文。

- [ ] **Step 4: Run the isolated browser acceptance**

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\pilot-real-ai-browser-harness.ps1
~~~

Expected: browser completes the native review flow in an isolated temporary data directory, confirms evidence-backed rendering, event deletion source_changed, deleted Application 404, no cross-domain writes, no recruiting-platform requests and zero residual synthetic rows.

- [ ] **Step 5: Record final verification and report**

Run `git diff --check` and `git status --short --branch`; expected output is no diff-check errors and a clean worktree. Any correction discovered during verification must be returned to its owning task, tested there, and committed with that task's conventional commit before this final step.

Final report must include changed files, no destructive schema behavior, remaining risks, exact test commands/results, final commit SHA and `git status --short --branch`. Never output API keys, complete notes, complete event data or raw model output.

---

## Plan self-review

- Schema/migration coverage: Task 1.
- Existing note binding, update presence semantics, cross-application checks, duplicate binding, event deletion and Application soft-delete visibility: Task 2.
- Frozen minimal input, strict JSON, versioned no-evidence constants, evidence excerpt validation, limits and one controlled retry: Task 3.
- Immutable snapshots, historical source_changed, source drift 409, SQLite transaction boundary and idempotency lifecycle: Task 4.
- Stable API contracts and safe errors: Task 5.
- Native event-bound form and Chinese-safe service errors: Task 6.
- Human-reviewed proposal cards, historical read-only behavior and no cross-domain actions: Task 7.
- Pilot navigation-only boundary: Task 8.
- Isolated real-AI browser and cleanup: Task 9.
- Full test, lint, type, build and deployment gates: Task 10.

No code, schema, API, frontend or smoke implementation is performed by this plan-writing step.
