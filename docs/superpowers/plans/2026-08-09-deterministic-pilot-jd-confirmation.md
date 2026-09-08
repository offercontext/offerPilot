# 实施计划：确定性 Pilot JD 确认

> For agentic workers: before implementing, read this plan end to end and execute every checkbox in order.

## Goal

将 Pilot 中“保存/更新当前投递 JD”从模型工具选择改为服务端确定性流程：快捷入口和严格限定的自然语言命令直接生成现有 save_application_jd_version 待确认动作；确认、拒绝、编辑、冲突恢复和回读复用现有 pending action、confirmation token、ApplicationJDService、幂等与 CAS。该流程从触发到终态的 Provider 调用次数必须为 0，普通聊天、其他 Pilot 写工具和下游 JD 版本消费保持不变。

计划已获批，当前按 TDD 执行。先把
docs/superpowers/specs/2026-08-09-deterministic-pilot-jd-confirmation-design.md
第 4 行状态改为“已复审通过”，再开始 TDD；本计划不授权推送或合并。

## Architecture

- 新增 src/offerpilot/ai/deterministic_actions.py：纯解析、严格命令识别、取消识别、状态决策和安全参数构造；不读 Repository、不调用 Provider、不写数据库。
- 修改 src/offerpilot/api.py：/api/chat 与 /api/chat/stream 在 Agent 前路由；确定性路径不加载 Chat Provider、不生成标题；普通消息继续原 Agent。
- 修改 src/offerpilot/ai/tools.py 与 src/offerpilot/ai/agent.py：Registry 保留完整 JD 工具，仅在给模型的工具列表过滤 model_visible=False。
- 修改两个 confirm endpoint：pending 工具为 save_application_jd_version 时使用共享确定性确认处理器；其他工具继续 Agent resume。
- 仅在确有需要时修改 src/offerpilot/repositories/chat.py，补充原子 CAS/替换，不增加迁移。
- 前端从 ApplicationDetail 进入已有 Chat API 的 public pilot_action；ProposalCard 展示 JD 专用确认信息，不直接调用 JD 保存 service。

## Tech Stack

Python/FastAPI/SQLAlchemy/pytest/Ruff/mypy；React/TypeScript/Ant Design/Vitest/jsdom；PowerShell CDP harness 与现有 SSE、ApplicationJDService、ChatRepository。

## 固定契约

- public action 只接受：

  ```json
  {"type":"application_jd_save","jdText":"可选原文","sourceUrl":null}
  ```

  客户端不得传 application_id、source_kind、expected_current_version_id、idempotency_key。
- Application 来自 context_type=application 与 context_ref，必须唯一可见；source_kind 固定为 pilot；当前版本、16–128 位 ASCII 幂等键和 tool_call_id 全由服务端生成。
- 严格命令仅识别保存/更新/补充/录入 + JD/岗位描述/职位描述/岗位资料，可带当前投递目标；正文必须由英文冒号、中文冒号或换行分隔，原文保留，不 normalize、不摘要、不清洗、不读取 URL。
- 收集 JD 时的取消短语是精确 allowlist：取消、算了、先不用、不用了、不保存、不要保存；只对 trim 后的完整字符串匹配，不做前缀/子串匹配，“不要保存 JD”等其他句子不属于取消短语。
- 大小口径固定为 UTF-8 bytes，不是 Unicode code point。继续复用现有 ApplicationJDService 的 MAX_JD_UTF8_BYTES=60_000；测试除 59,999/60,000/60,001 bytes 外，必须覆盖 60×1024-1、60×1024、60×1024+1 bytes，并记录这些值相对现有 60,000 cap 的预期结果，禁止静默改变既有 UI 保存上限。
- 否定、疑问、只读、引用、讨论、模糊表达和未分隔附加文本走普通 Agent；非法 action 返回 422，且不创建会话、不追加消息、不调用 Provider。
- 无正文使用现有 pending_clarification，固定追问“请粘贴完整岗位描述”；下一条非空消息整体作为 JD，取消清除 clarification。
- stale/idempotency conflict 创建新卡并重新人工确认；结果未知保留原卡/token/key 只允许原尝试重试；确认只允许编辑 jd_text/source_url。
- 确认、拒绝、成功说明不加载 Provider，不生成标题，不创建 Opportunity Fit、材料、面试、Knowledge、Offer 或 Application 状态写入。

## 文件 allowlist

实现阶段只允许：

```text
docs/superpowers/specs/2026-08-09-deterministic-pilot-jd-confirmation-design.md
docs/superpowers/plans/2026-08-09-deterministic-pilot-jd-confirmation.md
src/offerpilot/ai/deterministic_actions.py
src/offerpilot/ai/agent.py
src/offerpilot/ai/tools.py
src/offerpilot/api.py
src/offerpilot/repositories/chat.py
tests/test_deterministic_pilot_actions.py
tests/test_ai_agent.py
tests/test_ai_tools.py
tests/test_chat_repository.py
tests/test_chat_api.py
web/src/types/chat.ts
web/src/services/chat.ts
web/src/components/ApplicationDetail.tsx
web/src/components/ApplicationDetail.deterministicPilot.test.tsx
web/src/components/ChatPanel/index.tsx
web/src/components/ChatPanel/ProposalCard.tsx
web/src/components/ChatPanel/capabilities.ts
web/src/components/ChatPanel/deterministicPilotConfirmation.test.tsx
web/src/components/applicationPilotEntry.test.ts
web/src/layout/AppShell.tsx
scripts/application-jd-real-ai-browser-harness.ps1
scripts/application_jd_stage_diagnostic.py
tests/test_application_jd_browser_harness.py
tests/test_application_jd_stage_diagnostic.py
docs/reports/2026-08-05-application-jd-versions-release-verification.md
```

若确需 src/offerpilot/schemas.py 或其他现有类型文件，先停止、补充准确路径和理由，再继续；不得用目录通配符。禁止修改 Application JD 模型/迁移、Opportunity Fit、材料、面试、模拟面试、Provider 配置/fallback/证据校验、其他 Pilot 写工具或无关布局。

实施开始时执行一次 bootstrap PowerShell，创建固定 locator 文件持久化 gate 根目录、baseline 文件、allowlist 文件、worktree 根路径和 baseline SHA。`$approvedPaths` 必须由上方 allowlist 代码块的逐行字面量构成；同一数组既写入 `allowlist.txt`，也用于 committed、staged、unstaged、untracked 四类路径校验，不得通过 glob 或目录扫描扩展范围。locator 使用固定路径 `$env:TEMP\offerpilot-deterministic-pilot-jd-gate.locator.json`，不得依赖跨独立 PowerShell 调用传递的环境变量：

```powershell
$locatorPath = Join-Path $env:TEMP "offerpilot-deterministic-pilot-jd-gate.locator.json"
if (Test-Path -LiteralPath $locatorPath) { throw "stale gate locator exists: $locatorPath" }
$gateRoot = Join-Path $env:TEMP ("offerpilot-deterministic-pilot-gate-" + [guid]::NewGuid().ToString("N"))
$baselineFile = Join-Path $gateRoot "baseline.sha"
$allowlistFile = Join-Path $gateRoot "allowlist.txt"
$approvedPaths = @(
  "docs/superpowers/specs/2026-08-09-deterministic-pilot-jd-confirmation-design.md"
  "docs/superpowers/plans/2026-08-09-deterministic-pilot-jd-confirmation.md"
  "src/offerpilot/ai/deterministic_actions.py"
  "src/offerpilot/ai/agent.py"
  "src/offerpilot/ai/tools.py"
  "src/offerpilot/api.py"
  "src/offerpilot/repositories/chat.py"
  "tests/test_deterministic_pilot_actions.py"
  "tests/test_ai_agent.py"
  "tests/test_ai_tools.py"
  "tests/test_chat_repository.py"
  "tests/test_chat_api.py"
  "web/src/types/chat.ts"
  "web/src/services/chat.ts"
  "web/src/components/ApplicationDetail.tsx"
  "web/src/components/ApplicationDetail.deterministicPilot.test.tsx"
  "web/src/components/ChatPanel/index.tsx"
  "web/src/components/ChatPanel/ProposalCard.tsx"
  "web/src/components/ChatPanel/capabilities.ts"
  "web/src/components/ChatPanel/deterministicPilotConfirmation.test.tsx"
  "web/src/components/applicationPilotEntry.test.ts"
  "web/src/layout/AppShell.tsx"
  "scripts/application-jd-real-ai-browser-harness.ps1"
  "scripts/application_jd_stage_diagnostic.py"
  "tests/test_application_jd_browser_harness.py"
  "tests/test_application_jd_stage_diagnostic.py"
  "docs/reports/2026-08-05-application-jd-versions-release-verification.md"
)
$bootstrapSucceeded = $false
try {
  New-Item -ItemType Directory -Force -Path $gateRoot | Out-Null
  $worktreeStatus = @(git status --porcelain)
  if ($worktreeStatus.Count -ne 0) { throw "worktree is not clean at baseline capture" }
  $worktreeRoot = (git rev-parse --show-toplevel).Trim()
  $baseline = (git rev-parse --verify HEAD).Trim()
  if ($baseline -notmatch '^[0-9a-f]{40}$') { throw "baseline is not a full SHA: $baseline" }
  if ((git cat-file -t $baseline).Trim() -cne "commit") { throw "baseline is not a commit: $baseline" }
  Set-Content -LiteralPath $baselineFile -Value $baseline -Encoding ascii -NoNewline
  Set-Content -LiteralPath $allowlistFile -Value $approvedPaths -Encoding ascii
  $allowlistHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $allowlistFile).Hash.ToLowerInvariant()
  [ordered]@{
    locator_version = 1
    worktree_root = $worktreeRoot
    gate_root = $gateRoot
    baseline_file = $baselineFile
    allowlist_file = $allowlistFile
    baseline_sha = $baseline
    allowlist_sha256 = $allowlistHash
  } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $locatorPath -Encoding utf8 -NoNewline
  $bootstrapSucceeded = $true
}
finally {
  if (-not $bootstrapSucceeded) {
    if (Test-Path -LiteralPath $gateRoot) { Remove-Item -LiteralPath $gateRoot -Recurse -Force }
    if (Test-Path -LiteralPath $locatorPath) { Remove-Item -LiteralPath $locatorPath -Force }
  }
}
```

实现、提交、测试和浏览器验收分别运行在独立 PowerShell 调用时，每次先读取该 locator，验证 worktree 根路径一致、baseline 文件原文仍严格等于 locator 中的 baseline SHA、allowlist SHA-256 未变，并运行 `git cat-file -t $baseline` 确认该 SHA 仍可解析为 commit；不得要求 baseline 等于当前 HEAD。随后由本次调用临时设置 OFFERPILOT_APPLICATION_JD_BASELINE_FILE 和 OFFERPILOT_APPLICATION_JD_ALLOWLIST_FILE，供其子进程继承。locator、gateRoot 和 baseline 在最终报告提交后的复核完成前不得删除；失败保留脱敏诊断供恢复，成功清理时必须同时删除 locator/gateRoot 并用 Test-Path 验证不存在。

每个独立调用加载状态的固定骨架如下；环境变量只在本次调用内设置，不能作为跨调用状态：

```powershell
$locatorPath = Join-Path $env:TEMP "offerpilot-deterministic-pilot-jd-gate.locator.json"
$oldBaselineEnv = $env:OFFERPILOT_APPLICATION_JD_BASELINE_FILE
$oldAllowlistEnv = $env:OFFERPILOT_APPLICATION_JD_ALLOWLIST_FILE
try {
  $locator = Get-Content -Raw -LiteralPath $locatorPath | ConvertFrom-Json
  $gateRoot = $locator.gate_root
  $baselineFile = $locator.baseline_file
  $allowlistFile = $locator.allowlist_file
  if ((git rev-parse --show-toplevel).Trim() -cne $locator.worktree_root) { throw "worktree locator mismatch" }
  $baseline = Get-Content -Raw -LiteralPath $baselineFile
  if ($baseline -cne $locator.baseline_sha) { throw "baseline file changed" }
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $allowlistFile).Hash.ToLowerInvariant() -cne $locator.allowlist_sha256) { throw "allowlist file changed" }
  if ((git cat-file -t $baseline).Trim() -cne "commit") { throw "baseline commit cannot be resolved" }
  $env:OFFERPILOT_APPLICATION_JD_BASELINE_FILE = $baselineFile
  $env:OFFERPILOT_APPLICATION_JD_ALLOWLIST_FILE = $allowlistFile
  # 在此执行本次实现、测试或门禁调用
}
finally {
  $env:OFFERPILOT_APPLICATION_JD_BASELINE_FILE = $oldBaselineEnv
  $env:OFFERPILOT_APPLICATION_JD_ALLOWLIST_FILE = $oldAllowlistEnv
}
```

每次范围门禁合并以下四类文件状态并与 allowlist 比较：

```powershell
$baselineRaw = Get-Content -Raw $baselineFile
if ($baselineRaw -cne $locator.baseline_sha) { throw "baseline file changed" }
$baseline = $baselineRaw
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $allowlistFile).Hash.ToLowerInvariant() -cne $locator.allowlist_sha256) { throw "allowlist file changed" }
if ((git cat-file -t $baseline).Trim() -cne "commit") { throw "baseline is not a resolvable commit: $baseline" }
git diff --name-only "$baseline..HEAD"
git diff --name-only
git diff --cached --name-only
git ls-files --others --exclude-standard
```

命令失败、locator/baseline/allowlist 读取或校验失败、越界、baseline 文件改变或并行分支交集非零时停止；报告只记录 baseline SHA 和允许路径，不记录临时目录原文。只有报告提交后的最终范围复核、`git diff --check` 和工作区检查全部通过后，才清理 locator、gateRoot 和临时文件。

## TDD 规则

每个切片严格执行：写最小失败测试；运行并确认因功能缺失失败；写最小生产实现；定向测试转绿；再重构。不得先写生产代码。每个完成切片单独提交，标题必须使用具体的 conventional commit，例如 `test: AI define deterministic Pilot JD router` 或 `feat: AI add deterministic Pilot JD router`，不得保留占位符。

## 1. 纯逻辑 Red/Green

文件：tests/test_deterministic_pilot_actions.py（新增），src/offerpilot/ai/deterministic_actions.py（随后新增）。

- [ ] 先写失败测试：action 运行时校验（合法、缺失、空值、未知、额外服务端字段、非对象）、严格命令正例、正文冒号/换行、CJK/emoji/换行/前后空白原样保留。
- [ ] 写负向参数化测试：否定、疑问、查看/总结/分析、引用、无分隔附加文本；断言 normal_agent。
- [ ] 写边界测试：空白/0 字、UTF-8 bytes 的 59,999/60,000/60,001，以及 60×1024-1/60×1024/60×1024+1；分别使用 ASCII、CJK、emoji 证明按字节而非 code point 判定；同时测 URL 仅文本和提示注入文本不执行，注入 id/key factory 断言 key 为 16–128 位 ASCII。
- [ ] 先运行 uv run pytest tests/test_deterministic_pilot_actions.py -q，确认因模块/函数缺失而失败。
- [ ] 实现不可变结果类型和纯函数 parse_pilot_action、match_application_jd_command、is_pilot_cancel_message、decide_pilot_action、build_pilot_pending_action；只接收显式上下文/依赖，绝不访问网络或 Repository。
- [ ] 定向测试转绿后运行 uv run pytest tests/test_deterministic_pilot_actions.py tests/test_ai_agent.py -q，并提交 test: AI define deterministic Pilot JD router 与 feat: AI add deterministic Pilot JD router。

## 2. 模型工具可见性

先测 tests/test_ai_tools.py、tests/test_ai_agent.py。

- [ ] 写失败测试：完整 Registry 仍含 save_application_jd_version，Schema、always_confirm、validator、describe、handler、source_kind=pilot 不变，并标记 model_visible=False。
- [ ] 写失败测试：捕获模型 tools，确认只排除 save_application_jd_version，读工具和其他写工具不变；内部 Registry handler 仍可验证/执行。
- [ ] 先运行定向测试确认缺少过滤逻辑；再改 src/offerpilot/ai/tools.py 和 src/offerpilot/ai/agent.py 的工具列表构造；运行 uv run pytest tests/test_ai_tools.py tests/test_ai_agent.py -q。

## 3. Repository 原子操作

先测 tests/test_chat_repository.py。

- [ ] 写失败测试：确定性 pending/clarification 复用现有字段；归档不能写；pending、助手消息和固定终态消息按事务提交。
- [ ] 写失败测试：冲突后仅在原 tool_call_id + tool_name + args 仍匹配时原子替换新 token/key/expected version；CAS 失败保留原卡。
- [ ] 写失败测试：同 token 并发最多一个成功；结果未知不清卡；清卡后重复确认只能回读/返回 stale，不能新增版本。
- [ ] 先运行 uv run pytest tests/test_chat_repository.py -q 确认 Red，再只修改 src/offerpilot/repositories/chat.py 中必要的 CAS 方法；运行 uv run pytest tests/test_chat_repository.py tests/test_chat_api.py -q。

## 4. Chat Router

先测 tests/test_chat_api.py，参数化 /api/chat 与 /api/chat/stream。

- [ ] 写失败测试：显式 pilot_action + JD 在唯一 Application 上下文返回 confirmation_required；args 含服务端 application/current version/source/key，固定文案；fake chat/title model 调用数为 0。
- [ ] 写失败测试：新会话不生成标题；SSE 只有固定 meta、user_message_saved、status、confirmation_required、completed，无 model delta/tool-call。
- [ ] 写失败测试：无 JD 只生成一次固定追问并保存 clarification；下一条消息整体作为 JD；取消清除；快捷入口不覆盖 collecting。
- [ ] 写失败测试：已有同卡返回原卡，其他 pending 不覆盖；归档/删除/错误 Application 固定失败；非法 action 422 且会话、消息、Provider 均不变；普通旧请求仍走 Agent。
- [ ] 先运行 uv run pytest tests/test_chat_api.py -q 确认 Red。
- [ ] 修改 src/offerpilot/api.py，在加载 _chat_model、标题任务、run_turn 前执行共享 Router；两个 endpoint 共享同一 deterministic response builder，普通路径不改。
- [ ] 运行 uv run pytest tests/test_chat_api.py -q，再运行 uv run pytest tests/test_chat_api.py tests/test_deterministic_pilot_actions.py -q。

## 5. 确定性确认与冲突

先测 tests/test_chat_api.py，必要时补 tests/test_chat_repository.py。

- [ ] 参数化两个 confirm endpoint：没有 AI 配置也能调用现有 JD handler；只创建一个 source_kind=pilot 版本；固定成功消息；Provider/title 调用均为 0。
- [ ] 测 token 错、非法字段、空白/超限 JD、非法 URL 保留原卡；合法编辑只改变 jd_text/source_url；服务端字段不可改。
- [ ] 测拒绝、重复确认、并发确认、响应丢失重试；stale 和 idempotency conflict 保留原文生成新卡并要求重新确认；未知异常保留原 token/key。
- [ ] 增加关键事务边界回归：让 ApplicationJDService.create_version 已提交一个 pilot 版本后，故意让 Chat pending 清理/终态消息 CAS 返回失败，并明确拆分两条恢复路径。原 pending 仍存在且 token/key 匹配时，使用原 token/key 和原幂等键重试，幂等回读同一版本 ID，不新增版本；pending 已被新卡替换时，保留新卡及其状态，不恢复/覆盖旧卡，改由 Application 当前版本/历史回读定位已提交版本并报告已恢复，不再依赖丢失的旧 token/key，也不新增版本。
- [ ] 在该写后 CAS 失败场景中分别断言：原 pending 仍存在时批准成功清除匹配的旧 last_write_undo，拒绝不调用 JD handler 且保留旧 undo；pending 已替换时新 pending 完整保留，只有原 pending 字段匹配时才允许清理；两条路径均不产生第二个 Provider/版本写入。
- [ ] 测确认后历史回读、跨领域写入为 0、source URL 不外联。
- [ ] 增加升级兼容回归：手工种入升级前模型生成的 JD pending action、等待原文 pending clarification；在 Agent checkpoint 缺失、无 AI 配置时分别确认、拒绝和恢复。首次确认必须恰好创建 1 个版本，拒绝必须创建 0 个版本；首次确认已提交后的响应恢复/重放必须新增 0 个版本（总数仍为 1），并复用同一 token/key。
- [ ] 先确认现有端点在模型前加载 Provider 的测试失败，再在 src/offerpilot/api.py 增加共享 deterministic JD confirmation helper；先 token/锁/CAS/编辑验证，再调用完整 Registry validator/handler；流式只包 SSE。
- [ ] 只对 pending.tool_name == save_application_jd_version 走 helper，其他工具保持 Agent resume；运行 uv run pytest tests/test_chat_api.py tests/test_chat_repository.py tests/test_ai_tools.py -q。

## 6. 前端动作与确认卡

先测新增 web/src/components/ApplicationDetail.deterministicPilot.test.tsx、web/src/components/ChatPanel/deterministicPilotConfirmation.test.tsx，并扩展 web/src/components/applicationPilotEntry.test.ts、必要的 web/src/components/ChatPanel/layout.test.ts。

- [ ] 挂载测无当前 JD 显示“保存岗位资料”、有当前 JD 显示“更新岗位资料”；点击只发 Pilot callback，不调用 saveApplicationJdVersion、不导航、不写 JD。
- [ ] 挂载 ChatPanel 测 startRequest 发送 Chat endpoint + pilot_action，保留 Application 上下文；确认卡前后 AI/JD/navigation spies 为 0。
- [ ] 挂载 ProposalCard 测公司、岗位、版本、JD 原文预览、字符数、Pilot 来源、“不会访问链接”、仅 jd_text/source_url 可编辑、拒绝/确认/结果未知重试；普通 pending 卡不回归。
- [ ] 先运行 cd web; npm test -- --run src/components/ApplicationDetail.deterministicPilot.test.tsx src/components/ChatPanel/deterministicPilotConfirmation.test.tsx src/components/applicationPilotEntry.test.ts src/components/ChatPanel/layout.test.ts 确认 Red。
- [ ] 修改 web/src/types/chat.ts、web/src/services/chat.ts、web/src/components/ApplicationDetail.tsx、web/src/layout/AppShell.tsx、web/src/components/ChatPanel/index.tsx、web/src/components/ChatPanel/capabilities.ts、web/src/components/ChatPanel/ProposalCard.tsx；快捷入口只发送 action，不调用 JD save service，不自动确认。
- [ ] 运行定向前端测试，再运行 cd web; npm test -- --run。

## 7. Harness、浏览器与报告

文件：scripts/application-jd-real-ai-browser-harness.ps1、scripts/application_jd_stage_diagnostic.py、tests/test_application_jd_browser_harness.py、tests/test_application_jd_stage_diagnostic.py、docs/reports/2026-08-05-application-jd-versions-release-verification.md。

- [ ] 先写失败断言：Stage A 观察 UI JD v1、Pilot 卡、jd_version_id、source_kind=pilot、历史 v1/v2；Pilot/title Provider=0；Stage A 未完成不得进入 Triage。
- [ ] 写失败断言：临时隔离目录、中文合成数据、亮色宽屏；网络只允许本地静态资源、本地 /api 和审计中的下游 Provider；失败也清理进程树、端口、目录、环境变量。
- [ ] 写失败断言：Stage B 所有 Triage/Material Kit/Interview Preparation 均使用确认后的 v2，失败不回滚 v2。
- [ ] 先运行 uv run pytest tests/test_application_jd_browser_harness.py tests/test_application_jd_stage_diagnostic.py -q 确认 Red。
- [ ] 修改脚本为统一 try/finally harness，递归停止本次服务/浏览器进程树，等待端口释放，恢复环境变量；诊断只保存状态、计数、版本、来源、哈希、错误码，不保存 JD/简历/提示词/原文/key。
- [ ] 临时隔离环境只执行一次：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\application-jd-real-ai-browser-harness.ps1 -Stage all
```

  顺序固定为：UI JD v1 → Pilot 入口 → 中文 JD → 确认卡（Provider=0）→ 人工确认 → v2 历史回读（Provider 仍为 0）→ Triage → 材料包 → 面试准备。失败保留脱敏报告并停止，不扩大重试或修改 Provider/证据契约。前端改动时提供亮色中文宽屏截图，覆盖快捷入口、等待原文、确认卡、成功历史，合并前交用户确认。

## 8. 最终门禁与收口

- [ ] 所有最终门禁进程先从固定 locator 解析同一 gateRoot；每个独立 PowerShell 调用各自使用 try/finally 恢复本次环境并清理本次服务/浏览器/端口，但在报告提交后的最终复核前保留 locator、gateRoot、baseline 和门禁结果目录。任一命令失败都保留脱敏失败摘要并 fail-closed。
- [ ] 在生成最终 manifest 和运行最终门禁前，按 requesting-code-review 发起独立代码复审；复审发现的问题先修复并提交，随后重新执行 locator 范围校验。任何修复导致源码或测试变化，都必须从 manifest 生成开始重新执行本节全部门禁，不得复用旧结果。
- [ ] 生成后端 full-manifest.txt：运行 uv run pytest --collect-only -q --disable-warnings tests，提取全部 tests/*:: node id，先检查重复再写文件；不能用 Sort-Object -Unique 掩盖重复。
- [ ] 使用现有 scripts/windows-pytest-groups.ps1 逐组运行 agent、domain、knowledge、proposals、misc，再用同一 ResultDir -Aggregate；不得以单次 pytest 代替。期待每组 collect/junit/complete marker 均存在，manifest、源码 fingerprint、结果 hash 和 marker hash 均匹配，无重复 node ID，仅允许脚本内固定的 4 个 skip，aggregate coverage 与 full-manifest 完全相等。
- [ ] 组门禁命令固定为：

```powershell
$pytestResultDir = Join-Path $gateRoot "pytest"
$pytestGroups = @("agent", "domain", "knowledge", "proposals", "misc")
foreach ($group in $pytestGroups) {
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-pytest-groups.ps1 -Group $group -ResultDir $pytestResultDir
}
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-pytest-groups.ps1 -Aggregate -ResultDir $pytestResultDir
```
- [ ] 使用现有 scripts/windows-vitest-groups.ps1 先 -Collect 生成 frontend-manifest.json，再逐组运行 components-core、components-chat、components-interview、components-offer、components-support、features、layout、lib、services、theme，最后 -Aggregate；不得手写或复用旧 manifest。
- [ ] 前端组门禁命令固定为：

```powershell
$frontendResultDir = Join-Path $gateRoot "vitest"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Collect -ResultDir $frontendResultDir
$frontendGroups = @("components-core", "components-chat", "components-interview", "components-offer", "components-support", "features", "layout", "lib", "services", "theme")
foreach ($group in $frontendGroups) {
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Group $group -ResultDir $frontendResultDir
}
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-vitest-groups.ps1 -Aggregate -ResultDir $frontendResultDir
```
- [ ] 校验前端 manifest 的 test-file 集合、fingerprint-file 集合、file_count、source_hash；每组 marker 必须匹配 manifest/source/result hash，测试 ID 不重复，numPendingTests/numTodoTests 必须为 0，aggregate 必须覆盖每个文件恰好一次。
- [ ] 分组 aggregate 作为完整后端测试门禁；不再强制运行已知会长期超时的单进程全量 pytest。分组门禁之后运行 uv run ruff check .、uv run mypy src；前端运行 cd web; npm.cmd test、npm.cmd run build；任一分组结果、aggregate 或静态检查失败都 fail-closed。
- [ ] 在 gateRoot 内用 TcpListener 申请并释放一个 loopback freePort，再执行现有 Windows 发布脚本的本地路径：powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-smoke.ps1 -Port $freePort，随后执行 uv run oc smoke --static-dir web/dist 和 uv run oc verify --profile local --static-dir web/dist；local smoke、local verify 任何一个未跑或失败都不能收口。
- [ ] 在已明确提供真实 AI 配置且得到授权的同一隔离环境执行 uv run oc verify --profile real-ai --static-dir web/dist；不把缺少配置、跳过或受控 Provider 结果记为 real-AI 通过。
- [ ] 在报告提交前运行 git diff --check；合并 committed、staged、unstaged、untracked 四类文件状态与 locator 中的 baselineFile，证明无模型/迁移、Provider 配置、证据契约、无关服务层或其他领域改动；记录当时 HEAD，但不要求其等于 baseline。
- [ ] 发布报告在 ignored docs/reports 路径只保存脱敏事实、baseline SHA、allowlist、门禁命令、manifest/source/aggregate 哈希和结果；先 git add -f docs/reports/2026-08-05-application-jd-versions-release-verification.md，单独提交报告，再用 git show --name-only HEAD 复核报告确实进入提交且没有密钥/JD/简历/模型原文。
- [ ] 报告提交后重新读取固定 locator，复跑范围门禁（committed、staged、unstaged、untracked）、git diff --check 和 git status --short --branch；确认 baseline 文件未变且 baseline SHA 仍由 git cat-file 解析、报告提交路径在 allowlist 内、工作区干净后，才在 finally 中删除 locator、gateRoot 和结果目录并用 Test-Path 验证清理完成。
- [ ] 每个阶段单独提交，标题必须使用具体的 conventional commit，例如 `test: AI define deterministic Pilot JD router` 或 `feat: AI add deterministic Pilot JD router`；本计划不授权推送或合并。

## 完成定义

必须有证据证明确定性 Pilot JD 保存从触发到终态 Provider 调用为 0；版本、来源、幂等、CAS、stale、结果未知、并发和旧卡兼容；普通 Chat/其他工具不回归；Stage A/Stage B、前后端门禁、截图和脱敏报告全部通过，工作区干净。
