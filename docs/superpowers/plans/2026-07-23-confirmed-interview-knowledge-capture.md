# 已确认面试知识沉淀实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execute inline in the current worktree; do not dispatch subagents.

**Goal:** 让用户从一条 InterviewNote 中选择原始面试片段，直接或通过可选 AI 预览生成带 Evidence 引用的可编辑内容，并在明确确认后原子写入 Captured Source、Evidence 与 Knowledge Note Version。

**Architecture:** 新增一个独立的 interview knowledge capture domain/repository，使用短 SQLite session 管理带幂等 key 的 capture attempt；AI 调用前提交并关闭 session，结果以 revision/token CAS 回写。确认写入在 `BEGIN IMMEDIATE` 中重新校验 note 指纹、UTF-16 片段和逐块 Evidence 引用，然后复用或创建 Knowledge Source/Snapshot/Evidence 并创建不可变 Note Version。前端只负责选择、审阅和确认，未确认 attempt 不进入 Knowledge 检索或练习输入。

**Tech Stack:** Python 3、FastAPI、SQLAlchemy、SQLite、Pydantic、pytest、React、TypeScript、Vitest、现有 KnowledgeRepository、现有 ChatModel `response_format` 能力。

**Design source:** `docs/superpowers/specs/2026-07-23-confirmed-interview-knowledge-capture-design.md`

---

## 执行约束

- 开发前运行 `git status --short --branch`，保持当前分支和 worktree，不新建 worktree；实现过程全部 inline，不派 subagent 编写代码。最终交付前必须执行一次独立 CR，独立审查者可以是专门的 review subagent，但只能审查和提出问题，不能代替实现。
- 每个任务按“先写失败测试 → 运行确认失败 → 最小实现 → 定向测试 → 小步提交”执行。
- 所有提交使用 `type: AI subject` 格式；本计划建议提交标题已给出。
- 不修改现有 Interview Review Proposal 的 API、数据语义或 HITL 行为；新能力只增加独立 capture API 和 Knowledge 只读展示。
- 确认后的 Attempt 允许因原 `InterviewNote` 物理删除而随 `note_id ON DELETE CASCADE` 删除，这是预期行为：确认后的幂等恢复不再可用；幂等与审计依靠不可级联的 `origin_note_id`、Source、Snapshot、Evidence 和 Note Version 保留。
- Snapshot 解析器必须严格读取 `text-bytes` 后的 `bytes=N` 个 UTF-8 bytes，再读取一个固定 LF；不得按换行或 Unicode 字符猜测边界。
- 客户端选择请求中的临时 `fragment_id` 只用于提交选择；服务端返回并生成 `fragment_001` 等 canonical ID。预览、确认、AI prompt 和 Evidence 引用只允许 canonical ID，旧客户端 ID 一律返回 422。

## 文件地图

| 文件 | 责任 |
| --- | --- |
| `src/offerpilot/models.py` | 新增 attempt、captured-source metadata、Knowledge Note/Version/引用模型；复用现有 Source/Snapshot/Evidence |
| `src/offerpilot/db.py` | 全新库建表、旧库兼容、`0011_confirmed_interview_knowledge_capture` 记录、索引和外键行为 |
| `src/offerpilot/knowledge/interview_capture.py` | UTF-16 坐标、片段 canonicalization、Snapshot byte serializer/parser、指纹和严格内容引用校验 |
| `src/offerpilot/repositories/interview_knowledge_capture.py` | attempt 生命周期、短 session/CAS、确认事务和 Knowledge 资产写入 |
| `src/offerpilot/ai/interview_knowledge_capture.py` | AI prompt、JSON Schema、严格解析、一次格式修复、安全空预览和安全诊断 |
| `src/offerpilot/schemas.py` | capture 请求、预览、确认、Knowledge Note/引用响应 schema |
| `src/offerpilot/api.py` | preview、confirm、Knowledge Note 只读入口与错误映射 |
| `tests/test_interview_knowledge_capture_migrations.py` | 全新库、旧库、Attempt cascade 与迁移幂等 |
| `tests/test_interview_knowledge_capture_fragments.py` | UTF-16、Unicode、上限、canonical ID 和 Snapshot parser |
| `tests/test_interview_knowledge_capture_repository.py` | attempt CAS、来源漂移、确认原子性、删除后审计 |
| `tests/test_interview_knowledge_capture_ai.py` | provider capability、严格 JSON、引用门禁、一次修复和安全空预览 |
| `tests/test_interview_knowledge_capture_api.py` | API 状态码、幂等、错误码、无知识资产副作用 |
| `web/src/types/interviewKnowledgeCapture.ts` | 前端请求/响应和 attempt 状态类型 |
| `web/src/services/interviewKnowledgeCapture.ts` | 新 API service 与安全中文错误映射 |
| `web/src/components/InterviewKnowledgeCaptureDrawer.tsx` | 原始片段选择、直接保存、可选 AI 预览、编辑和二次确认 |
| `web/src/components/InterviewKnowledgeCaptureDrawer.test.tsx` | 组件静态契约与固定安全文案 |
| `web/src/components/InterviewKnowledgeCaptureDrawer.interaction.test.tsx` | 选择、AI 确认、取消、错误、幂等恢复和提交边界 |
| `web/src/layout/AppShell.tsx` | 按 `noteId` 持有完整 capture draft，保证抽屉卸载/重进不丢失未知结果 key |
| `web/src/layout/AppShell.interviewKnowledgeCapture.test.tsx` | 真实卸载/重进、普通取消与结果未知的 draft 保留语义 |
| `web/src/components/ApplicationDetail.tsx` / `ReviewManagementView.tsx` | 接入复盘知识沉淀入口与已确认 Knowledge 查看入口 |
| `web/src/services/knowledge.ts` / `web/src/types/knowledge.ts` | 增加已确认 interview capture 的只读展示类型/读取方法 |
| `src/offerpilot/smoke.py` / `tests/test_smoke.py` | 隔离 real-AI 合成数据、浏览器闭环和清理断言 |
| `scripts/interview-knowledge-real-ai-browser-harness.ps1` | 临时数据目录、端口/进程归属、服务生命周期和失败传播 |

### Task 1: 迁移与 SQLAlchemy 模型

**Files:**
- Modify: `src/offerpilot/models.py`
- Modify: `src/offerpilot/db.py`
- Create: `tests/test_interview_knowledge_capture_migrations.py`

- [ ] **Step 1: 写失败迁移测试**

在 `tests/test_interview_knowledge_capture_migrations.py` 写两个路径：

```python
def test_fresh_database_creates_capture_schema_and_records_0011(tmp_path):
    factory = init_database(tmp_path / "data.db")
    with factory() as session:
        tables = {
            row[0]
            for row in session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        migrations = set(
            session.execute(text("SELECT version FROM schema_migrations")).scalars()
        )
    assert {
        "interview_knowledge_capture_attempts",
        "knowledge_captured_source_metadata",
        "knowledge_notes",
        "knowledge_note_versions",
        "knowledge_note_evidence",
    } <= tables
    assert "0011_confirmed_interview_knowledge_capture" in migrations


def test_existing_database_migration_preserves_note_and_is_idempotent(tmp_path):
    db_path = tmp_path / "legacy.db"
    create_legacy_interview_notes_database(db_path)
    first = init_database(db_path)
    second = init_database(db_path)
    with second() as session:
        note = session.execute(
            text("SELECT company, application_event_id FROM interview_notes WHERE id=1")
        ).one()
        migration_count = session.execute(
            text(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE version='0011_confirmed_interview_knowledge_capture'"
            )
        ).scalar_one()
    assert note.company == "legacy-company"
    assert note.application_event_id is None
    assert migration_count == 1
    first.kw["bind"].dispose()
    second.kw["bind"].dispose()
```

补充模型约束测试：`interview_knowledge_capture_attempts.note_id` 使用 `ON DELETE CASCADE`；`knowledge_captured_source_metadata.origin_note_id` 不是外键；`knowledge_note_evidence.block_id` 非空，唯一约束是 `(note_version_id, block_id, evidence_id)`，而不是 `(note_version_id, evidence_id)`。

- [ ] **Step 2: 运行迁移测试确认失败**

运行：

```powershell
uv run pytest tests/test_interview_knowledge_capture_migrations.py -q
```

预期：FAIL，新增表、`0011` 迁移和模型尚不存在。

- [ ] **Step 3: 添加模型并实现增量迁移**

在 `models.py` 中新增以下关系，字段名与后续 repository/API 保持一致：

```python
class InterviewKnowledgeCaptureAttempt(Base):
    __tablename__ = "interview_knowledge_capture_attempts"
    __table_args__ = (
        UniqueConstraint("note_id", "attempt_key", name="uq_interview_capture_attempt"),
        Index("idx_interview_capture_attempt_note", "note_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    note_id: Mapped[int] = mapped_column(
        ForeignKey("interview_notes.id", ondelete="CASCADE"), nullable=False
    )
    attempt_key: Mapped[str] = mapped_column(String, nullable=False)
    note_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    selected_fragments_json: Mapped[str] = mapped_column(Text, nullable=False)
    last_preview_mode: Mapped[str] = mapped_column(String, nullable=False)
    preview_status: Mapped[str] = mapped_column(String, nullable=False)
    preview_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_call_token: Mapped[str] = mapped_column(String, default="", server_default="")
    preview_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    preview_error_code: Mapped[str] = mapped_column(String, default="", server_default="")
    confirmed_note_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

同一批新增 `KnowledgeCapturedSourceMetadata`, `KnowledgeNote`, `KnowledgeNoteVersion`, `KnowledgeNoteEvidence`。`KnowledgeCapturedSourceMetadata.origin_note_id` 只用 `Integer`，不能声明 `ForeignKey`；`KnowledgeNoteEvidence.block_id` 为 `String(nullable=False)`，唯一约束为三元组。

在 `init_database()` 中严格执行：`Base.metadata.create_all(engine)` → 对已存在旧表执行 `_ensure_column()` → 创建需要依赖列的索引/约束 → `_record_migration(engine, "0011_confirmed_interview_knowledge_capture", "Add confirmed interview knowledge capture")`。不得在 `create_all()` 前对不存在的旧表执行 `ALTER TABLE`，不得复用 `0009` 或 `0010`。

- [ ] **Step 4: 运行迁移测试确认通过**

运行：

```powershell
uv run pytest tests/test_interview_knowledge_capture_migrations.py -q
```

预期：全部 PASS，且重复初始化不增加迁移记录、不删除旧 InterviewNote。

- [ ] **Step 5: 提交迁移切片**

```powershell
git add src/offerpilot/models.py src/offerpilot/db.py tests/test_interview_knowledge_capture_migrations.py
git commit -m "feat: AI add interview knowledge capture schema"
```

### Task 2: UTF-16 片段协议与 Snapshot canonicalization

**Files:**
- Create: `src/offerpilot/knowledge/interview_capture.py`
- Create: `tests/test_interview_knowledge_capture_fragments.py`

- [ ] **Step 1: 写失败的 Unicode、上限和 parser 测试**

测试必须覆盖：

```python
def test_utf16_offsets_match_browser_for_cjk_emoji_and_combining_text():
    value = "问题：Kafka 🚀 e\u0301"
    assert slice_utf16(value, 3, 8) == "Kafka"
    assert slice_utf16(value, 9, 11) == "🚀"
    assert slice_utf16(value, 12, 14) == "e\u0301"


def test_utf16_slice_rejects_surrogate_pair_boundary():
    value = "🚀"
    with pytest.raises(FragmentValidationError, match="surrogate"):
        slice_utf16(value, 1, 2)


def test_selected_fragments_reject_count_and_utf8_byte_limits():
    source_fields = {"/questions": "x" * 21}
    too_many = [fragment("/questions", i, i + 1, "x") for i in range(21)]
    with pytest.raises(FragmentValidationError, match="fragment_count"):
        canonicalize_fragments(too_many, source_fields)
    oversized_source = "x" * 4097
    oversized = [fragment("/questions", 0, 4097, oversized_source)]
    with pytest.raises(FragmentValidationError, match="utf8_bytes"):
        canonicalize_fragments(oversized, {"/questions": oversized_source})


def test_snapshot_round_trip_reads_exact_text_bytes_then_one_lf():
    fragments = canonicalize_fragments(
        [fragment("/questions", 0, 2, "问题"), fragment("/mood", 0, 2, "🚀")],
        {"/questions": "问题", "/mood": "🚀"},
    )
    encoded = serialize_capture_snapshot(fragments)
    assert parse_capture_snapshot(encoded) == fragments
    assert serialize_capture_snapshot(parse_capture_snapshot(encoded)) == encoded
```

再增加：无 NFC/NFD 变化、总和 32,769 UTF-8 bytes 拒绝、重复/重叠范围拒绝、客户端临时 ID 重排后 canonical ID 仍为 `fragment_001`、`text-bytes` 声明少/多一个 byte 或固定 LF 缺失时拒绝。

- [ ] **Step 2: 运行测试确认失败**

运行：

```powershell
uv run pytest tests/test_interview_knowledge_capture_fragments.py -q
```

预期：FAIL，因为 canonicalization、UTF-16 slicing 和 snapshot parser 尚不存在。

- [ ] **Step 3: 实现纯函数协议**

公开给 repository 和 AI validator 的纯函数固定为：

```python
from collections.abc import Mapping

def slice_utf16(value: str, start: int, end: int) -> str:
    pass

def canonicalize_fragments(
    raw: list[SelectedFragment], source_fields: Mapping[str, str]
) -> list[CanonicalFragment]:
    pass

def serialize_capture_snapshot(fragments: list[CanonicalFragment]) -> bytes:
    pass

def parse_capture_snapshot(payload: bytes) -> list[CanonicalFragment]:
    pass

def note_fingerprint(note: InterviewNote) -> str:
    pass
def validate_canonical_fragment_refs(
    refs: list[EvidenceRef], fragments: list[CanonicalFragment]
) -> None:
    pass
```

`slice_utf16()` 使用 UTF-16LE code-unit 视图；范围端点不能切开 surrogate pair。`canonicalize_fragments()` 只接受四个固定 path，按 path 顺序和 UTF-16 `start/end` 排序，分配 canonical IDs，校验每个片段与当前原文逐字相等。serializer 在 `text-bytes` 后严格读取 `bytes=N` 个 UTF-8 bytes，再读取一个固定 LF；任何额外 byte、缺失 LF、非法 UTF-8 或字段顺序变化均拒绝。所有哈希均使用 serializer 的 bytes，不使用平台换行或隐式 Unicode normalization。

- [ ] **Step 4: 运行测试确认通过**

```powershell
uv run pytest tests/test_interview_knowledge_capture_fragments.py -q
```

预期：全部 PASS。

- [ ] **Step 5: 提交片段协议切片**

```powershell
git add src/offerpilot/knowledge/interview_capture.py tests/test_interview_knowledge_capture_fragments.py
git commit -m "feat: AI add interview capture fragment protocol"
```

### Task 3: Attempt repository、短 session 与 CAS

**Files:**
- Create: `src/offerpilot/repositories/interview_knowledge_capture.py`
- Create: `tests/test_interview_knowledge_capture_repository.py`

- [ ] **Step 1: 写失败的 attempt/CAS 测试**

覆盖以下确定行为。测试模块先定义 `tracked_session_factory(db_path)`，每个 factory 都创建独立 SQLAlchemy engine/SQLite connection，并通过 `open_sessions` 计数；再定义带 `started`/`release` Event 的 `BlockingProvider.complete(fragments)`，Provider 每次进入递增 `calls`，并记录进入时两个 tracker 的 open session 总数。这样可以证明 claim 提交并关闭 session 后才调用 Provider，而不是只测试顺序调用：

```python
def test_same_key_can_switch_from_direct_to_ai_without_new_key(repository):
    direct = repository.prepare_preview(note_id, key, "direct", fragments)
    ai = repository.claim_ai_preview(note_id, key, fragments)
    assert direct.attempt_key == ai.attempt_key == key
    assert ai.preview_status == "ai_generating"


def test_concurrent_ai_claim_allows_one_provider_call_across_two_sqlite_sessions(tmp_path):
    db_path = tmp_path / "capture.db"
    factory_a, tracker_a = tracked_session_factory(db_path)
    factory_b, tracker_b = tracked_session_factory(db_path)
    repository_a = InterviewKnowledgeCaptureRepository(factory_a)
    repository_b = InterviewKnowledgeCaptureRepository(factory_b)
    provider = BlockingProvider()

    def invoke(repository):
        claim = repository.claim_ai_preview(note_id, key, fragments)
        if not claim.should_call_provider:
            return claim
        preview = provider.complete(fragments)
        return repository.complete_ai_preview(
            note_id, key, claim.preview_revision, claim.provider_call_token, preview
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(invoke, repository_a)
        assert provider.started.wait(timeout=5)
        second_future = pool.submit(invoke, repository_b)
        second = second_future.result(timeout=5)
        provider.release.set()
        first = first_future.result(timeout=5)

    assert provider.calls == 1
    assert provider.open_sessions_seen == [0]
    assert second.status == "ai_generating"
    assert first is True
    assert tracker_a.open_sessions == 0
    assert tracker_b.open_sessions == 0
    factory_a.kw["bind"].dispose()
    factory_b.kw["bind"].dispose()


def test_stale_provider_result_fails_revision_token_cas(repository):
    claim = repository.claim_ai_preview(note_id, key, fragments)
    repository.mark_provider_unknown(note_id, key, claim.preview_revision, claim.provider_call_token)
    assert not repository.complete_ai_preview(
        note_id, key, claim.preview_revision, claim.provider_call_token, valid_preview
    )
```

再覆盖：同 key 不同指纹返回 attempt conflict；AI 调用前 session 已关闭的 fake model 交互；provider unknown 同 key 可重新 claim 下一 revision；确定 404/422/409 后 key 可清理；原 note 删除级联删除 attempt 但不删除 metadata/Source/Snapshot/Evidence/Version。

另加 `discard_unconfirmed_attempt()` 回归：未确认 attempt 可删除，重复删除返回相同成功结果且不产生 Knowledge 资产；已确认 attempt 不可删除并返回稳定冲突，不得影响已确认资产。

- [ ] **Step 2: 运行测试确认失败**

```powershell
uv run pytest tests/test_interview_knowledge_capture_repository.py -q
```

预期：FAIL，因为 repository 和状态转换尚不存在。

- [ ] **Step 3: 实现 attempt 状态转换**

实现最小接口：

```python
prepare_preview(note_id, attempt_key, mode, raw_fragments) -> CaptureAttemptView
claim_ai_preview(note_id, attempt_key, canonical_fragments) -> AiPreviewClaim
complete_ai_preview(note_id, attempt_key, revision, provider_call_token, preview) -> bool
mark_provider_unknown(note_id, attempt_key, revision, provider_call_token) -> bool
get_attempt(note_id, attempt_key) -> CaptureAttemptView | None
discard_unconfirmed_attempt(note_id, attempt_key) -> None
```

`prepare_preview()` 只在短 session 中创建/校验 attempt；direct 直接写 `direct_ready`，ai 只申请 claim。`claim_ai_preview()` 在事务内检查当前没有 `ai_generating`，递增 revision、写 token、提交并关闭 session；它返回后才允许调用 ChatModel。`complete_ai_preview()` 与 `mark_provider_unknown()` 使用 revision/token/status 条件 UPDATE，rowcount 必须为 1 才算成功。CAS 失败不保存原始模型响应。

- [ ] **Step 4: 运行测试确认通过**

```powershell
uv run pytest tests/test_interview_knowledge_capture_repository.py -q
```

预期：全部 PASS，并证明同 key direct→ai、并发互斥和 stale response 丢弃成立。

- [ ] **Step 5: 提交 attempt 切片**

```powershell
git add src/offerpilot/repositories/interview_knowledge_capture.py tests/test_interview_knowledge_capture_repository.py
git commit -m "feat: AI add interview capture attempt CAS"
```

### Task 4: 严格 AI 预览与安全空结果

**Files:**
- Create: `src/offerpilot/ai/interview_knowledge_capture.py`
- Create: `tests/test_interview_knowledge_capture_ai.py`
- Modify: `tests/test_ai_client.py` only if compatibility coverage is missing

- [ ] **Step 1: 写失败的 AI contract 测试**

先在 `tests/test_ai_client.py` 固化 `ChatModel.complete(response_format=None)` 的兼容契约：旧调用不传参数仍成功；`supports_json_schema=True` 的 capture provider 才收到 response format；false/未配置 provider 不收到未知参数。已有实现满足时只补回归测试，不改现有 HTTP 或数据库契约。

测试 fake `ChatModel` 收到的输入只包含服务端 canonical `fragment_001` 等 ID 和选中原文，不含完整 note、JD、Resume、location 或复盘建议；同时覆盖：

```python
def test_valid_preview_accepts_only_canonical_fragment_ids(fake_model):
    assert generate_preview(fake_model, canonical_fragments) == valid_preview

def test_old_client_fragment_id_is_rejected(fake_model):
    with pytest.raises(PreviewValidationError, match="canonical_fragment_id"):
        validate_preview(old_client_preview, canonical_fragments)

def test_fenced_json_extra_field_non_finite_and_bad_excerpt_are_rejected(fake_model):
    for invalid in (fenced_json, extra_field_json, non_finite_json, bad_excerpt_json):
        with pytest.raises(PreviewValidationError):
            parse_and_validate_preview(invalid, canonical_fragments)

def test_invalid_first_response_repairs_once_with_machine_failure_category(fake_model):
    result = generate_preview(fake_model, canonical_fragments)
    assert fake_model.call_count == 2
    assert fake_model.repair_categories == ["missing_evidence_ref"]
    assert result == valid_preview

def test_provider_error_is_not_retried_and_preserves_attempt_key(fake_model):
    with pytest.raises(PreviewProviderError):
        generate_preview(fake_model, canonical_fragments)
    assert fake_model.call_count == 1

def test_two_contract_failures_return_validated_safe_empty_preview(fake_model):
    assert generate_preview(fake_model, canonical_fragments) == SAFE_EMPTY_PREVIEW
    assert fake_model.call_count == 2
```

断言模型请求在 `supports_json_schema=True` 时含 `response_format`，未声明或为 false 时不含未知参数；断言诊断只包含类别、修复次数、耗时和 request ID，不含模型原文或快照。

- [ ] **Step 2: 运行测试确认失败**

```powershell
uv run pytest tests/test_interview_knowledge_capture_ai.py tests/test_ai_client.py -q
```

预期：新 AI 模块、schema 和验证器尚不存在；既有 ChatModel 兼容测试必须保持通过。

- [ ] **Step 3: 实现 prompt、schema 和一次修复**

在 `interview_knowledge_capture.py` 中固定：

```python
PREVIEW_SCHEMA_VERSION = 1
SAFE_EMPTY_PREVIEW = {"title": "", "blocks": []}
MAX_TITLE_CHARS = 120
MAX_BLOCKS = 20
MAX_BLOCK_TEXT_CHARS = 2000
MAX_EVIDENCE_REFS_PER_BLOCK = 5
```

解析顺序必须是：strict JSON → exact fields/types/limits → canonical fragment ID → excerpt exact match → per-block evidence coverage。失败只生成机器类别并最多发一次修复请求；修复仍只使用冻结 canonical fragments。两次仍失败时服务端自行构造并再次通过同一 validator 的 `SAFE_EMPTY_PREVIEW`。Provider/网络异常直接向 API 层报告 502，保留 attempt。

- [ ] **Step 4: 运行测试确认通过**

```powershell
uv run pytest tests/test_interview_knowledge_capture_ai.py tests/test_ai_client.py -q
```

预期：全部 PASS；非法模型内容不会成为 Source、Evidence 或 Note Version。

- [ ] **Step 5: 提交 AI 切片**

```powershell
git add src/offerpilot/ai/interview_knowledge_capture.py tests/test_interview_knowledge_capture_ai.py tests/test_ai_client.py
git commit -m "feat: AI add interview knowledge preview contract"
```

### Task 5: 确认事务、API 与历史审计

**Files:**
- Modify: `src/offerpilot/repositories/interview_knowledge_capture.py`
- Modify: `src/offerpilot/schemas.py`
- Modify: `src/offerpilot/api.py`
- Create: `tests/test_interview_knowledge_capture_api.py`
- Modify: `tests/test_notes_api.py` for visibility/deletion regression if shared fixture is required

- [ ] **Step 1: 写失败的 API/事务测试**

覆盖：

```python
def test_direct_preview_has_no_knowledge_rows_before_confirm(client, db):
    preview = create_direct_preview(client, note_id, selected_fragments)
    assert preview.status_code == 200
    assert knowledge_counts(db) == zero_knowledge_counts()

def test_confirm_creates_source_snapshot_evidence_and_version_atomically(client, db):
    attempt = create_direct_preview(client, note_id, selected_fragments).json()
    response = confirm_capture(client, note_id, attempt, direct_content(attempt))
    assert response.status_code == 201
    assert knowledge_counts(db) == expected_confirmed_counts()

def test_confirm_requires_every_final_block_to_have_evidence(client, db):
    attempt = create_direct_preview(client, note_id, selected_fragments).json()
    response = confirm_capture(client, note_id, attempt, content_with_uncited_block(attempt))
    assert response.status_code == 422
    assert knowledge_counts(db) == zero_knowledge_counts()

def test_same_evidence_can_be_reused_by_multiple_blocks(client, db):
    attempt = create_direct_preview(client, note_id, selected_fragments).json()
    response = confirm_capture(client, note_id, attempt, two_blocks_same_evidence(attempt))
    assert response.status_code == 201
    assert count_note_evidence_links(db) == 2

def test_note_edit_before_confirm_returns_409_without_new_rows(client, db):
    attempt = create_direct_preview(client, note_id, selected_fragments).json()
    edit_note(client, note_id, questions="changed")
    response = confirm_capture(client, note_id, attempt, direct_content(attempt))
    assert response.status_code == 409
    assert knowledge_counts(db) == zero_knowledge_counts()

def test_deleted_note_keeps_confirmed_assets_but_not_confirmed_attempt(client, db):
    attempt = create_and_confirm_capture(client, note_id)
    delete_note(client, note_id)
    assert fetch_confirmed_knowledge(client, attempt.version_id).status_code == 200
    assert fetch_attempt(client, note_id, attempt.key).status_code == 404

def test_confirm_rejects_client_fragment_id_and_accepts_server_canonical_id(client, db):
    attempt = create_direct_preview(client, note_id, selected_fragments).json()
    assert confirm_capture(client, note_id, attempt, content_with_client_id(attempt)).status_code == 422
    assert confirm_capture(client, note_id, attempt, direct_content(attempt)).status_code == 201

def test_idempotent_confirm_returns_same_version(client, db):
    attempt = create_direct_preview(client, note_id, selected_fragments).json()
    first = confirm_capture(client, note_id, attempt, direct_content(attempt))
    second = confirm_capture(client, note_id, attempt, direct_content(attempt))
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["version_id"] == first.json()["version_id"]

def test_delete_unconfirmed_attempt_is_idempotent_and_creates_no_knowledge(client, db):
    attempt = create_direct_preview(client, note_id, selected_fragments).json()
    first = delete_capture_attempt(client, note_id, attempt["attempt_key"])
    second = delete_capture_attempt(client, note_id, attempt["attempt_key"])
    assert first.status_code == 204
    assert second.status_code == 204
    assert fetch_attempt(client, note_id, attempt["attempt_key"]).status_code == 404
    assert knowledge_counts(db) == zero_knowledge_counts()

def test_delete_confirmed_attempt_returns_conflict_and_keeps_frozen_assets(client, db):
    attempt = create_and_confirm_capture(client, note_id)
    response = delete_capture_attempt(client, note_id, attempt.key)
    assert response.status_code == 409
    assert response.json()["error_code"] == "capture_attempt_confirmed"
    assert fetch_confirmed_knowledge(client, attempt.version_id).status_code == 200
```

确认前查询 `knowledge_sources`, `knowledge_evidence`, `knowledge_notes`, `knowledge_note_versions`, `knowledge_note_evidence` 均不得出现新资产。原 note 删除后用 Knowledge 只读 API 读取 `origin_note_id`、Snapshot、Evidence 和 Version；再次用原 key 查询 attempt 返回 404，不创建新版本。

- [ ] **Step 2: 运行测试确认失败**

```powershell
uv run pytest tests/test_interview_knowledge_capture_api.py -q
```

预期：FAIL，因为新路由、确认事务和 Knowledge Note 只读响应尚不存在。

- [ ] **Step 3: 实现确认事务与安全错误映射**

在 repository 的 `confirm()` 中按以下顺序执行并在同一个 `BEGIN IMMEDIATE` 中提交：

1. 按 `(note_id, attempt_key)` 查已确认版本；命中即返回相同版本，不解析 Provider；
2. 检查 note 可见、attempt 未过期、指纹一致；
3. 用 `parse_capture_snapshot()` 和 `slice_utf16()` 校验当前 note 的每个片段；
4. 校验最终 content block ID 集合与 `knowledge_note_evidence` link 集合完全一致，每个 block 至少一条 Evidence；只接受服务器 canonical `fragment_001` 等 ID；
5. 创建或复用 `source_hash` 对应的 captured Source/Snapshot/Evidence，并写不可级联 `origin_note_id`；
6. 创建 Note/Version/每条 `(version_id, block_id, evidence_id)` link，更新 current version，标记 attempt confirmed；
7. 提交，任何异常全部回滚。

API 新增：

```text
POST /api/notes/{note_id}/knowledge-capture/preview
POST /api/notes/{note_id}/knowledge-capture/confirm
DELETE /api/notes/{note_id}/knowledge-capture/attempts/{attempt_key}
GET  /api/knowledge/notes
GET  /api/knowledge/notes/{knowledge_note_id}
```

DELETE 仅删除未确认 attempt：note 不可见返回 404；attempt 不存在或已删除时仍返回 204（幂等）；attempt 已确认时返回稳定 `capture_attempt_confirmed` 409，不删除任何冻结资产。普通取消/关闭必须先调用该 DELETE，收到 204 后再清除 AppShell draft；若请求结果未知，不得把它当作已删除。

其他接口只返回稳定 `error_code`：404 note 不可见、409 source changed/attempt conflict、410 expired、422 selection invalid、502 provider unknown。前端不可见后端原始 error、Axios message 或 Provider 原文。

- [ ] **Step 4: 运行 API 测试确认通过**

```powershell
uv run pytest tests/test_interview_knowledge_capture_api.py tests/test_notes_api.py -q
```

预期：全部 PASS；确认是唯一创建 Knowledge 资产的入口，原始 note 编辑/解绑/删除不被反向修改。

- [ ] **Step 5: 提交后端切片**

```powershell
git add src/offerpilot/repositories/interview_knowledge_capture.py src/offerpilot/schemas.py src/offerpilot/api.py tests/test_interview_knowledge_capture_api.py tests/test_notes_api.py
git commit -m "feat: AI add confirmed interview knowledge capture API"
```

### Task 6: 前端选择、预览与二次确认

**Files:**
- Create: `web/src/types/interviewKnowledgeCapture.ts`
- Create: `web/src/services/interviewKnowledgeCapture.ts`
- Create: `web/src/services/interviewKnowledgeCapture.test.ts`
- Create: `web/src/components/InterviewKnowledgeCaptureDrawer.tsx`
- Create: `web/src/components/InterviewKnowledgeCaptureDrawer.test.tsx`
- Create: `web/src/components/InterviewKnowledgeCaptureDrawer.interaction.test.tsx`
- Modify: `web/src/layout/AppShell.tsx`
- Create: `web/src/layout/AppShell.interviewKnowledgeCapture.test.tsx`
- Modify: `web/src/components/ApplicationDetail.tsx`
- Modify: `web/src/components/ReviewManagementView.tsx`

- [ ] **Step 1: 写失败的 service 与组件测试**

测试必须断言：

```tsx
it('direct save does not call the AI preview endpoint', async () => {
  await renderAndClickDirectSave();
  expect(mockAiPreview).not.toHaveBeenCalled();
});
it('requires confirmation before AI preview and before knowledge save', async () => {
  await renderAndRequestAiPreview();
  expect(mockAiPreview).not.toHaveBeenCalled();
  await confirmAiDisclosure();
  expect(mockAiPreview).toHaveBeenCalledTimes(1);
  expect(mockConfirm).not.toHaveBeenCalled();
});
it('renders only selected raw fields and canonical evidence ids', async () => {
  await renderWithCanonicalPreview();
  expect(screen.getByText('fragment_001')).toBeInTheDocument();
  expect(screen.queryByText('AI summary')).not.toBeInTheDocument();
});
it('AI 502 keeps direct-save available and shows safe Chinese copy', async () => {
  mockAiPreview.mockRejectedValueOnce(new InterviewKnowledgeCaptureError('provider_unknown'));
  await renderAndConfirmAiDisclosure();
  expect(screen.getByRole('button', { name: '直接保存选中原文' })).toBeEnabled();
  expect(screen.getByText('AI 预览暂不可用，可直接保存选中原文')).toBeInTheDocument();
});
it('ordinary cancel deletes the unconfirmed server attempt before clearing the draft', async () => {
  await renderWithDirectPreview();
  await clickCancel();
  expect(mockDeleteAttempt).toHaveBeenCalledWith(noteId, attemptKey);
  expect(readDraft(noteId)).toBeUndefined();
  expect(mockConfirm).not.toHaveBeenCalled();
});
it('unknown-result close keeps the server attempt and draft for same-key recovery', async () => {
  await renderWithUnknownResultAndReadAttemptKey();
  await clickClose();
  expect(mockDeleteAttempt).not.toHaveBeenCalled();
  await remountAndReadAttemptKey();
  expect(readAttemptKey()).toBe(attemptKey);
});
it('safe empty preview is normal empty state, not an error', async () => {
  await renderWithSafeEmptyPreview();
  expect(screen.getByText('暂无可验证的笔记预览')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
it('409 source change disables confirm without writing', async () => {
  await renderWithSourceConflict();
  expect(mockConfirm).not.toHaveBeenCalled();
  expect(screen.getByText('复盘内容已变化，请重新选择原始片段')).toBeInTheDocument();
});
it('unknown result remount reuses the same attempt key', async () => {
  const firstKey = await renderWithUnknownResultAndReadAttemptKey();
  const secondKey = await remountAndReadAttemptKey();
  expect(secondKey).toBe(firstKey);
});
```

服务测试断言请求中的 `selected_fragments` 使用临时 client ID 仅提交一次；收到服务端响应后，后续 preview/confirm/evidence refs 全部使用 `fragment_001` 等 canonical ID；提交旧 client ID 返回的错误必须显示中文并禁用确认。

- [ ] **Step 2: 运行前端测试确认失败**

```powershell
Set-Location web
npm.cmd test -- --run src/services/interviewKnowledgeCapture.test.ts src/components/InterviewKnowledgeCaptureDrawer.interaction.test.tsx src/layout/AppShell.interviewKnowledgeCapture.test.tsx
Set-Location ..
```

预期：FAIL，因为 service、组件和入口尚不存在。

- [ ] **Step 3: 实现 AppShell 持久草稿 reducer 与受控 Drawer**

`InterviewKnowledgeCaptureDrawer` 不得在自身 `useState` 中拥有幂等 key、canonical fragments 或结果状态。`AppShell` 使用按 `noteId` 键控的 reducer 保存完整 draft：

```tsx
type InterviewKnowledgeCaptureDraft = {
  selectedFragments: SelectedFragment[];
  canonicalFragments: CanonicalFragment[];
  attemptKey: string;
  previewStatus: 'not_requested' | 'direct_ready' | 'ai_generating' | 'ai_ready' | 'safe_empty' | 'provider_unknown';
  preview: KnowledgePreview | null;
  editedBlocks: PreviewBlock[];
  errorCode: string | null;
};

type CaptureDraftAction =
  | { type: 'upsert'; noteId: number; patch: Partial<InterviewKnowledgeCaptureDraft> }
  | { type: 'mark_unknown'; noteId: number }
  | { type: 'cancel'; noteId: number }
  | { type: 'dismiss_unknown'; noteId: number }
  | { type: 'confirmed'; noteId: number }
  | { type: 'source_changed'; noteId: number };
```

`AppShell` 只在 `cancel`、确定 404/409/422、确认成功或来源变化时删除 key；普通取消/关闭先调用 `DELETE /api/notes/{note_id}/knowledge-capture/attempts/{attempt_key}`，仅在收到确定 204（包括服务端已不存在）后 dispatch `cancel` 并删除对应 note draft。若请求已发出但响应超时、断网或丢失，必须转为 `provider_unknown`，关闭只 dispatch `dismiss_unknown`、不调用 DELETE，并保留完整 draft/key。结果未知只把状态标记为 `provider_unknown` 并保留全部 draft；重新挂载按同一 `noteId` 读取原 draft，并用原 `attemptKey` 恢复。Drawer 通过 `draft` 和 `onDraftChange` 受控渲染，卸载不触发清理。

服务端返回 `ai_generating` 时不得发第二个 Provider 请求；确认按钮仅在每个 block 有 canonical Evidence ref、内容未超限且用户完成二次确认后调用 confirm。

`web/src/services/interviewKnowledgeCapture.ts` 必须提供 `deleteUnconfirmedAttempt(noteId, attemptKey)`，只对该 note/key 调用上述 DELETE，并把 204/不存在统一视为确定删除；超时、断网、普通 5xx 或响应丢失不得转换成成功删除，调用方必须保留 `provider_unknown` 草稿。

在 `web/src/layout/AppShell.interviewKnowledgeCapture.test.tsx` 使用真实 `render` → `unmount` → `render` 流程断言：

```tsx
it('keeps unknown-result draft and key across drawer unmount and remount', async () => {
  const firstKey = await openDrawerSubmitAndLoseResponse(noteId);
  closeDrawerByUnmount();
  await reopenDrawer(noteId);
  expect(readAttemptKey()).toBe(firstKey);
  await retryWithSameKey();
  expect(mockPreview).toHaveBeenLastCalledWith(expect.objectContaining({ attempt_key: firstKey }));
});

it('clears ordinary-cancel draft but preserves unknown-result draft', async () => {
  await openDrawerAndCancel(noteId);
  expect(mockDeleteAttempt).toHaveBeenCalledTimes(1);
  expect(readDraft(noteId)).toBeUndefined();
  await openDrawerSubmitAndLoseResponse(noteId);
  await cancelAfterUnknownResult(noteId);
  expect(mockDeleteAttempt).toHaveBeenCalledTimes(1);
  expect(readDraft(noteId)?.attemptKey).toBeDefined();
});
```

固定 UI 行为：默认“直接保存选中原文”；AI 预览前明确说明发送给当前 Provider；安全空结果显示“暂无可验证的笔记预览”；502 显示“AI 预览暂不可用，可直接保存选中原文”；不显示原始 Axios/Provider 文本，不提供练习、Memory、能力判断或投递动作。

- [ ] **Step 4: 运行前端测试确认通过**

```powershell
Set-Location web
npm.cmd test -- --run src/services/interviewKnowledgeCapture.test.ts src/components/InterviewKnowledgeCaptureDrawer.interaction.test.tsx src/layout/AppShell.interviewKnowledgeCapture.test.tsx
Set-Location ..
```

预期：全部 PASS。

- [ ] **Step 5: 提交前端切片**

```powershell
git add web/src/types/interviewKnowledgeCapture.ts web/src/services/interviewKnowledgeCapture.ts web/src/services/interviewKnowledgeCapture.test.ts web/src/components/InterviewKnowledgeCaptureDrawer.tsx web/src/components/InterviewKnowledgeCaptureDrawer.test.tsx web/src/components/InterviewKnowledgeCaptureDrawer.interaction.test.tsx web/src/layout/AppShell.tsx web/src/layout/AppShell.interviewKnowledgeCapture.test.tsx web/src/components/ApplicationDetail.tsx web/src/components/ReviewManagementView.tsx
git commit -m "feat: AI add interview knowledge capture review flow"
```

### Task 7: Knowledge 工作台只读入口

**Files:**
- Modify: `web/src/types/knowledge.ts`
- Modify: `web/src/services/knowledge.ts`
- Modify: `web/src/components/KnowledgeSourcesView.tsx`
- Modify: `web/src/components/KnowledgeSourcesView.test.tsx`
- Create: `web/src/components/KnowledgeSourcesView.interviewCapture.test.tsx`

- [ ] **Step 1: 写失败的只读入口测试**

断言已确认 capture 显示 `origin_kind=confirmed_interview_capture`、Note Version 内容、每个 block 的 Evidence 原文/路径/冻结时间；未确认 attempt 不在列表；原 note 删除后历史仍可读；页面没有自动练习、Memory、能力画像或投递按钮。

- [ ] **Step 2: 运行测试确认失败**

```powershell
Set-Location web
npm.cmd test -- --run src/components/KnowledgeSourcesView.interviewCapture.test.tsx src/components/KnowledgeSourcesView.test.tsx
Set-Location ..
```

预期：FAIL，因为新增来源类型和 Note Version 展示尚未接入。

- [ ] **Step 3: 实现只读 Knowledge 读取**

只读 API 直接读取已确认 Source/Snapshot/Evidence/Note Version；不查询 `interview_knowledge_capture_attempts`，不触发 Ingest、Brief、Exercise、Memory 或任何写 API。动态用户原文和 Evidence 摘录保持原文，固定来源标签使用中文。

- [ ] **Step 4: 运行测试确认通过**

```powershell
Set-Location web
npm.cmd test -- --run src/components/KnowledgeSourcesView.interviewCapture.test.tsx src/components/KnowledgeSourcesView.test.tsx
Set-Location ..
```

预期：全部 PASS。

- [ ] **Step 5: 提交 Knowledge 只读切片**

```powershell
git add web/src/types/knowledge.ts web/src/services/knowledge.ts web/src/components/KnowledgeSourcesView.tsx web/src/components/KnowledgeSourcesView.test.tsx web/src/components/KnowledgeSourcesView.interviewCapture.test.tsx
git commit -m "feat: AI show confirmed interview knowledge"
```

### Task 8: 隔离 real-AI smoke 与浏览器闭环

**Files:**
- Modify: `src/offerpilot/smoke.py`
- Modify: `tests/test_smoke.py`
- Create: `scripts/interview-knowledge-real-ai-browser-harness.ps1`

- [ ] **Step 1: 写失败的隔离 smoke 测试**

为 real-AI profile 增加合成数据和清理断言：

```python
def test_real_ai_interview_knowledge_capture_uses_isolated_data_and_cleans_all_assets(tmp_path):
    result = run_real_ai_capture_smoke(tmp_path)
    assert result.cleaned_temp_data is True
    assert result.source_data_unchanged is True

def test_local_profile_does_not_call_interview_knowledge_provider(tmp_path):
    result = run_local_capture_smoke(tmp_path)
    assert result.provider_calls == 0
```

断言至少三组非空 InterviewNote：直接保存、AI 预览后直接保存、来源漂移 409；确认前无 Knowledge 行，确认后链路完整，原 note 删除后仍可读，清理后 tempData 无残留且 sourceData 不变。

- [ ] **Step 2: 运行 smoke 测试确认失败**

```powershell
uv run pytest tests/test_smoke.py -q
```

预期：新增 real-AI capture smoke 尚不存在或失败；现有 local smoke 必须保持通过。

- [ ] **Step 3: 实现隔离 harness**

PowerShell harness 必须：

1. 创建 `$tempData`，只复制脱敏配置，不复制正式数据库；
2. 选择并确认空闲端口，启动 `OFFERPILOT_DATA=$tempData` 服务；
3. 启动后检查监听 PID 属于本次进程树，不匹配则停止本次进程并禁止浏览器打开；
4. 逐次检查每个原生命令 `$LASTEXITCODE`，非零立即 `throw`，外层 `finally` 仍清理进程树和 tempData；
5. 浏览器从根地址进入投递详情的面试复盘入口，完成选择→直接保存或 AI 预览→二次确认→Knowledge 历史查看；
6. 断言浏览器请求只到本地 `/api`、静态资源和已配置 Provider，无 URL 抓取/招聘平台/自动投递；
7. 停服后删除合成 Application、InterviewNote、attempt 和 Knowledge 资产，再断言 tempData 干净；确认已确认资产删除仅用于 smoke 清理，正式产品语义仍保留历史资产。

- [ ] **Step 4: 运行测试与隔离 real-AI 验收**

```powershell
uv run pytest tests/test_smoke.py -q
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\interview-knowledge-real-ai-browser-harness.ps1
```

预期：local profile 不调用 Provider；harness 在 `$tempData` 环境内执行 real-AI verify 与浏览器流程，不运行会写默认数据目录的裸 `real-ai verify`；AI 返回有效预览、安全空预览或安全 502 均不产生未确认 Knowledge；浏览器完成确认后的历史查看；清理和 sourceData 隔离断言通过。

- [ ] **Step 5: 提交 smoke 切片**

```powershell
git add src/offerpilot/smoke.py tests/test_smoke.py scripts/interview-knowledge-real-ai-browser-harness.ps1
git commit -m "test: AI add interview knowledge capture smoke"
```

### Task 9: 全量门禁、差异审查与交付报告

**Files:**
- No new implementation files; only test or documentation adjustments discovered by verification

- [ ] **Step 1: 运行后端定向门禁**

```powershell
uv run pytest tests/test_interview_knowledge_capture_migrations.py tests/test_interview_knowledge_capture_fragments.py tests/test_interview_knowledge_capture_repository.py tests/test_interview_knowledge_capture_ai.py tests/test_interview_knowledge_capture_api.py -q
uv run ruff check src tests
uv run mypy src
```

预期：新增与受影响后端测试全绿；若全量已有基线失败，逐项记录命令、失败测试和是否由本任务触发，不得将其报告为通过。

- [ ] **Step 2: 运行前端门禁**

```powershell
Set-Location web
npm.cmd test -- --run
npm.cmd run build
Set-Location ..
```

预期：前端定向与全量测试、生产构建通过；React `act()` 警告只能记录，不能掩盖失败。

- [ ] **Step 3: 运行全量 smoke 和 diff 检查**

```powershell
uv run pytest
uv run oc smoke --static-dir web/dist
git diff --check origin/main..HEAD
git status --short --branch
```

预期：新增/受影响测试全绿，工作区干净；任何既有失败须在报告中明确列出。

- [ ] **Step 4: 自审安全边界**

用 `rg` 检查：

```powershell
rg -n "job_url|requests\.get|/jobs|auto.?apply|Memory|weakness|exercise" src/offerpilot/ai/interview_knowledge_capture.py src/offerpilot/repositories/interview_knowledge_capture.py web/src/components/InterviewKnowledgeCaptureDrawer.tsx
```

确认 AI 模块没有读取 JD/Resume/聊天/Memory，确认接口没有外部 URL 访问，确认前端没有自动练习、自动投递或未确认写入路径。日志审查不得出现模型原文、完整复盘、Evidence 摘录或密钥。

- [ ] **Step 5: 交付报告**

报告必须包含：

- 改动文件与对应功能；
- 迁移版本 `0011_confirmed_interview_knowledge_capture` 和破坏性变化说明（预期无；确认后的 Attempt 随原 note 删除是明确语义）；
- UTF-16、Snapshot parser、canonical ID、Attempt CAS、逐块 Evidence、来源漂移和原 note 删除审计测试名称及结果；
- 隔离 real-AI 浏览器实际结果：有效预览/安全空预览/502 的具体安全状态，不输出模型原文或敏感数据；
- 未运行或既有失败的门禁命令、原因和剩余风险。

### Task 10: 独立最终 CR 与问题回归

**Files:**
- Review: `src/offerpilot/models.py`, `src/offerpilot/db.py`, `src/offerpilot/knowledge/interview_capture.py`, `src/offerpilot/repositories/interview_knowledge_capture.py`, `src/offerpilot/ai/interview_knowledge_capture.py`, `src/offerpilot/api.py`
- Review: `web/src/layout/AppShell.tsx`, `web/src/components/InterviewKnowledgeCaptureDrawer.tsx`, `web/src/components/KnowledgeSourcesView.tsx`
- Review: all new/modified tests and `scripts/interview-knowledge-real-ai-browser-harness.ps1`

- [ ] **Step 1: 发起独立 CR，不让 reviewer 代写代码**

在所有 inline 实现和 Task 9 门禁完成后，使用 `requesting-code-review` 或等价独立审查流程提交：批准设计文档、当前 diff、定向测试结果和安全边界检查结果。审查范围必须特别询问：UTF-16 helper 是否真的使用 source fields、双 connection + Provider barrier 是否证明 session 释放和单调用、AppShell 是否拥有完整 noteId draft、Attempt cascade 是否不影响冻结资产、canonical ID 是否贯穿 preview/confirm/Evidence。

预期：reviewer 只输出问题列表和风险，不直接修改 worktree；inline 开发者负责逐项处理。

- [ ] **Step 2: 为每个 CR 问题先补回归测试**

每个可复现问题必须先在对应测试文件增加失败断言：Unicode/fragment 问题放入 `tests/test_interview_knowledge_capture_fragments.py`；CAS/删除/来源漂移问题放入 `tests/test_interview_knowledge_capture_repository.py` 或 `tests/test_interview_knowledge_capture_api.py`；AppShell 卸载/重进问题放入 `web/src/layout/AppShell.interviewKnowledgeCapture.test.tsx`；canonical ID 或文案问题放入对应 service/component 测试。

- [ ] **Step 3: inline 修复并回归**

只在当前 worktree inline 修复已确认问题，按最小范围提交；每个修复后运行对应定向测试，再重跑：

```powershell
uv run pytest tests/test_interview_knowledge_capture_migrations.py tests/test_interview_knowledge_capture_fragments.py tests/test_interview_knowledge_capture_repository.py tests/test_interview_knowledge_capture_ai.py tests/test_interview_knowledge_capture_api.py -q
Set-Location web
npm.cmd test -- --run src/layout/AppShell.interviewKnowledgeCapture.test.tsx src/components/InterviewKnowledgeCaptureDrawer.interaction.test.tsx src/components/KnowledgeSourcesView.interviewCapture.test.tsx
Set-Location ..
```

预期：CR 所指出的每个问题都有对应回归测试，且新增/受影响测试全部 PASS。

- [ ] **Step 4: 复核独立 CR 结论并交付**

确认所有 CR 意见已修复或有明确技术理由不采纳；最终报告列出 CR 审查范围、问题、回归测试和剩余风险。不得以“测试通过”替代独立 CR。

## 完成定义

- 直接保存路径不调用 AI，确认前没有任何 Knowledge Source/Evidence/Note Version；
- AI 预览只使用 canonical selected fragments，严格 JSON/证据/上限校验，最多一次修复；Provider/网络异常保留 key，契约失败转安全空预览；
- 确认事务在来源未漂移时原子写入三类知识资产，每个最终 block 都有至少一条 Evidence，允许多个 block 复用同一 Evidence；
- 原 InterviewNote 物理删除后，确认后的 Source/Snapshot/Evidence/Note Version 仍可审计，已确认 Attempt 不再提供幂等恢复；
- 未确认内容不进入 Knowledge、检索、练习、Memory 或能力画像；没有 URL 抓取、招聘平台访问、自动投递或自动状态迁移；
- 后端定向测试、前端定向测试、构建、静态检查和隔离 real-AI 浏览器验收均有可复现记录。
