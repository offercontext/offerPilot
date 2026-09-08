# Tool Execution Pipeline 设计

日期：2026-08-18

状态：已复审通过

分支：`feat/20260818-tool-execution-pipeline`

固定基线：`30c944f3bda1d99b303f8e9875a170a552f79af7`

## 1. 背景与问题

OfferPilot 当前的 Chat/LangGraph 工具运行时以大型字典 registry 为中心。Provider 契约、参数 Schema、读写属性、确认描述、validator 和字符串 handler 混合在同一结构中，Agent、API、SSE 与确认恢复又分别解析这些字典和 `"错误：..."` 字符串。

这套结构已经具备成熟的业务行为，包括：

- LangGraph 多轮模型调用；
- 写工具 HITL、Pending Action、编辑确认与拒绝；
- checkpoint 与无 checkpoint 恢复；
- 业务 Repository 的归属、revision、CAS 和幂等保护；
- 普通 HTTP、SSE 和确认恢复事件；
- 第一阶段 Durable Execution Journal。

但执行协议缺少明确边界：

- Provider Schema 与内部执行元数据耦合；
- handler 以字符串同时表达数据、错误和控制状态；
- Agent 和 API 通过错误前缀反推成功或失败；
- capability、binding、确认策略和异常分类没有统一契约；
- 同一工具的验证、确认、恢复和执行分散在多个分支；
- 新旧路径可能在重构时被隐式混用。

本项目是 Harness 重构第二阶段。它以**内部破坏性重构、外部契约严格兼容**的方式，把 25 个模型可见工具一次性迁移到 Typed Catalog 与 Tool Execution Pipeline，并把 3 个模型不可见的确定性工具隔离在明确的 Legacy Adapter 中。

本阶段替换内部执行机制，但不接管业务真值，不改变 Provider、HTTP/SSE、HITL、Pending Action、CAS、幂等或领域写入语义。

## 2. 目标与非目标

### 2.1 目标

1. 建立唯一的 `ProviderToolContract`，精确保留现有 Provider 工具契约。
2. 建立泛型 `ToolSpec[ArgsT, ResultT]` 和封闭的 25 工具 Typed Catalog。
3. 统一 JSON Schema 校验、无损 typed decode、capability、binding audit、确认策略和异常映射。
4. 建立 `prepare_call()` 与 `execute_prepared()` 两阶段 Pipeline。
5. Pipeline 内部只使用类型化 `ToolOutcome`，消除 25 个工具的字符串控制流。
6. 通过兼容 renderer 保持模型、HTTP、SSE 和确认恢复可见字符串不变。
7. 保持模型调用次数、工具调用次数、消息顺序和业务副作用等价。
8. 严格沿用第一期 Journal Event Schema，并保持 Journal fail-open。
9. 对 Provider payload、工具结果、确认恢复、SSE 和 checkpoint 建立机械化兼容门禁。
10. 明确隔离 3 个 deterministic legacy 工具，防止它们被 Provider 暴露或被模型 dispatcher 调用。

### 2.2 非目标

本阶段明确不实现：

- Write Operation Ledger 或跨请求 exactly-once；
- 新的领域事务、Unit of Work 或统一 Repository 外部 Session；
- Context Projector、token budget 或 context-scoped Tool Catalog；
- 更严格的实体隔离、ID 自动预绑定或跨记录操作限制；
- 新的用户可见错误协议；
- SSE reconnect/replay；
- 新的 Pending Action 持久化模型；
- UI 或公开调试接口；
- 多 Agent、插件系统、任意 Bash/Python 或自主浏览器执行；
- 3 个 deterministic legacy 工具的正式 typed migration。

## 3. 方案选择

### 3.1 采用：破坏性 Typed Catalog 切换，外部契约严格兼容

25 个模型可见工具一次性切换到新的 Provider Contract、Typed Catalog 和 Execution Pipeline。旧 registry、旧 handler 协议和重复执行分支同步删除。

对外保持：

- Provider 工具名称、描述、顺序和完整参数 Schema；
- ToolMessage、HTTP 和 SSE 可见内容；
- HITL、Pending Action、编辑确认、拒绝、CAS 和恢复；
- Provider 与工具调用次数；
- Repository 写入、副作用和现有归属检查。

内部允许：

- 删除旧字典 registry；
- 修改 runner、Agent、API adapter 和测试接口；
- 重写 25 个 handler 为 typed executor；
- 删除内部旧兼容层，不保留长期 façade。

### 3.2 不采用：在旧 registry 外增加 façade

该方案改动较小，但 legacy string、字典 handler 和重复执行路径仍会成为事实上的核心协议，无法证明所有请求已经切换到新 Pipeline。

### 3.3 不采用：从 Pydantic 或 Args 类型生成 Provider Schema

该方案表面整洁，但会改变 Provider Schema 的字段、默认值、约束或顺序，并可能让 typed decoder 额外拒绝旧契约允许的输入。Provider 契约必须独立保存，不从内部类型反向生成。

## 4. 固定工具范围

### 4.1 25 个模型可见 Typed 工具

Provider 顺序固定为：

1. `list_applications`
2. `get_application`
3. `create_application`
4. `update_application_status`
5. `list_application_events`
6. `get_application_event`
7. `create_application_event`
8. `update_application_event`
9. `delete_application_event`
10. `list_notes`
11. `add_note`
12. `update_note`
13. `delete_note`
14. `list_offers`
15. `get_offer`
16. `compare_offers`
17. `update_offer`
18. `save_offer_assessment`
19. `list_resumes`
20. `get_resume`
21. `resume_update_career_intent`
22. `resume_rewrite_highlight`
23. `list_resume_matches`
24. `list_jd_analyses`
25. `get_jd_analysis`

首批必须完整迁移这 25 个工具，不允许按默认规则静默归类新增工具。

### 4.2 3 个确定性 Legacy 工具

以下模型不可见工具是唯一允许的 Legacy Adapter：

- `save_application_jd_version`
- `create_application_submission_snapshot`
- `record_application_outcome`

它们继续保持当前专用的：

- 服务端确定性创建入口；
- editable fields 与确认描述；
- 参数验证；
- Pending Action；
- 幂等键；
- revision/CAS；
- 写入与恢复流程。

它们不得进入 Provider Schema、Typed Catalog 或模型 dispatcher。后续单独设计它们的迁移，本首批不宣称它们已经类型化。

机器门禁必须精确证明：

```text
25 migrated typed tools + 3 legacy deterministic tools
```

任何第 29 个工具、遗漏、重复或未分类工具都导致 Catalog 初始化或测试失败。

## 5. 总体架构

```text
ProviderToolContract Catalog
        │  25 个，顺序固定
        ▼
Typed Tool Catalog
        │  ToolSpec[ArgsT, ResultT]
        ▼
Tool Execution Pipeline
  1. resolve / parse / Schema validation
  2. lossless typed decode
  3. capability gate
  4. binding audit
  5. read-only preflight
  6. existing HITL / Pending Action
  7. mutable precondition recheck
  8. write only: confirmation claim / CAS
  9. write only: create and verify ExecutionAuthorization
 10. tool.started
 11. executor at most once per call
 12. ToolOutcome
        ├── Compatibility Renderer
        ├── Transport Projector
        └── Frozen Journal Projector
```

### 5.1 ProviderToolContract

保存 Provider 最终可见的完整 envelope。它是唯一 Provider 契约来源，不接受 Pydantic Schema 或运行时推导结果。

### 5.2 ToolSpec

每个 `ToolSpec[ArgsT, ResultT]` 一对一引用一个 Provider contract，并声明：

- typed Args 视图；
- read/write kind；
- capabilities；
- binding resolvers；
- confirmation policy；
- editable fields 与确认摘要；
- read-only preflight；
- mutable precondition validator；
- typed executor；
- 封闭的领域异常映射；
- typed result 的兼容渲染规则；
- transport projection 规则。

Typed Spec 不允许保存 legacy 字符串 handler，也不允许返回字符串作为控制协议。

### 5.3 ToolExecutionContext

由 runner/runtime 注入，包含当前请求需要的：

- Repository 和 service 依赖；
- conversation scope；
- application/offer/interview bindings；
- 当前 capabilities；
- 现有 confirmation claimer/CAS adapter；
- 第一阶段 RunRecorder。

它不进入 Graph State、checkpoint、ChatMessage 或 Pending Action。

### 5.4 ToolExecutionRecord

在当前调用栈中短暂组合：

- `PreparedToolCall`；
- `ToolOutcome`；
- compatibility string；
- transport projection 所需的 typed result；
- 安全控制字段。

它是不可持久化的瞬态对象。typed result、兼容文本和其他潜在内容字段使用 `repr=False`，禁止通用序列化、结构化日志和 checkpoint 写入。

## 6. Provider 契约与 Golden Manifest

### 6.1 完整 envelope

`ProviderToolContract` 保存当前 Provider request 中的全部可见字段，例如：

```json
{
  "type": "function",
  "function": {
    "name": "list_applications",
    "description": "...",
    "parameters": {}
  }
}
```

如果基线存在 `strict` 或其他 envelope 字段，也必须原样保存。缺失字段不得补默认值，存在字段不得删除。capability、executor 或 confirmation 等内部元数据不得混入 Provider payload。

### 6.2 Golden 捕获与比较

Golden manifest 必须从固定基线 `30c944f` 的最终 Provider adapter 输出独立捕获并提交。它不是由新 Catalog 生成的自校验文件。

资产要求：

- 只保存纯合成数据；
- 只保存 canonical JSON 投影；
- 不保存 SQLite 文件；
- 不保存真实用户内容、密钥或不稳定时间字段；
- 测试只能读取，不能自动生成、覆盖或接受新 manifest。

比较规则：

- 工具列表顺序严格相同；
- 数组顺序严格相同；
- 对象键顺序忽略；
- 不忽略任何字段、约束或字段存在性；
- 字符串、布尔值、null 和数字严格比较。

Provider 边界测试通过 spy 捕获实际 request adapter 组装的完整 payload，不访问真实网络，并与只读 golden 比较，防止 Catalog 正确但最终组装漂移。

### 6.3 Canonical JSON 与 fingerprint

Canonical bytes 固定为：

```python
json.dumps(
    value,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
```

规则：

- 不做 Unicode normalization；
- 禁止非有限数字；
- 对象键排序；
- 数组顺序不变；
- 不保证保留数字原始词法形式，只保证 JSON 语义和值不变。

每个 `function.parameters` 计算 `sha256:<64 lowercase hex>`。fingerprint 只用于构建和测试，不写入第一期 Journal Event。

## 7. 参数解析、Schema 与 typed decode

### 7.1 固定实现

项目增加直接依赖：

```text
jsonschema==4.26.0
```

同时固定在 `pyproject.toml` 与 `uv.lock`。运行时使用：

```text
jsonschema.validators.Draft202012Validator
```

Catalog 初始化时：

1. 递归检查全部 Schema；
2. 同时检查 `$ref` 与 `$dynamicRef`；
3. 两种引用都只允许以 `#` 开头的本地 fragment reference；
4. 远程 URI、外部相对路径和其他非本地引用全部导致初始化失败；
5. 调用 `Draft202012Validator.check_schema()`；
6. 使用显式禁止 retrieval 的 `referencing.Registry` 预编译每个 validator；
7. 不配置 `FormatChecker`；
8. retrieval callback 只能抛出固定初始化异常，不得访问文件或网络。

Schema 门禁测试必须分别注入外部 `$ref` 和 `$dynamicRef`，断言 Catalog 初始化失败，并通过网络 spy 证明 HTTP、DNS 和其他 retrieval 调用均为 0 次。

内部 Schema 非法是应用初始化错误，不得在运行时伪装成用户参数错误，也不得回退旧路径。

### 7.2 JSON parser 边界

输入是 Provider `arguments` 字符串，解析顺序固定为：

```text
raw arguments
  → JSON lexical parse
  → duplicate key / finite number checks
  → top-level object check
  → Provider JSON Schema validation
  → lossless typed decode
```

Parser 必须拒绝：

- 非法 JSON；
- 顶层非 object；
- 任意层级重复对象键；
- `NaN`；
- `Infinity`；
- `-Infinity`；
- 数字溢出后形成的其他非有限值。

稳定 code 固定为：

- `invalid_json`
- `arguments_not_object`
- `duplicate_argument_key`
- `non_finite_number`
- `schema_validation_failed`
- `unknown_tool`

这些错误均为内部 `validation_error`，最终使用 compatibility renderer 输出基线可见错误文本。

### 7.3 Typed Args 无损边界

Typed Args 使用与 JSON 原值一致的 `TypedDict` 和 JSON primitive 表示。Schema 校验是唯一公共输入约束，decoder 只负责：

- 递归复制到新的 dict/list；
- 建立静态 typed view；
- 保留 Schema 允许的全部额外字段；
- 不原地修改输入；
- 不 trim、不填默认值、不强制转换；
- 不把 ID、enum、datetime 或数字转换为额外拒绝输入的领域类型。

Schema 合法但 decoder 失败属于实现缺陷，映射为 `internal_error`。Property/contract tests 必须证明 decoder 对 Schema 合法样本是 total 的。

## 8. Catalog、Capability 与 Binding

### 8.1 Catalog 封闭校验

Typed Catalog 初始化必须验证：

- 恰好 25 个 Spec；
- 名称和 Provider contract 一对一；
- 顺序与 golden 一致；
- read/write kind、confirmation policy、capability 和异常映射完整；
- 无重复、遗漏或未知工具；
- 3 个 Legacy 工具未进入 Typed Catalog。

未知模型工具名立即返回：

```text
category = validation_error
code = unknown_tool
```

并保持基线可见错误文本。unknown tool 不执行 capability、binding、Repository、preflight 或 executor，也不得尝试 Legacy Catalog。

### 8.2 Capability gate

Capabilities 按当前领域边界声明：

- applications read/write；
- application events read/write；
- notes read/write；
- offers read/write；
- resumes read/write；
- JD analyses read。

跨领域工具显式声明组合能力，例如创建 application event 需要 application read 与 event write。

主 Chat/LangGraph runtime 授予与基线相同的能力，不新增拒绝规则。若某调用上下文实际缺少 Spec 声明的 capability，则立即产生既有权限失败语义：

```text
permission_denied
```

Capability 必须先短路；缺少 capability 时不得进入 Binding resolver、Repository 查询、preflight 或 executor，避免通过实体是否存在泄露越权信息。

### 8.3 Binding audit

Binding 仅是首批审计结果，不自动注入 ID，也不新增拒绝规则。结果固定为：

- `matched`
- `mismatched`
- `unbound`
- `unavailable`

存在当前 binding 时，多目标聚合优先级固定为：

```text
任一可解析目标不同 → mismatched
否则任一目标无法安全解析 → unavailable
否则全部目标相同 → matched
```

没有当前 binding 时为 `unbound`。存在 binding 但零目标时为 `unavailable`。

测试必须覆盖：

- 相同 + 不同；
- 相同 + 无法解析；
- 不同 + 无法解析。

Binding resolver 允许只读查询，但结果只包含无 ID 状态。读取到的实体、ORM 对象或 revision 不得进入 `PreparedToolCall`，也不能替代执行阶段的权威归属检查。现有 API、工具和 Repository 的归属限制继续原样执行。

## 9. 两阶段 Pipeline

### 9.1 prepare_call()

```text
prepare_call()
  → Catalog resolve
  → JSON parse / Schema validation
  → lossless typed decode
  → capability gate
  → binding audit
  → read-only preflight
  → PreparedToolCall | confirmation_required | ToolOutcome failure
```

固定顺序：

1. Typed Catalog 查找；
2. 参数解析与 Schema 校验；
3. typed decode；
4. capability gate；
5. binding audit；
6. 只读 preflight；
7. 根据 confirmation policy 返回 ready 或 confirmation required。

确认前 preflight 必须只读、无业务副作用。它可以为确认 UI 提供当前预览，但不能缓存或授权执行。

`PreparedToolCall` 只包含：

- `tool_call_id`；
- 工具名；
- Provider contract fingerprint；
- 当前重新解码的 typed Args；
- capability 与 binding 结果；
- confirmation policy；
- 不携带 Repository 状态的安全准备信息。

它不得包含 ORM 对象、数据库锁、实体快照、旧 revision 结论或其他跨请求可信状态。

### 9.2 execute_prepared()

```text
execute_prepared()
  → mutable precondition recheck
  → write only: existing confirmation claim / CAS
  → write only: create and verify ExecutionAuthorization
  → tool.started
  → executor at most once
  → ToolOutcome
```

只读工具的固定顺序是：

```text
mutable precondition recheck
→ tool.started
→ executor
→ tool.completed | tool.failed
```

已确认写入的固定顺序是：

```text
mutable precondition recheck
→ confirmation claim / CAS
→ 创建 ExecutionAuthorization
→ authorization 与 PreparedToolCall 匹配
→ tool.started
→ executor
→ tool.completed | tool.failed
```

claim/CAS 或 authorization 匹配失败都发生在执行开始前，executor 为 0 次，也不得写 `tool.started`、`tool.completed` 或 `tool.failed`。

确认后必须重新校验：

- 实体仍存在；
- 实体归属；
- revision；
- stale-state；
- 领域业务前置条件；
- 现有 Repository/API 校验。

执行次数语义：

- executor 调用前任何失败：调用 0 次；
- executor 自身抛出普通异常：调用恰好 1 次并产生失败 Outcome；
- Journal、renderer、transport 或后续投影失败：不得再次调用 executor。

“执行一次”只保证单次 `execute_prepared()` 调用内最多一次，不承诺跨进程或跨请求 exactly-once。全局 exactly-once 属于第三阶段 Write Operation Ledger。

### 9.3 异常边界

Pipeline 只捕获普通 `Exception`。`KeyboardInterrupt`、`SystemExit`、取消信号和其他 `BaseException` 必须继续传播。

任一异常都不得触发：

- 旧 handler fallback；
- 第二次 executor 调用；
- shadow execution；
- Provider 重试；
- 工具重试。

## 10. 确认、恢复与瞬态执行授权

### 10.1 等待确认

25 个模型可见写工具使用 `ConfirmationPolicy.REQUIRED`，只读工具使用 `ConfirmationPolicy.NONE`。现有 `auto_approve` 外围参数可以暂时保留，但不能绕过写入确认。

Pending Action 继续保存现有字段。`PreparedToolCall`、Context、capability 和 binding 结果不进入 Pending Action。

Typed 确认恢复在 executor 前通过 Chat Repository 对现有 Pending Action 身份做一次持久 CAS claim。`0025_pending_confirmation_claim` 为 `conversations` 增加私有的 `pending_confirmation_claim_id` 与 `pending_confirmation_claimed_at` 两列；Provider 的 `tool_call_id` 必须原样持久化，禁止把 claim marker 编入任何 Provider 可控字段。Repository/API 对外仍投影原始 Pending Action 字段与 confirmation token。结果持久化必须匹配同一 claim identity 后才能清除 Pending Action；普通 Chat 的 clear/replace/persist 路径必须以 claim 列为空为条件，不能覆盖正在执行的确认。执行前 validation、capability、preflight、stale 或 authorization 失败若尚未取得 claim，不得调用结果持久化，也不得清除 Pending Action。

Claim 使用 15 分钟有界 lease，长于当前 120 秒 Chat Agent 执行预算。进程在 claim 后崩溃时，可信的后续确认请求可在 lease 过期后用同一 Pending Action 身份原子替换 claim，避免 Pending 永久卡死；未过期 claim 仍保持单赢家。空字符串只表示未 claim，不能作为显式 claim identity 传给 resolve。该恢复机制不新增业务 Ledger，也不承诺跨请求 exactly-once：若 executor 异常运行超过 lease，重新 claim 可能造成再次执行，此边界由第三阶段 Write Operation Ledger 解决。三个 deterministic Legacy Adapter 的既有确认实现不变。

### 10.2 批准与修改

```text
服务端读取可信 Pending Action
  → confirmation token / identity 校验
  → 合并 approved 或 modified 参数
  → 重新 prepare_call()
  → 将 PreparedToolCall 与可信确认尝试交给 execute_prepared()
      → mutable precondition recheck
      → 现有 confirmation claim / CAS
      → ExecutionAuthorization
      → authorization 与 PreparedToolCall 匹配
      → tool.started
      → executor
```

恢复不能信任 checkpoint 中的旧内存对象。必须从当前持久 Pending Action 重建参数、Context 和 PreparedToolCall，并重新运行 Schema、decode、capability、binding、preflight 和可变前置校验。

`ExecutionAuthorization` 是不可序列化的瞬态对象，至少绑定：

- Pending Action 的持久身份字段，以及已有 revision 时的 revision；
- `tool_call_id`；
- `tool_name`；
- effective arguments canonical digest。

写工具重新 `prepare_call()` 后仍会形成包含 PreparedToolCall 的 `confirmation_required` 结果，但可信恢复入口不得再次创建 Pending Action 或再次 interrupt；它将其中的 PreparedToolCall 与当前确认尝试直接交给 `execute_prepared()`。Authorization 在 `execute_prepared()` 内由现有 confirmation claim/CAS 成功后产生，必须与当前 PreparedToolCall 完全匹配。不匹配返回 stale/conflict，executor 为 0 次。

该授权只约束单次恢复流程，不扩大为跨进程 exactly-once 保证。

### 10.3 拒绝

拒绝路径固定为：

```text
服务端读取可信 Pending Action
  → token / Pending Action 身份校验
  → 拒绝 CAS
  → confirmation_rejected
  → executor 0 次
```

拒绝不重新运行 `prepare_call()`，不解析参数，不查询实体，也不执行 capability、binding 或 preflight。因此参数或实体在等待期间失效不能阻止用户完成拒绝。

### 10.4 Legacy 路由信任边界

- 模型 ToolCall dispatcher 只能查询 Typed Catalog；
- Typed Catalog 未命中时绝不能尝试 Legacy；
- Legacy 初始动作只能由服务端确定性流程创建；
- Legacy 确认恢复必须先读取服务端保存的 Pending Action；
- 只有可信 Pending Action 的封闭 legacy 类型/名称可以选择 Legacy Adapter；
- 客户端提交的 `tool_name` 不能单独决定进入 Legacy Adapter。

## 11. ToolOutcome、异常映射与兼容 Renderer

### 11.1 封闭结果类型

```text
ToolOutcome[ResultT]
  ├── ToolSuccess[ResultT]
  └── ToolFailure
       ├── validation_error
       ├── permission_denied
       ├── confirmation_rejected
       ├── stale_state
       ├── conflict
       ├── not_found
       ├── provider_error
       └── internal_error
```

每个 `ToolSpec` 必须声明封闭的领域异常映射。不能根据通用异常类型做过度推断，例如 `IntegrityError` 不自动等于 conflict。未显式映射的普通异常统一进入 `internal_error`。

禁止通过解析：

- `"错误："` 前缀；
- 异常文本；
- compatibility string；

来决定分类、重试、Run 状态或副作用。

### 11.2 瞬态兼容信息

为保持基线可见错误文本，ToolFailure 可以包含 renderer 所需的瞬态兼容信息，但：

- 不得保存异常对象；
- 字段必须 `repr=False`；
- 禁止通用序列化；
- 不得写入日志、审计、Journal、checkpoint 或数据库；
- 不得参与控制流判断。

### 11.3 CompatibilityRenderer

```text
render(spec, outcome) -> str
```

Renderer 是覆盖全部 ToolOutcome 的纯函数：

- 无 Repository、网络、时钟、随机数或 Journal；
- success 从 typed result 确定性生成基线字符串；
- failure 生成基线 `"错误：..."` 字符串；
- 不增加 Provider 调用、工具调用、重试或 fallback。

Renderer 异常是交付/集成缺陷，不得修改已经确定的 Outcome，也不得重跑 executor。它按现有编排或传输失败边界向上传播。

用户可见错误协议的变更必须在后续独立迁移中设计。

## 12. Journal 投影与固定事件时序

第二阶段严格沿用第一期冻结的 Event Schema，不增加通用字段，不提升 schema version。

### 12.1 固定时序

只有 executor 即将实际调用时，Journal 才进入工具执行序列：

```text
tool.proposed
→ tool.started
→ tool.completed | tool.failed
```

`tool.started` 必须在全部执行前检查和写入授权完成后、executor 调用前写入：

```text
result_contract = legacy_string_v1
```

虽然 Pipeline 内部已经类型化，持久 ToolMessage 仍是兼容字符串，因此本期不得写 `typed_result_v1`。

等待确认：

```text
tool.proposed
→ approval.requested
→ 不产生 tool.started
```

拒绝、token/CAS/stale、参数或权限在 executor 前失败：

```text
executor = 0
→ 不伪造 tool.started
→ 不伪造 tool.completed
→ 不伪造 tool.failed
```

这类失败仍保留内部 ToolOutcome 和兼容 transport 结果，但不写工具执行终结事件。第一期 Trace 因而不会产生 `tool_completion_without_start`。

### 12.2 第一期开集内 payload

只允许：

```text
tool.proposed
  tool_call_id / tool_name / tool_kind
  args_shape_digest / proposal_outcome

tool.started
  tool_call_id / tool_name / result_contract

tool.completed
  tool_call_id / tool_name / outcome
  result_shape_digest

tool.failed
  tool_call_id / tool_name / failure_category
```

contract fingerprint、binding/capability、内部失败 code、typed result enum 和额外计数只有在第一期 Schema 已允许时才能写入；否则只存在于内存诊断。需要新增字段时必须单独设计 Journal schema version。

### 12.3 ToolFailure 到 Journal 的阶段化投影表

映射表只能在 `execution_started=true`，即 Pipeline 已越过全部执行前门禁、调用了 `tool.started` 投影并进入 executor 调用边界后使用。`execution_started=false` 时所有失败分类都不产生 `tool.failed`。

| ToolFailure category | `execution_started=false` | `execution_started=true` 的第一期 `failure_category` |
|---|---|---|
| `validation_error` | 不写工具终结事件 | `tool_error` |
| `permission_denied` | 不写工具终结事件 | `tool_error` |
| `confirmation_rejected` | 不写工具终结事件；使用既有 `approval.decided=rejected` | 不适用 |
| `stale_state` | 不写工具终结事件 | `tool_error` |
| `conflict` | 不写工具终结事件 | `tool_error` |
| `not_found` | 不写工具终结事件 | `tool_error` |
| `provider_error` | 不写工具终结事件 | `provider_error` |
| `internal_error` | 不写工具终结事件 | `tool_error` |

`tool.completed` 同样要求 `execution_started=true`。executor 正常返回时写 `tool.completed`；executor 抛出被 Pipeline 捕获的普通 `Exception` 时，才按上表写 `tool.failed`。

第一期 `SafeRunRecorder` 的 degraded latch 保证结构 fail-open：若 `tool.started` 校验或持久化失败，Recorder 进入 degraded，后续 `tool.completed/tool.failed` 投影为 no-op；Pipeline 仍执行 executor，但不会制造“终结事件存在而 started 缺失”的残缺序列。

若未知工具名不满足第一期 `tool_name` 白名单，Journal projector 不得伪造其他名称；该投影按 recorder fail-open 处理。

`tool.failed` 不自动等同于 `run.failed`。Run 是否继续或失败仍由现有 Agent 编排语义决定。

### 12.4 隐私与 fail-open

Journal、日志及审计接口不得保存：

- 原始参数；
- typed Args；
- typed result；
- compatibility string；
- 原始异常、异常文本或 traceback；
- Prompt、回答、JD、简历或实体正文。

Journal 只写冻结 Schema 允许的固定事实和 shape digest。投影或写入失败由现有 Recorder 降级处理，不能改变 Outcome、renderer 输出或 executor 次数。

## 13. Transport、Graph State 与多调用语义

### 13.1 Transport projector

```text
project_transport_event(spec, record) -> existing HTTP/SSE payload
```

它是独立纯函数，读取 typed result/Outcome 并生成现有：

- `status`；
- `summary`；
- `evidence`；
- `affected_resources`；
- `changed_entities`；
- 其他当前 transport 字段。

Transport projector 不解析 compatibility string，也不复用 Journal projector。投影失败是交付异常，不修改 Outcome、不重跑 executor。

HTTP 与 SSE 使用同一 Pipeline 和 transport projection 语义。SSE 断开不触发工具重试，本期不增加 replay。

### 13.2 Graph State 与 checkpoint

`ToolCatalog`、`ToolExecutionContext` 和 `ToolExecutionRecord` 通过 runner/runtime context 注入或保存在 runner 实例中，不进入 `_GraphState`。

Graph State 只保存：

- 兼容 ToolMessage；
- 既有控制状态；
- checkpoint 已允许的安全字段。

负向测试必须读取实际 checkpoint，证明其中不存在：

- typed result；
- ToolOutcome；
- ToolExecutionRecord；
- ToolCatalog/ToolExecutionContext；
- capability/binding audit；
- 异常对象或瞬态兼容信息。

### 13.3 多 ToolCall 精确语义

保持固定基线行为：

```text
全部只读 → 按原顺序执行全部
只要包含写工具 → 只保留原响应的 tool_calls[0]
```

因此 `read + write` 也只执行第一个 read，第二个 write 被丢弃。测试必须覆盖：

- read + read；
- write + read；
- read + write；
- write + write；
- 前一个只读失败后是否继续后续只读；
- sync/stream 完全一致。

不增加模型调用、工具调用或 follow-up。工具失败后的模型循环行为保持基线。

## 14. 模块与依赖方向

```text
offerpilot/ai/tool_runtime/
  contracts.py       ProviderToolContract、ToolSpec、ToolOutcome
  validation.py      JSON parser、Schema compiler、typed decoder
  context.py         ToolExecutionContext、capability、binding
  pipeline.py        prepare_call、execute_prepared、authorization
  rendering.py       CompatibilityRenderer
  transport.py       HTTP/SSE event projector
  journal.py         第一期开集内 Journal projector
  catalog.py         通用 ToolCatalog、封闭校验和查询
  legacy.py          通用 Legacy adapter 基础设施

offerpilot/ai/tool_specs/
  applications.py
  application_events.py
  notes.py
  offers.py
  resumes.py
  jd_analyses.py
  catalog.py         composition root，组装 25 个 Spec
  legacy.py          仅组装 3 个 deterministic adapters
```

依赖方向固定为：

```text
tool_specs/* → tool_runtime/*
```

`tool_runtime/catalog.py` 不导入 `tool_specs`。`tool_specs/catalog.py` 作为 composition root 导入六个领域 Spec 并组装 25 工具，避免核心 runtime 反向依赖和循环导入。

## 15. 破坏性切换与旧路径删除

允许按领域逐步编写 Spec 和测试，但生产入口只能进行一次最终切换：

- 不设置 feature flag；
- 不双执行；
- 不 shadow write；
- 不保留旧 handler fallback；
- 不保留双轨 registry；
- 不从 Typed Pipeline 跳到 Legacy；
- 初始化或执行失败按现有失败语义返回，不回退旧路径。

切换提交必须同时：

1. 把 25 个工具接入 Typed Catalog；
2. 更新 Agent、API、HTTP/SSE 和测试调用方；
3. 删除 25 个旧字符串 handler；
4. 删除字典 Provider registry builder；
5. 删除 `startswith("错误：")` 控制流；
6. 保留且隔离 3 个 deterministic Legacy Adapter。

## 16. 机械化源码门禁

除行为测试外，增加 source/AST gate 证明旧路径已经删除。

### 16.1 必须证明

- 25 个迁移工具不存在旧字符串 handler；
- Provider builder 不接受字典 registry；
- 模型 dispatcher 不导入或引用 Legacy Catalog；
- `tool_runtime` 不导入 `tool_specs`；
- Agent/API 不访问 `handler`、`write`、`validate` 等旧字典键；
- 25 个 executor 只能通过 `ToolSpec` 与 Pipeline 调用；
- 不存在 feature flag、shadow execution、双写或旧路径 fallback；
- `jsonschema==4.26.0` 同时固定在 `pyproject.toml` 和 `uv.lock`。

### 16.2 错误字符串 allowlist

生产源码中的兼容错误前缀只能存在于：

- `CompatibilityRenderer` 的字符串生成逻辑；
- 3 个精确命名的 Legacy Adapter。

`startswith("错误：")` 或兼容字符串解析只允许出现在精确 source/symbol allowlist 中；transport projector 禁止解析兼容字符串。测试中的使用仅允许在 compatibility/transport golden assertions 的精确路径中。

Gate 不允许通过宽泛目录 allowlist 隐藏新增调用点。

## 17. 测试策略

### 17.1 Provider 契约

- 只读 golden manifest；
- 25 个名称、顺序、完整 envelope；
- 每个 Schema fingerprint；
- 无网络 Provider adapter spy；
- `25 typed + 3 legacy deterministic` 精确分类；
- 未分类、重复、遗漏和错误暴露初始化失败。

### 17.2 Parser、Schema 与 Pipeline 公共测试

- 非法 JSON、非 object、重复键、非有限数字；
- `check_schema` 与预编译；
- 禁止外部 `$ref`、`$dynamicRef` 和网络解析；
- unknown tool 与 capability 短路顺序；
- Binding 聚合组合；
- 普通 `Exception` 分类和 `BaseException` 传播；
- executor 前失败 0 次；
- executor 异常 1 次；
- renderer、transport、Journal 失败不重跑 executor；
- 全部固定 ToolFailure 分类的公共覆盖。

### 17.3 25 个 ToolSpec

每个工具测试：

- 至少一个成功路径；
- Spec 声明可能产生的全部失败分类；
- 最终兼容字符串；
- Repository 调用次数和业务副作用；
- preflight 无写入；
- 领域异常映射封闭性；
- Schema 合法输入不被 decoder 额外拒绝。

不要求工具制造其声明不可能产生的失败分类。

### 17.4 HITL、Pending Action 与 CAS

- approve、modify、reject；
- token、Pending Action 身份和参数 digest 不匹配；
- stale replacement 与并发 claim；
- 拒绝时参数或实体已经失效；
- claim 失败 executor 0 次；
- authorization 不匹配 executor 0 次；
- checkpoint 存在与缺失两条恢复路径；
- trusted server Pending Action 才能路由 Legacy；
- 客户端伪造 legacy tool name 不能进入 Legacy。

### 17.5 Journal

- `tool.proposed → tool.started → completed/failed` 时序；
- 等待确认不产生 `tool.started`；
- pre-executor failure 不伪造 started/completed/failed；
- claim/CAS 和 authorization 匹配失败不产生工具执行事件；
- ToolFailure 仅在 `execution_started=true` 时映射到第一期 failure category；
- 与 `30c944f` 的第一期 Journal 事件序列 golden 对比；
- Trace 不产生 `tool_completion_without_start`；
- `result_contract=legacy_string_v1`；
- Journal 故障 fail-open；
- Journal/日志/审计无参数、结果或异常文本。

### 17.6 Agent、HTTP/SSE 与 checkpoint

- sync/stream 多 ToolCall 矩阵；
- 前一个只读失败后的基线行为；
- 模型调用和工具调用次数；
- ToolMessage、HTTP 与 SSE 完整 payload；
- `status/summary/evidence/affected_resources/changed_entities`；
- checkpoint 负向内容扫描；
- transport projector 故障不修改 Outcome、不重跑 executor。

### 17.7 Golden 数据

兼容 golden 在删除旧实现前从 `30c944f` 捕获，使用：

- 纯合成实体；
- 固定时钟；
- 固定 ID；
- 隔离数据库；
- canonical JSON 投影。

Golden 锁定成功/失败字符串和领域写入结果，不保存 SQLite、真实用户内容、密钥或不稳定时间字段。新实现测试只能读取，不能刷新预期。

Golden 还必须保存纯合成、canonical 的第一期 Journal 事件序列投影，逐场景比较事件类型、顺序和冻结 facts。它不得保存 Journal SQLite 文件，也不得忽略结构异常。

## 18. 发布级完成门禁

首批完成前必须通过：

1. 全部定向 Tool Runtime、Agent、API、Journal 和 Legacy 测试；
2. 后端分组 manifest、并集、重复 node ID、skip 和 aggregate 门禁；
3. Ruff；
4. Mypy；
5. frontend tests；
6. frontend build；
7. local smoke 与 local verify；
8. 受控 real-AI verify；
9. 至少一次本地浏览器 Chat 闭环：
   - 只读多调用；
   - 写入确认；
   - 修改确认；
   - 拒绝；
   - 流式路径；
10. 独立 Code Review 无 P0/P1/P2；
11. baseline allowlist 检查；
12. 未跟踪文件检查；
13. `git diff --check`；
14. 最终工作区干净。

测试命令若耗时较长，应分组运行并在不超过约 30 秒的前台等待后轮询，避免桌面 Codex 进程稳定性问题。

## 19. 完成条件与发布报告

首批完成报告必须明确：

- 内部采用破坏性切换，旧 registry 和 25 个旧 handler 已删除；
- 25 个模型可见工具已经迁移；
- 3 个 deterministic 工具仍在 Legacy Adapter；
- Provider、HTTP/SSE、HITL、Pending Action、CAS、Journal、调用次数和副作用等价；
- strict binding、ID 预绑定、Write Operation Ledger、Context Projector 和 SSE Replay 尚未实现；
- 不承诺跨请求或跨进程 exactly-once；
- 所有验证命令、skip、环境限制和剩余风险；
- baseline allowlist 与最终提交范围。

本首批只声明“25 个模型可见工具完成 Typed Pipeline 迁移”。3 个 deterministic 工具必须保持明确的剩余兼容清单，并在后续独立设计中迁移。

## 20. 核心不变量

1. Provider 看到的完整工具列表、顺序和 Schema canonical 等价。
2. 模型 dispatcher 永远不能调用 Legacy Adapter。
3. Typed Pipeline 不解析兼容错误字符串。
4. Capability 缺失先于任何实体查询短路。
5. Binding 首批只审计，不放宽或收紧现有领域归属规则。
6. Pending Action 恢复不信任跨请求内存对象。
7. 已确认写入严格按 claim/CAS → authorization 创建与匹配 → `tool.started` → executor 排序。
8. 单次 `execute_prepared()` 内 executor 最多调用一次。
9. Journal、renderer 和 transport 故障不得重跑 executor。
10. Journal 只写第一期冻结 Schema，`tool.started` 保持 `legacy_string_v1`，执行前失败不写工具终结事件。
11. ToolExecutionRecord、Outcome、typed result 和异常对象不进入 checkpoint。
12. 本阶段不声称业务 exactly-once。
13. `$ref` 与 `$dynamicRef` 只允许本地 fragment，Schema validator 不具备 retrieval 能力。
