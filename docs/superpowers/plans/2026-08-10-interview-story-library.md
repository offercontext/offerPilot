# 结构化面试故事库实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Each step uses checkbox syntax and must be completed in order. Do not dispatch implementation work to subagents; use a separate, read-only reviewer only after implementation is complete.

**Goal:** 在顶层“面试”模块交付独立、可审计、可版本化的候选人面试故事资产：用户可手动保存、修订、归档和恢复故事；也可从明确选择的已保存复盘原文生成严格证据化的 Story Proposal，编辑、选择后经第二次确认保存。UI 与 Pilot 共享同一领域规则、Proposal 和确认契约，但不会自动生成、自动写入或推断故事使用。

**Architecture:** 新建独立 Story 聚合和 Repository，不复用 Knowledge Note，也不写入 Knowledge、Memory、Application、Event、Mock 或 Chat。`InterviewStory` 只保存生命周期和当前 Version 指针；每次确认追加不可变 `InterviewStoryVersion`、精确目标化的 Evidence Links，以及必要的不可变 `user_assertion`。Story Proposal Attempt 以全局幂等键、冻结最小来源、revision/token/lease fencing 和一次受限结构修复保护 Provider 调用；确认事务重新验证来源、CAS 更新 Story 指针并且只写 Story 表。前端顶层 Story Library 与 Pilot 主动入口只导航/调用同一套 API，未知结果草稿由 AppShell 按入口和目标 Story 分开持有。

**Tech Stack:** Python 3、FastAPI、SQLAlchemy、SQLite、Pydantic、pytest、现有 ChatModel/Provider fake、React、TypeScript、Ant Design、Vitest、Playwright/CDP harness、PowerShell。

**Design source:** `docs/superpowers/specs/2026-08-10-interview-story-library-design.md`

---

## 0. 实施边界、稳定基线与文件地图

### 0.1 开始前固定一次 implementation baseline

实施只能在 `D:\Users\yuqi.chen\offerpilot\.worktrees\feat-20260810-interview-story-library`、分支 `feat/20260810-interview-story-library` 中进行；不得触碰根工作树、`feat/20260805-application-jd-versions` 或其未提交改动。开始写第一行产品代码前运行一次：

```powershell
$planPath = 'docs/superpowers/plans/2026-08-10-interview-story-library.md'
$baselineFile = Join-Path $env:TEMP 'offerpilot-interview-story-library-baseline.txt'
$implementationBase = (git log -1 --format=%H -- $planPath).Trim()
if (-not $implementationBase) { throw 'Cannot resolve approved plan baseline' }
if (@(git status --short).Count -gt 0) { throw 'Worktree must be clean before capturing implementation baseline' }
git cat-file -e "$implementationBase^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'Approved implementation baseline is invalid' }
$implementationBase | Set-Content -LiteralPath $baselineFile -Encoding ascii
```

每一个后续独立 PowerShell 进程在运行 allowlist、diff 或 gate 前都先恢复同一个值，绝不在实施中途重新 `git log`：

```powershell
$baselineFile = Join-Path $env:TEMP 'offerpilot-interview-story-library-baseline.txt'
$implementationBase = (Get-Content -LiteralPath $baselineFile -Raw).Trim()
git cat-file -e "$implementationBase^{commit}"
if ($LASTEXITCODE -ne 0) { throw 'Recorded implementation baseline is invalid' }
```

实施期间不再改设计或本计划。最终所有 gate、独立 CR、报告提交及干净工作树都通过后才删除该文件；任一失败时保留它以便修复后重跑。

### 0.2 重新审计与 JD Version 分支的真实交集

实施开始时记录双方相对各自 fork point 的文件集合和交集，重点审计 `api.py`、`models.py`、`schemas.py`、`AppShell.tsx`：

```powershell
$storyBase = git merge-base $implementationBase feat/20260805-application-jd-versions
$jdBase = git merge-base feat/20260805-application-jd-versions main
$storyFiles = @(git diff --name-only "$storyBase..$implementationBase" | Sort-Object -Unique)
$jdFiles = @(git diff --name-only "$jdBase..feat/20260805-application-jd-versions" | Sort-Object -Unique)
$intersection = @($storyFiles | Where-Object { $_ -in $jdFiles })
[pscustomobject]@{ story_base = $storyBase; jd_base = $jdBase; intersection = $intersection } |
  ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $env:TEMP 'offerpilot-story-jd-overlap.json') -Encoding utf8
```

中心文件出现交集是预期风险，不能因此宣称无冲突：Story 路由须在独立连续块注册；模型只追加 Story 表；AppShell 只接入独立 feature state；合并前必须逐文件人工重放并回归两个分支。Story 第一期不接入 JD Version、Opportunity Fit、Material、Interview Preparation 或 Mock 的写路径。

### 0.3 允许的实现文件

除了本计划允许的发布报告，产品代码只可改动下列文件；新增文件按 `Create`，已有文件按 `Modify`。需要新增其他文件时，先停止并修订计划，不得悄悄扩大范围。

| 层 | 文件 |
| --- | --- |
| 模型与迁移 | Modify `src/offerpilot/models.py`, `src/offerpilot/db.py`; Create `tests/test_interview_stories_migrations.py` |
| Story 领域 | Create `src/offerpilot/repositories/interview_stories.py`, `src/offerpilot/ai/interview_stories.py`, `tests/test_interview_stories_repository.py`, `tests/test_interview_stories_ai.py` |
| HTTP/契约 | Modify `src/offerpilot/api.py`, `src/offerpilot/schemas.py`, `src/offerpilot/cli.py`, `src/offerpilot/smoke.py`; Create `tests/test_interview_stories_api.py`, `tests/test_interview_stories_smoke.py` |
| Pilot 接线 | Modify `web/src/layout/AppShell.tsx`, `web/src/components/ChatPanel/index.tsx`, `web/src/components/ChatPanel/ContextPanel.tsx`; Create `web/src/components/ChatPanel/InterviewStoryPilotEntry.test.tsx` |
| 前端类型与服务 | Create `web/src/types/interviewStory.ts`, `web/src/services/interviewStories.ts`, `web/src/services/interviewStories.test.ts` |
| 面试 Story UI | Modify `web/src/components/InterviewV01View.tsx`; Create `web/src/components/InterviewStoryLibraryView.tsx`, `web/src/components/InterviewStoryLibraryView.module.css`, `web/src/components/InterviewStoryLibraryView.test.tsx`, `web/src/components/InterviewStoryLibraryView.interaction.test.tsx`, `web/src/components/InterviewStoryDrawer.tsx`, `web/src/components/InterviewStoryDrawer.module.css`, `web/src/components/InterviewStoryDrawer.interaction.test.tsx`, `web/src/layout/AppShell.interviewStories.test.tsx` |
| 真实验收 | Create `scripts/interview-story-real-ai-browser-harness.ps1`, `tests/test_interview_story_browser_harness.py` |
| 验收记录 | Create `docs/reports/2026-08-10-interview-story-library-release-verification.md`，最终用 `git add -f` 暂存 |

明确禁止：修改 `src/offerpilot/knowledge/**`、`src/offerpilot/ai/tools.py`、现有 Interview/Mock/Knowledge 领域语义、JD Version 文件、任何外部招聘平台代码；新增 `StoryUsage` 表、usage 字段、usage API、自动练习或自动写入入口。

#### Post-review allowlist correction

The following existing support files are also allowed by the same exact-path
allowlist. They are necessary to make the approved Story acceptance audit and
the complete release gate exercise the Story UI, rather than a stale test
fixture. No product capability outside the Story aggregate is authorized:

| Scope | File | Restriction |
| --- | --- | --- |
| CDP response audit | Modify `scripts/browser-network-audit.py` | Capture only redacted structured workflow metadata after `Network.loadingFinished`; do not record bodies or credentials. |
| CDP response audit regression | Modify `tests/test_browser_network_audit.py` | Exercise the same redacted Story interaction record through the existing fake CDP server; do not add browser, Provider, or product writes. |
| Provider egress timing audit | Modify `scripts/provider-egress-proxy.py` | Record only a local UTC epoch connection timestamp with the already-allowed endpoint tuple, so the harness can bind each connection to the preceding UI or Pilot request window; never inspect tunneled payloads or credentials. |
| Windows backend manifest parser | Modify `scripts/windows-pytest-groups.ps1`, `tests/test_windows_pytest_groups.py` | Parse both normalized `/` and native Windows `\` test node-id separators before aggregate coverage comparison; do not alter grouping, skip, or completion semantics. |
| Complete-gate test timing | Modify `tests/test_chat_api.py` | Only stabilize the two named, intentionally slow Chat confirmation tests; no Chat production behavior or contract change. |
| AppShell test fixtures | Modify `web/src/layout/AppShell.evidenceNavigation.test.tsx`, `web/src/layout/AppShell.offerNegotiation.test.tsx` | Only supply the Ant Design exports needed to mount the approved Story drawer through real `AppShell` imports. |
| Reviewed design contract | Modify `docs/superpowers/specs/2026-08-10-interview-story-library-design.md` | Record the persisted bounded `repair_count` audit field required by the reviewed Story attempt contract; do not expand product scope. |
| This reviewed plan | Modify `docs/superpowers/plans/2026-08-10-interview-story-library.md` | Record this exact allowlist correction and the later review-required Story regression tests. |

The final machine allowlist in Task 10 must include these eight files and this
plan file, in addition to every path in the table above. This correction is
required because the original allowlist omitted existing test/audit fixtures
that the approved browser and full-gate tasks already depend on.

### 0.4 固定领域常量与来源协议

在 `repositories/interview_stories.py` 单点定义并由 API、AI validator、前端 tests 共同约束以下常量；不要在 API、组件和 prompt 复制字符串：

```python
STORY_VERSION_SCHEMA = "interview-story-v1"
STORY_PROPOSAL_SCHEMA = "interview-story-proposal-v1"
STORY_LEASE_SECONDS = 30
STORY_HEARTBEAT_SECONDS = 10
MAX_STORY_BLOCKS = 12
MAX_CAPABILITY_LABELS = 12
MAX_APPLICABLE_QUESTIONS = 12
MAX_FACT_GAPS = 8
MAX_EVIDENCE_REFS_PER_TARGET = 5
MAX_TITLE_CHARS = 200
MAX_BLOCK_CHARS = 4_000
MAX_SHORT_ITEM_CHARS = 300
MAX_ASSERTION_CHARS = 4_000
MAX_EVIDENCE_EXCERPT_CHARS = 800
ASCII_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
```

允许的 Provider/保存来源只有四类，且必须由服务器重新解析：

| `source_kind` | 服务器可接受的稳定身份与规范 path | 资格 |
| --- | --- | --- |
| `resume_version` | `resume_id`，`/content_json/<RFC6901 escaped string-leaf path>` | Resume 未删除；只接受 `content_json` 的非空字符串叶子 |
| `interview_note` | `note_id`，`/questions`、`/self_reflection`、`/difficulty_points`、`/mood` | 已保存、当前可见的 `InterviewNote` 原文字段；绝不使用 Review Proposal AI 输出 |
| `mock_turn` | `attempt_id + turn_no`，`/turns/001/question` 或 `/turns/001/answer` | Attempt 已完成且未取消，Turn 为 `answered`；三位补零编号 |
| `user_assertion` | 本次请求临时 `assertion_001`，固定 `/statement`；确认后映射为 `InterviewStoryUserAssertion.id` | 用户在本次保存/确认中显式提交的原始陈述，且仅证明“用户如此陈述” |

任何 `KnowledgeEvidence`、Knowledge Source、Memory、Application/JD、Offer、Chat、旧 AI Proposal、未选择来源、跨 source/path 引用，均返回确定性错误，绝不能当作候选人事实。字符串证据目录只发布 `value.strip()` 非空的叶子，但保留原文本，不 trim 后再校验 excerpt。JSON Pointer 使用 RFC 6901 转义；排序键固定为 `source_kind, source_stable_id, source_path`。

## Task 1: 建立 Story 模型和可重入 SQLite 迁移

**Files:** Modify `src/offerpilot/models.py`, `src/offerpilot/db.py`; Create `tests/test_interview_stories_migrations.py`.

- [ ] **Step 1: 写失败的 fresh/legacy migration 测试。**

```python
def test_fresh_database_creates_story_phase_one_tables_and_records_0019(tmp_path):
    factory = init_database(tmp_path / "story.db")
    with factory() as session:
        tables = set(session.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars())
        migrations = set(session.execute(text("SELECT version FROM schema_migrations")).scalars())
    assert {
        "interview_stories", "interview_story_versions",
        "interview_story_version_evidence_links", "interview_story_user_assertions",
        "interview_story_proposal_attempts",
    } <= tables
    assert "0019_interview_story_library" in migrations
    assert "interview_story_usage" not in tables
```

再建 pre-story DB，初始化两次，断言既有 InterviewNote/Mock Turn 不变且 migration 只有一条；断言 Attempt 的 `idempotency_key` 为全局 `UNIQUE`，Evidence Link 唯一约束包含 Version、target kind/id 和完整来源 identity，且没有 usage 字段。外部 Resume/Note/Mock/Application source identity 必须为普通字段，不建立会 cascade/阻塞历史 Story 的 FK。

新增并先写失败的共存回归：以包含 `0018_application_jd_versions` marker 和最小 JD Version schema 的 SQLite fixture 启动初始化，断言该 marker/schema 均不被改写，`0019_interview_story_library` 与五张 Story 表同时出现。该测试专门防止并行 JD 分支合并后因编号冲突而将 Story schema 静默视为已迁移。

- [ ] **Step 2: 运行确认失败。**

```powershell
uv run pytest tests/test_interview_stories_migrations.py -q
```

- [ ] **Step 3: 最小追加模型和迁移。**

新增 `InterviewStory`（title projection、`active|archived`、current version plain pointer、story revision）、`InterviewStoryVersion`（内部 `story_id` FK、不可变 content/fingerprints、`manual|proposal`）、`InterviewStoryVersionEvidenceLink`（target kind/id 与来源 snapshot）、`InterviewStoryUserAssertion`（Version FK、原文/hash）和 `InterviewStoryProposalAttempt`（全局 key、entrypoint/context、lease/fencing、frozen input、proposal、confirmation、confirmed identities）。

`InterviewStory.current_version_id` 是 nullable plain `Integer`，Repository 在事务中验证 pointer 的 Story 归属；Version/Link/Assertion 使用内部 Story FK 与 `RESTRICT`。`db.py` 在 `Base.metadata.create_all(engine)` 后运行 `_ensure_interview_story_schema()`，创建索引并 `INSERT OR IGNORE` 记录 `0019_interview_story_library`；不重建既有表、不回填旧数据，也不触碰 JD 分支的 `0018_application_jd_versions` marker/schema。

- [ ] **Step 4: 通过并提交 schema 切片。**

```powershell
uv run pytest tests/test_interview_stories_migrations.py -q
git add src/offerpilot/models.py src/offerpilot/db.py tests/test_interview_stories_migrations.py
git commit -m "feat: AI add interview story schema"
```

## Task 2: 实现确定性 Story 内容、来源和手动 Version 写入

**Files:** Create `src/offerpilot/repositories/interview_stories.py`, `tests/test_interview_stories_repository.py`.

- [ ] **Step 1: 写失败的 content/evidence/source resolver 测试。**

真实 SQLite fixture 建立 Resume、InterviewNote、已完成 Mock Attempt/Turn 和软删除 Application，覆盖：

1. content canonicalization 分配 `title`、`situation_001`、`capability_001`、`question_001` 的稳定 ASCII target ID；重复显示文本仍得到不同 ID。
2. title、每个非空 `situation/task/action/result/reflection`、每个 label/question 都必须有 Link；`reflection` 必须是 `user_view`；fact gap 只能来自固定版本化枚举。
3. Resume Pointer 的 `~`、`/`、中文、emoji、NFD 不作 Unicode normalization；source sorting 稳定。
4. Review 只接受四个原文字段；Review Proposal JSON、KnowledgeEvidence、Application/JD、Chat、外部 Knowledge 全拒绝。
5. Mock 只接受 completed/answered source 和三位补零 path；未完成、取消、跨 Attempt、`/turns/1/answer` 均拒绝。
6. `excerpt==""` 为结构错误；全空白、超限、不连续、未知 source/path 或交叉 source/path 为语义错误。
7. 临时 `assertion_001` 只能 `/statement`；确认前不插入 Assertion，确认后 Link 指向实际 Assertion；显示恒为“已冻结的用户确认陈述”。
8. Resume/Note/Mock 修改或删除后，已存 Version 冻结摘录保持可读，读取状态分别为 `changed/missing`；永远不把状态回写 Link。

- [ ] **Step 2: 运行确认失败。**

```powershell
uv run pytest tests/test_interview_stories_repository.py -q -k "source or evidence or content"
```

- [ ] **Step 3: 实现共享纯函数。**

```python
def canonical_story_content(raw: Mapping[str, Any]) -> dict[str, Any]: ...
def materialize_selected_sources(session: Session, selections: list[dict[str, Any]], assertions: list[str]) -> StorySourceSnapshot: ...
def validate_story_evidence_links(content: Mapping[str, Any], links: list[dict[str, Any]], snapshot: StorySourceSnapshot) -> list[CanonicalStoryLink]: ...
def derive_story_source_states(session: Session, version: InterviewStoryVersion) -> list[StoryEvidenceView]: ...
def story_request_fingerprint(*, target_story_id: int | None, expected_current_version_id: int | None, expected_story_revision: int | None, selections: list[dict[str, Any]], assertions: list[str]) -> str: ...
```

上述函数是唯一来源：API、AI 和 confirm 不得复制 pointer、source 或 fingerprint 规则。所有 hash 使用 `canonical_json()` + SHA-256；原始 assertion/Resume/Note 文本不写普通日志。

- [ ] **Step 4: 写失败的手动 Story 生命周期/CAS 测试。**

首建必须在同一事务创建 Story+Version+Links+Assertions；修订只追加 Version/Links/Assertions 并 CAS 更新 pointer。双 SQLite connection 同时同 revision/current-version 提交，恰好一个成功；归档拒绝 Version；恢复需 revision，不修改 Version。列表支持 `active|archived|query`，历史按 Version number 只读稳定排序。每条手动保存断言零 ChatModel、零 Knowledge/Memory/Application/Event/Mock/Chat 写入。

- [ ] **Step 5: 实现 `InterviewStoriesRepository` 手动路径。**

实现 `list_stories`、`get_story`、`get_version`、`create_manual_story`、`create_manual_version`、`archive`、`restore`。写入必须 `BEGIN IMMEDIATE`，事务内重新 materialize source、canonicalize content、验证 Links、插入数据并 CAS 更新 Story projection。首建 `expected_current_version_id` 必须 strict `null`，修订必须 strict positive integer；校验失败、CAS conflict 或取消绝不留下 Assertion 行。

- [ ] **Step 6: 通过并提交。**

```powershell
uv run pytest tests/test_interview_stories_repository.py -q -k "source or evidence or manual or archive or version"
git add src/offerpilot/repositories/interview_stories.py tests/test_interview_stories_repository.py
git commit -m "feat: AI add versioned interview stories"
```

## Task 3: 用失败测试固定严格 Proposal JSON、一次修复和安全空结果

**Files:** Create `src/offerpilot/ai/interview_stories.py`, `tests/test_interview_stories_ai.py`; Modify `src/offerpilot/repositories/interview_stories.py`.

- [ ] **Step 1: 写失败的 AI contract 测试。**

使用 queued fake ChatModel 覆盖 native JSON Schema 和 text-only Provider。Proposal raw JSON 固定为：

```json
{
  "title": {"text": "string", "evidence_refs": []},
  "blocks": [{"kind": "situation|task|action|result|reflection", "text": "string", "fact_mode": "evidence_backed|user_view", "evidence_refs": []}],
  "capability_labels": [{"text": "string", "evidence_refs": []}],
  "applicable_questions": [{"text": "string", "evidence_refs": []}],
  "fact_gap_codes": ["missing_result"]
}
```

服务端为 Proposal 条目分配稳定 ID；Provider 不提交 id。表驱动测试必须证明：

- 每个 target 只能引用 catalog 中同 source/path 的连续 excerpt；`reflection` 仅可 `user_view`，S/T/A/R 仅可 `evidence_backed`。
- 缺少 result 时只有固定 `missing_result`，不得补造数字、范围、团队规模或业务结果。
- 非对象、字段类型错误、缺字段、额外字段、空 excerpt、无效 JSON 为 `invalid_shape`：最多第二次 Provider 调用；repair prompt 仅含失败类别、固定 schema、允许 evidence object shape，不能含模型原文、冻结来源或用户文本。
- forged source、source/path cross-reference、非法 pointer、旧 Mock path、excerpt mismatch、纯空白 excerpt、超限、未允许 Knowledge、语义越界都是语义失败：Provider 调用严格一次，绝不 repair。
- 两次结构失败或合法但没有可验证内容生成固定 `safe_empty`，它必须可被 validator 接受但永远不可确认、无模型原文。
- Provider/网络异常为 `StoryProviderError`，诊断只含 failure category、HTTP 状态/timeout、duration、hash 后 request id。

- [ ] **Step 2: 运行确认失败。**

```powershell
uv run pytest tests/test_interview_stories_ai.py -q
```

- [ ] **Step 3: 实现严格 AI 模块。**

实现 `INTERVIEW_STORY_JSON_SCHEMA`、`safe_empty_interview_story_proposal()`、`generate_interview_story_proposal()`、`validate_interview_story_proposal()`。Provider 仅收到已选来源的最小 evidence catalog；prompt 明确没有可验证 result 就输出 `fact_gap_codes:["missing_result"]`，不能编造事实。模型不得收到未选 Resume、完整复盘/Mock transcript、已有 Story、Application/JD、Knowledge、Memory、Chat、数据库内部信息、密钥或诊断。

分类必须先判断 evidence object shape/type，再判断 source/path/excerpt 语义；不要用 `unknown_evidence_ref` 覆盖结构错误。Repository 确认时仍会重验用户编辑后的 content/links。

- [ ] **Step 4: 通过并提交。**

```powershell
uv run pytest tests/test_interview_stories_ai.py -q
git add src/offerpilot/ai/interview_stories.py src/offerpilot/repositories/interview_stories.py tests/test_interview_stories_ai.py tests/test_interview_stories_repository.py
git commit -m "feat: AI validate evidence gated story proposals"
```

## Task 4: 实现 Attempt lifecycle、心跳 fencing、确认原子性和恢复

**Files:** Modify `src/offerpilot/repositories/interview_stories.py`, `tests/test_interview_stories_repository.py`.

- [ ] **Step 1: 写失败的 Attempt/lease/CAS 测试。**

使用 `ManualClock`、`ControlledWaiter`、Provider barrier 和两个 SQLite session factory；禁止 `sleep()` 驱动 lease。覆盖：

1. 同 key、同 canonical selection/assertion/input 指纹只创建一次 Attempt/一次 Provider；第二请求为 pending/replay。
2. 同 key、不同指纹返回稳定 `StoryIdempotencyConflict`，不改原 `generating/provider_unknown/ready` Attempt、revision、token 或 lease。
3. Provider 被 barrier 阻塞、假时钟越过原 lease 后，一次 Story heartbeat 续期；第二连接同 key 只能 pending，总 Provider 调用为 1；每 heartbeat tick 新建 Session。
4. 停止 heartbeat、推进时钟至过期后恰好一个新 owner 可接管；旧 owner 晚到的不同合法 Proposal 无法覆盖新 owner 的 proposal/hash/revision/token/lease。
5. 一次锁冲突后有限重试成功仍阻止接管；全部续签不可确认标 `heartbeat_uncertain`，未接管的 owner 仍可 final CAS ready；只有 rowcount=0 或明确 status/revision/token mismatch 是 `confirmed_ownership_lost`。
6. frozen source 在 Provider 期间漂移为 `invalidated` / `409 story_source_conflict`；Provider/网络/response-lost 为 `provider_unknown`；两次结构失败 `safe_empty`；语义失败 `contract_failed` 且同 key 不再调用 Provider。
7. 读取 ready/safe-empty/confirmed Attempt 完全独立于新 draft，不能改变进行中的 key。

- [ ] **Step 2: 实现 claim、短 session 和 heartbeat。**

Repository constructor 仅以私有 keyword-only timing seams 接收生产默认 UTC-aware `now_factory`、30 秒 lease、10 秒 heartbeat；不暴露 API。Claim 在短 `BEGIN IMMEDIATE` 事务中先全局 key replay，再 materialize frozen source/fingerprint，创建或合法接管 Attempt，commit 并退出 Session；只有此后启动 `_InterviewStoryLeaseHeartbeat` 与 Provider。

heartbeat 条件更新只写 `provider_lease_until`，约束 Attempt id、`generating`、generation revision、provider token。每 tick 使用新 Session；锁冲突在本 tick 有界重试。Python 用 aware UTC，SQLite lease 写入/比较用 naive UTC；final CAS 不依赖 lease 大于 now，只依赖 status/revision/token/frozen source fingerprint。

- [ ] **Step 3: 写失败的 confirmation 测试并实现。**

`confirm_attempt()` 的 transaction order 必须是：同 confirmation token replay → ready/token/payload hash → 重验非 assertion frozen source → 重验编辑后 content/links → 原子创建首个 Story+Version+Links+Assertions，或只追加 Version+Links+Assertions 并 `expected_current_version_id/story_revision` CAS。两个 SQLite connection 同 token 只能创建一个 Version；来源变更、archived、过期/已消费 token、不同 confirmation payload 均不得产生第二 Version。响应丢失后同 token/payload 返回同一 Story/Version。

`safe_empty`、`contract_failed`、`provider_unknown`、`invalidated` 的 confirm 必须拒绝且五张 Story 表计数不变。临时 assertion 只有 transaction 最后成功时才 materialize 成 Row/Link。

- [ ] **Step 4: 通过并提交 lifecycle 切片。**

```powershell
uv run pytest tests/test_interview_stories_repository.py tests/test_interview_stories_ai.py -q
git add src/offerpilot/repositories/interview_stories.py tests/test_interview_stories_repository.py tests/test_interview_stories_ai.py
git commit -m "feat: AI add story proposal recovery and confirmation"
```

## Task 5: 固定 HTTP schema、路由和安全错误映射

**Files:** Modify `src/offerpilot/schemas.py`, `src/offerpilot/api.py`; Create `tests/test_interview_stories_api.py`.

- [ ] **Step 1: 写失败 API contract 测试。**

测试客户端 payload 精确键匹配、bool/float/string 不能冒充 CAS integer、ID/key/文本上限和稳定错误码。端点语义固定如下：

| Route | Required behavior |
| --- | --- |
| `GET /api/interview-stories?status=active|archived&query=&limit=&cursor=` | 元数据、当前 Version 摘要、读取时 source states；不返回完整 history/Attempt，不写入或调 Provider |
| `POST /api/interview-stories` | 手动首建，`expected_current_version_id:null`，固定 `origin_kind=manual` |
| `GET /api/interview-stories/{story_id}` | 当前 Version、冻结 evidence/read states |
| `GET /api/interview-stories/{story_id}/versions` / `.../{version_id}` | 前者仅元数据，后者才 full content/evidence |
| `POST /api/interview-stories/{story_id}/versions` | 手动新 Version、严格 current-version/revision CAS |
| `POST /api/interview-stories/{story_id}/archive` / `restore` | 只改 lifecycle、严格 story revision CAS |
| `POST /api/interview-story-proposals` | UI wrapper，server-side 固定 `entrypoint=ui` |
| `POST /api/pilot/interview-story-proposals` | Pilot wrapper，server-side 固定 `entrypoint=pilot`；同 payload/response contract |
| `GET /api/interview-story-proposals/{attempt_id}` | 只读恢复，不调 AI |
| `POST /api/interview-story-proposals/{attempt_id}/confirm` | 同一确认契约，不看 entrypoint 改写语义 |

断言 `201/200` 成功、`422` 未建 Attempt、`404` 不可见来源/Story、`409 story_idempotency_conflict` 不改旧 Attempt、`409 story_source_conflict` 清理本次生成 key、`202 generating/provider_unknown` 或 `502 story_provider_error` 保留 key、`502 story_unverifiable` 为 terminal/same-key no Provider。所有错误仅中文安全文案和稳定 `error_code`，不得含模型/Provider/快照原文。

- [ ] **Step 2: 运行确认失败并实现输出联合类型/route wrapper。**

```powershell
uv run pytest tests/test_interview_stories_api.py -q
```

在 `schemas.py` 新增独立 `InterviewStory*Out` 判别联合；前端不得将 pending/invalid Attempt 强制当 ready Proposal。`api.py` 采用小型 mapper，新 routes 位于同一连续块，静态 `interview-story-proposals` 在任何动态 story route 之前。两个 Proposal wrapper 只固定 entrypoint 并委托相同 parser/Repository；客户端 payload 不得有 entrypoint、fingerprint、proposal、token hash 或状态字段。

- [ ] **Step 3: 通过并提交 API 切片。**

```powershell
uv run pytest tests/test_interview_stories_api.py tests/test_interview_stories_repository.py -q
git add src/offerpilot/schemas.py src/offerpilot/api.py tests/test_interview_stories_api.py
git commit -m "feat: AI expose interview story APIs"
```

## Task 6: 添加前端类型、服务和零写入读取回归

**Files:** Create `web/src/types/interviewStory.ts`, `web/src/services/interviewStories.ts`, `web/src/services/interviewStories.test.ts`.

- [ ] **Step 1: 写失败的 TypeScript service 测试。**

建立 Axios mock，断言 list/detail/version GET 只读且正确表现 `current|changed|missing` 与独立的 `frozen_user_assertion`；不会把 `frozen` 伪装为可变 source status。UI create proposal 调 `/interview-story-proposals`，Pilot 调 `/pilot/interview-story-proposals`，payload 完全相同且不含 `entrypoint`；confirm 始终调同一 endpoint。

`generating/provider_unknown` 必须解析为可恢复 draft；`story_provider_error` 保留 key/frozen input；`story_unverifiable`、source/CAS 409 清理当前生成 key；`422/404` 清理未创建 draft。GET attempt、Version history、evidence 展开不得发 POST 或触发任何 write service。

- [ ] **Step 2: 运行确认失败并实现严格联合类型。**

```powershell
Set-Location web
npx vitest run src/services/interviewStories.test.ts
```

`interviewStory.ts` 必须用 discriminated unions 表达 ready/safe-empty/confirmed、generating/provider-unknown 和 contract/invalid attempt；不使用 `as StoryProposal` 绕过状态。`interviewStories.ts` 只做 transport 与安全 error discrimination，不在浏览器计算 fingerprint、证据或 source state。

- [ ] **Step 3: 通过并提交。**

```powershell
npx vitest run src/services/interviewStories.test.ts
Set-Location ..
git add web/src/types/interviewStory.ts web/src/services/interviewStories.ts web/src/services/interviewStories.test.ts
git commit -m "feat: AI add interview story web contract"
```

## Task 7: 在顶层面试模块实现 Story Library 与手动维护 UI

**Files:** Modify `web/src/components/InterviewV01View.tsx`; Create `web/src/components/InterviewStoryLibraryView.tsx`, `web/src/components/InterviewStoryLibraryView.module.css`, `web/src/components/InterviewStoryLibraryView.test.tsx`, `web/src/components/InterviewStoryLibraryView.interaction.test.tsx`, `web/src/components/InterviewStoryDrawer.tsx`, `web/src/components/InterviewStoryDrawer.module.css`, `web/src/components/InterviewStoryDrawer.interaction.test.tsx`.

**Required saved-review handoff:** a saved `InterviewNote` remains an Interview business record. Its `note_id` may pre-scope the Story Drawer source picker, but never preselects or freezes any source fragment. The user must explicitly select exact eligible original Review fragments before Proposal generation is enabled.

- [ ] **Step 1: 写真实挂载失败测试。**

挂载真实 `InterviewV01View` + Library/Drawer（不允许源码字符串扫描或整体 mock），验证：

1. 面试页保留既有事件列表，页头添加“故事库”入口和中文说明；search 与 `active|archived` filter 只触发 GET。
2. Story 卡显示最近确认 Version、能力标签、适用问题、fact gap 和“冻结来源”；`changed/missing` 为中文风险提示，不自动修复/重生成；assertion 显示“已冻结的用户确认陈述”，不伪称外部验证。
3. New Story 打开手动 Drawer；每个非空 target 没有合法来源时保存不可用；无来源/空白/非法 target id 不发请求；手动保存只 POST Story，零 AI request。
4. Detail 将 history 放入独立只读 state，不能覆盖当前手动编辑或正在恢复 Proposal；归档 Story 无法新建 Version/Proposal，只显示恢复入口；restore 成功才恢复编辑。
5. current Version 被其它窗口更新的 409 不吞掉原文，提示重新加载/重选，绝不静默创下一 Version。
6. 历史/evidence/filter/close 全是零 POST；所有固定文案是中文，并具备可访问名称/焦点关闭行为。
7. `note_id` 非空的已保存复盘事件项显示“整理为故事”。点击后打开同一个 `proposal` Story Drawer 并传入 `sourceScope={{ reviewNoteId: note_id }}`；picker 只列出该 Note 的 `questions`、`self_reflection`、`difficulty_points`、`mood` 原文候选片段，初始选中集合为空。断言未选片段不发 Proposal 请求；选定精确片段并二次确认后，仅向 UI Proposal endpoint 发送该 Note/field/path 的来源 identity，绝不发送 AI review proposal JSON、其他 Note 全文或任何 Knowledge。

- [ ] **Step 2: 运行确认失败。**

```powershell
Set-Location web
npx vitest run src/components/InterviewStoryLibraryView.test.tsx src/components/InterviewStoryLibraryView.interaction.test.tsx src/components/InterviewStoryDrawer.interaction.test.tsx
```

- [ ] **Step 3: 最小实现视觉和状态边界。**

`InterviewV01View` 仅添加 Story Library action，不改变投递详情、Knowledge 页或事件列表职责。Library 管理 list/query/filter/detail viewport；Drawer `mode` 明确为 `manual-new|manual-version|proposal|history`。历史永远只读，不能复用/覆盖进行中的 proposal/manual state。来源 picker 只在用户主动打开后显示 Resume、已保存复盘、已完成 Mock Turn、本次 assertion；不预选、不扫描全部文本、不渲染 source URL 链接。

对 `note_id` 非空的已保存复盘事件项添加显式“整理为故事”动作。它仅把不可变 `note_id` 和 `entrypoint=ui` 交给同一个受控 Drawer；scope 会在关闭、切换事件或重新打开后清除，不得泄漏到其他 Note。点击本身不创建 Story、Version、Attempt、Chat message 或 Provider 调用；只有用户在该 scope 内主动选择原始片段并确认生成，才进入现有 UI Proposal lifecycle。

使用现有 Ant Design/项目控件尺度，中文亮色可读；CSS 不引入全局布局规则。`InterviewStoryDrawer` 只能通过受控 `onDraftChange` 上报，不能在 effect 无条件调用新 callback 形成重渲染循环。

- [ ] **Step 4: 通过并提交 UI 切片。**

```powershell
npx vitest run src/components/InterviewStoryLibraryView.test.tsx src/components/InterviewStoryLibraryView.interaction.test.tsx src/components/InterviewStoryDrawer.interaction.test.tsx
Set-Location ..
git add web/src/components/InterviewV01View.tsx web/src/components/InterviewStoryLibraryView.tsx web/src/components/InterviewStoryLibraryView.module.css web/src/components/InterviewStoryLibraryView.test.tsx web/src/components/InterviewStoryLibraryView.interaction.test.tsx web/src/components/InterviewStoryDrawer.tsx web/src/components/InterviewStoryDrawer.module.css web/src/components/InterviewStoryDrawer.interaction.test.tsx
git commit -m "feat: AI add interview story library UI"
```

## Task 8: 接入 Proposal 审阅、AppShell 恢复和 Pilot 主动入口

**Files:** Modify `web/src/layout/AppShell.tsx`, `web/src/components/ChatPanel/index.tsx`, `web/src/components/ChatPanel/ContextPanel.tsx`; Create `web/src/layout/AppShell.interviewStories.test.tsx`, `web/src/components/ChatPanel/InterviewStoryPilotEntry.test.tsx`.

- [ ] **Step 1: 写 AppShell draft ownership 的失败测试。**

真实挂载 AppShell、Library、Drawer，按 `entrypoint:targetScope` 保存草稿：`ui:new`、`pilot:new`、`ui:story:<id>`、`pilot:story:<id>` 必须彼此隔离。覆盖：

- UI Proposal 的 `generating/provider_unknown` 关闭重进后，原 Attempt ID、key、source selection、assertions、frozen input、confirmation token、编辑 payload 均保留；“使用原尝试重试”只调原 UI endpoint/key。
- UI history 不能覆盖正在进行的 draft；safe-empty/contract-failed/invalidated 不显示 confirm；确定终态才清对应 generation key。
- confirmation 已写入但响应丢失时，重挂载以同 token/payload replay，仍只有一个 Version。
- UI/Pilot 同一 Story 草稿/key 不覆盖；两入口创建不同 Attempt，但确认同一 Story 时受相同 current-version/revision CAS。

- [ ] **Step 2: 实现 AppShell 受控草稿。**

新增 `interviewStoryDraftsRef` 与受控 state map，更新时总从 ref 的当前 scope 草稿合并，避免异步闭包覆盖 key/token。`generating/provider_unknown` 和 confirmation unknown 冻结普通编辑/操作，只允许同 key/同 frozen payload retry。`404/422` 清理未创建 draft；`story_unverifiable` 和 `story_source_conflict` 清理当前 generation key、保留用户可见原文供新尝试，不删除历史。

- [ ] **Step 3: 写 Pilot 入口失败测试。**

实际 `ContextPanel`/ChatPanel 测试必须证明：

1. 仅打开 Pilot 或展示静态入口，零 Conversation、ChatMessage、Story、Version、Attempt、Provider 调用。
2. 用户点击“整理面试故事”，或在 Composer 输入严格支持的“帮我整理一个面试故事”/“整理面试故事”，打开同一个 `pilot` scope Drawer；本地导航不发 `/api/chat`、不调 Provider、不猜测/自动选择 source。
3. 非匹配的普通 Pilot 消息保持现有 Chat 行为，但零 Story-domain 写入。
4. Pilot source picker 要求用户选择来源/手工 assertion；二次确认后只调 Pilot Proposal wrapper，随后用统一 confirm API 保存。UI/Pilot Attempt 的 entrypoint 正确且 Chat 表仍零写入。

- [ ] **Step 4: 实现最小 Pilot 适配器。**

向 ContextPanel/ChatPanel 增加 `onOpenInterviewStoryLibrary`，仅有一个显式可点击静态入口。Composer submit 在发送前只识别以上两个 trim 后完全匹配的意图并调用同回调；它不是模型语义解析、不会建 tool call、不会自动发送消息，也没有第二套 Story state machine。其余输入保持 Chat。Pilot Drawer 只改变标题“Pilot · 整理面试故事”，其 source、Proposal、confirm 均复用 Library/Drawer/service。

- [ ] **Step 5: 通过并提交 Pilot/recovery 切片。**

```powershell
Set-Location web
npx vitest run src/layout/AppShell.interviewStories.test.tsx src/components/ChatPanel/InterviewStoryPilotEntry.test.tsx src/components/InterviewStoryLibraryView.interaction.test.tsx src/components/InterviewStoryDrawer.interaction.test.tsx
Set-Location ..
git add web/src/layout/AppShell.tsx web/src/layout/AppShell.interviewStories.test.tsx web/src/components/ChatPanel/index.tsx web/src/components/ChatPanel/ContextPanel.tsx web/src/components/ChatPanel/InterviewStoryPilotEntry.test.tsx
git commit -m "feat: AI connect story proposal recovery and Pilot"
```

## Task 9: Add isolated Story verification, browser/CDP audit, and real-AI acceptance harness

**Files:** Modify `src/offerpilot/cli.py`, `src/offerpilot/smoke.py`; Create `tests/test_interview_stories_smoke.py`, `tests/test_interview_story_browser_harness.py`, `scripts/interview-story-real-ai-browser-harness.ps1`.

- [ ] **Step 1: Write failing isolated-verification and fake-CDP tests.**

  Cover the new `verify-interview-stories` command without invoking a real provider.  It must be explicitly labelled as an *isolated Interview Story API verification* and must not claim to replace full `verify` or browser/CDP evidence.  The local smoke must create the Chinese candidate `筱哲` with only permitted Story evidence (a Resume string leaf, an existing saved interview note, a completed Mock Interview turn, and an explicit user assertion), then exercise:

  1. manual Story create, immutable revision, archive/restore and read-only version history;
  2. a proposal that reaches `ready`, human selection/editing, idempotent confirmation, and a history read of the confirmed Version;
  3. source drift after confirmation, shown as derived `changed` without changing frozen links or historical text;
  4. provider-unknown recovery using the original attempt/key/frozen payload, and terminal unverifiable cleanup without a Version;
  5. UI and Pilot wrappers using different Attempts but the same domain endpoints and confirmation contract; Pilot’s static/open action must make zero `conversations` and `chat_messages` writes.

  The harness tests must start a fake Browser-level CDP endpoint and an egress-proxy fixture.  Require a dedicated target, `Target.setAutoAttach`, `Network.enable` ready hand-off, request method/URL/target/session capture, and response body capture only after `Network.loadingFinished`.  Assert that a run fails closed for a wrong target, missing ready file, `Network.enable` rejection, browser early exit, missing UI or Pilot sequence, or a request outside the local service and the configured Provider candidate allowlist.  Do not use string scanning as evidence.

- [ ] **Step 2: Run the new tests to confirm failure.**

  ```powershell
  uv run pytest tests/test_interview_stories_smoke.py tests/test_interview_story_browser_harness.py -q
  ```

- [ ] **Step 3: Implement the isolated command and harness.**

  Add `oc verify-interview-stories --profile local|real-ai --static-dir <path>`.  It must use a fresh temporary SQLite/static/config/output directory and a silent byte-for-byte copy of the configured real-provider file when `--profile real-ai` is requested; never print configuration, credentials, Resume/JD/note/assertion text, model text, or raw request IDs.  Persist only redacted phase, duration, model/provider endpoint triple, bounded request-size bucket, repair count, stable failure category, and hashed Provider request ID in smoke logs or reports.

  The PowerShell browser harness must start and own a temporary Chromium instance with Browser DevTools HTTP enabled, discover the browser WebSocket endpoint, create a dedicated `about:blank` target, auto-attach to its child targets, wait for the audit-ready file, navigate the target to the local app, and always stop the browser, auditor, local server, proxy, and temporary data in `finally`.  It must operate in Chinese light mode at a width of at least `1440x900`.

  Audit the following complete, target-bound request sequence for **both** entrypoints.  It may not accept arbitrary `/api/*` traffic as proof:

  - UI: Story library read → source selection/read → Proposal create/replay as needed → Proposal/readiness → confirmation → Story/version history read.
  - Pilot: explicit local “整理面试故事” action → source selection/read → Pilot Proposal create/replay as needed → confirmation → Story/version history read.

  For each entrypoint extract its own attempt ID from the proposal response/attempt read, prove its own confirmation/history belongs to that attempt or resulting Version, and prove UI/Pilot keys/attempts differ.  On `story_provider_error` the harness may use only the existing bounded user-visible retry rule with the same key; on `story_unverifiable`, source/CAS conflict, or a deterministic API error it must not retry that Attempt.  Report an incomplete Provider run as `provider_unstable_not_completed`, not as a successful browser flow.

  Before the acceptance baseline, seed the complete synthetic context: Application, event, saved InterviewNote, Resume version, completed Mock Attempt/Turn, local service, and the selected Provider route. Snapshot every table only after that seed is committed. During UI/Pilot Story acceptance, permit writes solely to `InterviewStory`, `InterviewStoryVersion`, `InterviewStoryVersionEvidenceLink`, `InterviewStoryUserAssertion`, and `InterviewStoryProposalAttempt`. Assert that the Application’s complete row and count remain unchanged, and require zero writes to Resume, application events, InterviewNote, Opportunity Fit, Material Kit, interview-preparation, mock-interview, Knowledge, Offer, reminders, conversations, and `chat_messages`; no recruitment-platform or arbitrary external browser request is allowed. Verify provider egress only through the configured allowlist and proxy.

- [ ] **Step 4: Run the tests and commit the harness slice.**

  ```powershell
  uv run pytest tests/test_interview_stories_smoke.py tests/test_interview_story_browser_harness.py -q
  git add src/offerpilot/cli.py src/offerpilot/smoke.py tests/test_interview_stories_smoke.py tests/test_interview_story_browser_harness.py scripts/interview-story-real-ai-browser-harness.ps1
  git commit -m "test: AI add interview story acceptance harness"
  ```

## Task 10: Run the complete release gate, record evidence, and perform independent review

**Files:** Create `docs/reports/2026-08-10-interview-story-library-release-verification.md`.

- [ ] **Step 1: Restore and validate the fixed implementation baseline before every diff-based gate.**

  Every PowerShell process that needs the baseline must read the stable file written in Task 0; it must not recalculate it from the current plan history:

  ```powershell
  Set-Location 'D:\Users\yuqi.chen\offerpilot\.worktrees\feat-20260810-interview-story-library'
  $baselineFile = Join-Path $env:TEMP 'offerpilot-interview-story-library-baseline.txt'
  $implementationBase = (Get-Content -LiteralPath $baselineFile -Raw).Trim()
  git cat-file -e "$implementationBase^{commit}"
  if ($LASTEXITCODE -ne 0) { throw 'Recorded implementation baseline is invalid' }
  ```

  Machine-check every changed path against the single Task 0 allowlist, rather than merely printing paths.  Read the allowlist from this plan into an explicit PowerShell array (one exact repository-relative path per item); normalize separators; reject any `git diff --name-only "$implementationBase..HEAD"` result not in the array.  The final report is the sole exception after it is created.  Reject any StoryUsage table/API/field, `knowledge/**` edit, `src/offerpilot/ai/tools.py` edit, unrelated ApplicationDetail redesign, or migration beyond `0019_interview_story_library` even if it is otherwise in a broad path prefix.

- [ ] **Step 2: Run changed-domain tests before independent review.**

  ```powershell
  uv run pytest tests/test_interview_stories_migrations.py tests/test_interview_stories_repository.py tests/test_interview_stories_ai.py tests/test_interview_stories_api.py tests/test_interview_stories_smoke.py tests/test_interview_story_browser_harness.py -q
  Set-Location web
  npx vitest run src/services/interviewStories.test.ts src/components/InterviewStoryLibraryView.test.tsx src/components/InterviewStoryLibraryView.interaction.test.tsx src/components/InterviewStoryDrawer.interaction.test.tsx src/layout/AppShell.interviewStories.test.tsx src/components/ChatPanel/InterviewStoryPilotEntry.test.tsx
  Set-Location ..
  uv run ruff check .
  uv run mypy src
  ```

  These are pre-review checks only. Do not create a final manifest, grouped result directory, real-AI output, screenshot, or release report until the independent review and any fixes are complete.

- [ ] **Step 3: Obtain an independent code review before freezing release evidence.**

  Start a separate subagent review under the repository’s code-review workflow after all product-code commits from Tasks 1–9 and Step 2 are green. The review must inspect the diff against the recorded baseline, migration ordering/coexistence, source-map restrictions, manual/proposal confirmation CAS, lease fencing, safe/unknown results, saved-review UI handoff, UI/Pilot draft isolation, CDP target sequence, zero Chat writes, and Story-only persistence. Fix every P0/P1 and rerun Step 2 after each fix; record only user-accepted P2 items with a rationale.

  Any code, test, script, dependency, or UI change after this review invalidates prior release artifacts. Start the complete gates in Steps 4–6 with fresh empty result directories and do not create the release report before this review is complete.

- [ ] **Step 4: Generate a fresh full pytest manifest and run the five supported backend groups.**

  `windows-pytest-groups.ps1` has no `-Collect` switch. Generate its `full-manifest.txt` from a fresh all-test collection, then invoke exactly its supported groups with the same mandatory `-ResultDir`.

  ```powershell
  $backendResultDir = Join-Path $env:TEMP 'offerpilot-interview-story-library-pytest'
  Remove-Item -LiteralPath $backendResultDir -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $backendResultDir | Out-Null
  $allCollect = @(& uv run pytest --collect-only -q --disable-warnings 2>&1)
  $allCollectExit = $LASTEXITCODE
  if ($allCollectExit -ne 0) { throw "full pytest collection failed: $allCollectExit" }
  $collectedNodeIds = @($allCollect | ForEach-Object {
      $line = ([string]$_).Trim()
      if ($line -match '^(tests[\\/].+::.+)$') { $Matches[1].Replace('/', '\\') }
  } | Where-Object { $_ })
  if ($collectedNodeIds.Count -eq 0) { throw 'full pytest collection returned no node ids' }
  $duplicateNodeIds = @($collectedNodeIds | Group-Object | Where-Object { $_.Count -gt 1 })
  if ($duplicateNodeIds.Count -gt 0) {
      throw "full pytest collection contains duplicate node ids: $($duplicateNodeIds.Name -join ', ')"
  }
  $allNodeIds = @($collectedNodeIds | Sort-Object)
  $allNodeIds | Set-Content -LiteralPath (Join-Path $backendResultDir 'full-manifest.txt') -Encoding utf8
  foreach ($group in @('agent', 'domain', 'knowledge', 'proposals', 'misc')) {
      & powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-pytest-groups.ps1 -Group $group -ResultDir $backendResultDir
      if ($LASTEXITCODE -ne 0) { throw "pytest group $group failed: $LASTEXITCODE" }
  }
  & powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-pytest-groups.ps1 -Aggregate -ResultDir $backendResultDir
  if ($LASTEXITCODE -ne 0) { throw "pytest aggregate failed: $LASTEXITCODE" }
  uv run ruff check .
  uv run mypy src
  ```

  Record the generated manifest hash and every group’s collected node IDs, exit code, pass/fail count, and aggregate result. Reject duplicate node IDs, any missing/extra node against the fresh manifest, stale marker, non-zero group exit, or a skip outside the four approved Windows symbolic-link permission node IDs and reasons.

- [ ] **Step 5: Generate a fresh full Vitest manifest, run all ten supported frontend groups, and run local verification.**

  The Vitest gate uses named groups and requires `-ResultDir`; it must not reuse an aggregate from another source fingerprint.

  ```powershell
  $frontendResultDir = Join-Path $env:TEMP 'offerpilot-interview-story-library-vitest'
  Remove-Item -LiteralPath $frontendResultDir -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $frontendResultDir | Out-Null
  & powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Collect -ResultDir $frontendResultDir
  if ($LASTEXITCODE -ne 0) { throw "Vitest manifest collection failed: $LASTEXITCODE" }
  foreach ($group in @('components-core', 'components-chat', 'components-interview', 'components-offer', 'components-support', 'features', 'layout', 'lib', 'services', 'theme')) {
      & powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Group $group -ResultDir $frontendResultDir
      if ($LASTEXITCODE -ne 0) { throw "Vitest group $group failed: $LASTEXITCODE" }
  }
  & powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Aggregate -ResultDir $frontendResultDir
  if ($LASTEXITCODE -ne 0) { throw "Vitest aggregate failed: $LASTEXITCODE" }
  Set-Location web
  npx tsc -b
  if ($LASTEXITCODE -ne 0) { throw "TypeScript project build failed: $LASTEXITCODE" }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "production build failed: $LASTEXITCODE" }
  Set-Location ..
  uv run oc smoke --static-dir web/dist
  uv run oc verify --profile local --static-dir web/dist
  uv run oc verify-interview-stories --profile local --static-dir web/dist
  ```

  Record the actual ten group names, files, test counts, marker/source fingerprint, each exit code, and aggregate status. Do not claim a JUnit artifact for the Vitest gate; it produces JSON/text/marker artifacts instead.

- [ ] **Step 6: Execute real-provider and real-browser acceptance after every local gate is green.**

  With the existing real configuration silently copied into temporary isolated data, run the Story-specific real-AI check before the full check:

  ```powershell
  uv run oc verify-interview-stories --profile real-ai --static-dir web/dist
  uv run oc verify --profile real-ai --static-dir web/dist
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\interview-story-real-ai-browser-harness.ps1
  ```

  The real browser operator must use the dedicated harness target only and capture the following light-mode, wide-screen screenshots after visually re-reading each image for clipping, blank space, or missing Chinese context:

  1. Interview module Story library list and the clear “新建故事” entry;
  2. source picker showing user-selected allowed sources and no preselection;
  3. frozen-source/confirmation preview before AI generation;
  4. generated safe structured draft with target-specific evidence expansion;
  5. human edit/selection and the final confirmation point;
  6. confirmed Story Version history after reopening;
  7. source-changed historical read-only notice;
  8. Pilot static entry before any chat write;
  9. Pilot explicit local request and source choice;
  10. Pilot draft/confirmation and its separate history result.

  Save them with these exact names in the externally supplied `$ScreenshotDirectory`:
  `01-story-library.png`, `02-source-picker.png`, `03-source-preview.png`,
  `04-generated-draft.png`, `05-confirmation.png`, `06-history.png`,
  `07-source-changed.png`, `08-pilot-entry.png`, `09-pilot-source-choice.png`,
  and `10-pilot-history.png`. The harness must reject a missing image, a width
  below 1440, a height below 900, or a stitched image above 1400px high; it
  writes a UTF-8 screenshot matrix containing only filename, dimensions,
  SHA-256, and an operator-required visual-review marker. The operator must
  visually verify light mode, Chinese context, no clipping, and no blank or
  squeezed panels before accepting that marker.

  The network audit must prove the UI and Pilot differ only by their explicit
  Story entrypoint: exactly one local UI proposal request and one local Pilot
  proposal request, distinct hashed idempotency keys and Attempts, their own
  confirmation/history sequence, and zero `/api/chat` or `/api/chat/confirm`
  writes. Provider egress must be mapped to the two persisted Story Attempts:
  each entrypoint has exactly one initial connection, plus its persisted
  `repair_count` (0 or 1), plus at most one browser-proven user replay. A
  second browser proposal request is accepted only after one captured
  `story_provider_error` response with the same hashed key, frozen-payload
  hash, and Attempt ID; a format-repair connection is accepted only when the
  exact persisted Attempt records the bounded repair. The audit must bind every
  browser record to the auditor-created target/session and map the resulting
  distinct confirmed Story/Version identities back to the UI and Pilot Attempt.

  Screenshots are evidence only after the CDP request sequence, target/session association, local-only browser allowlist, provider-egress allowlist, zero cross-domain writes, temporary process shutdown, and isolated data cleanup all pass.  A provider timeout, unavailable endpoint, contract failure, missing target sequence, or incomplete UI/Pilot branch is a failed or incomplete acceptance result, never a substitute API-smoke success.

- [ ] **Step 7: Write the release report only from observed results, then make the final documentation commit.**

  Create the ignored report with `git add -f` after every command above has completed.  It must contain the final commit tested; baseline; exact commands; start/end timestamps; per-group collection/pass/fail/skip/exit-code data; frontend source-fingerprint result; ruff/mypy/TypeScript-build/production-build/local results; Story-specific and full real-AI results; UI and Pilot CDP sequence status; screenshot paths/dimensions/SHA-256; temporary data/provider-proxy/browser cleanup status; and known Provider-stability risks.  It must not include secrets, config contents, Resume/JD/note/assertion text, model text, raw prompt/request/response bodies, raw Provider request IDs, or personal identifiers beyond the approved synthetic candidate name `筱哲`.

  Never turn an incomplete real-AI or CDP result into a pass.  If any release gate fails, update the report with the failure and stop without claiming release approval; retain the baseline file for repair/re-run.  If all gates pass:

  ```powershell
  git add -f docs/reports/2026-08-10-interview-story-library-release-verification.md
  git commit -m "docs: AI record interview story verification"
  ```

- [ ] **Step 8: Execute the final clean gate without changing reviewed evidence.**

  No code review occurs after the report commit: Step 3 review completion is a prerequisite for the fresh release gates and report. Only after the report accurately records successful results, run this final check. Preserve each command’s exit status before issuing another command; remove the baseline only at the end.

  ```powershell
  $baselineFile = Join-Path $env:TEMP 'offerpilot-interview-story-library-baseline.txt'
  $implementationBase = (Get-Content -LiteralPath $baselineFile -Raw).Trim()
  git diff --check "$implementationBase..HEAD"
  $diffCheckExit = $LASTEXITCODE
  if ($diffCheckExit -ne 0) { throw "git diff --check failed: $diffCheckExit" }
  $dirty = git status --porcelain
  if ($dirty) { throw 'Worktree is not clean after final verification' }
  Remove-Item -LiteralPath $baselineFile -Force
  ```

  Do not push, merge, rebase unrelated work, or remove any other worktree.  Final handoff must distinguish product-code changes, schema/data compatibility implications, accepted P2 items, unrun/failed gates, and externally induced Provider stability risk.
