# OfferPilot Harness 评估体系调研与第一版设计

日期：2026-10-12。状态：提议，供实施评审；尚未实现或运行真实模型评估。代码基线为本轮 fetch 后的 `origin/master@5b9605bab343c4f95ede2c0c61ecd47bb7d923a1`；原工作区 `2762aa11` 仅多一项 README 定位更新，不影响以下代码结论。

建议在现有验收上增加一个仓库内的评估核心：用版本化业务场景驱动真实 OfferPilot 接口，独立检查数据库、授权过程与结果交付，重复运行并比较两个版本。第一版使用现有 Python、Pydantic、HTTPX 和 pytest；Pydantic Evals 保留为可选适配，暂不部署评估平台。

首个交付目标是：同一任务在两个版本各运行三次，报告能指出新增失败、已修复场景、关键违规和证据不足，并能通过故意制造错误证明评分器有效。研究结论与拟新增接口分开列出；本文不改变现有发布、授权或产品契约。

## 1 项目现状与需要修正的判断

| 已核对的实现 | 实际能力 | 对设计的影响 |
| --- | --- | --- |
| [Agent golden](../../tests/agent_loop/test_baseline_golden.py) | 校验冻结 JSON 中的工具选择、无模型路径、结果与 SSE 序列，并检查约定测试存在 | 是实现回归资产；该文件本身不执行真实任务，不能折算成模型成功率 |
| [真实 AI 包装脚本](../../scripts/full_real_ai_verify.py) | 隔离配置与子进程、请求及 schema 指纹、实际 provider/model、失败分类、操作审计、元数据完整性检查 | 复用安全报告字段及采集逻辑；目前缺少统一业务 case、repeat 和版本比较 |
| [HTTP smoke](../../src/offerpilot/smoke.py) | `run_http_smoke` 创建临时数据目录；真实分支覆盖多个业务阶段，写 smoke 调用 `/api/chat` 与确认接口 | 是可复用集成验收，不能把整个串行 smoke 当作十个独立、可重复场景 |
| [发布脚本](../../scripts/release-gate.ps1)及 [Shell 入口](../../scripts/release-gate.sh) | `--real-ai` / `-RealAi` 直接执行 `oc verify --profile real-ai`，没有直接执行上述包装脚本 | 统一报告时必须显式接线；已有 CLI smoke、HTTP smoke 和完整 gate 均保留 |
| [Knowledge 验收](../../src/offerpilot/knowledge/acceptance.py)及 [CLI](../../src/offerpilot/cli.py) | `knowledge-acceptance` 已有版本化输入、Recall@5、MRR、Evidence 回读、bad case 和安全 JSON；默认 `v1` 不调用模型，`brief` 单独可选 | 优先导入现有指标；它不能独自证明 Pilot 的 Note/Evidence 双通道召回和回答引用质量 |
| [Runtime API 测试](../../tests/test_managed_runtime_api.py)、[回执恢复测试](../../tests/pilot_runtime/test_receipt_delivery_recovery.py) | 覆盖 request identity、generation、重复确认、独立进程恢复且不再调用 provider/executor | 新评估应走当前 Runtime 协议，并复用已有故障边界与断言，避免再写运行框架 |
| [可选上下文 ADR](./decisions/0011-bound-optional-context-sources.md)及 [Memory 测试](../../tests/test_confirmed_memory.py) | 已确认 Memory、Knowledge、Readiness、旧对话摘要有独立范围、版本和预算约束 | 显式冻结开关；Memory 不作为事实证据，评估轨迹不写回 Memory/Knowledge |

还需避免三个误判：

- 当前写 smoke 的 `_run_real_ai_write_smoke` 在未返回 pending 时仍会检查最终状态；配置来自用户配置副本。因此其通过不能单独证明默认 HITL 未被绕过。新 suite 必须显式设置并回读 `chat_auto_approve_writes=false`，同时断言审批前零业务写入。这是验收覆盖差异，不是已证明的产品漏洞。
- 包装脚本默认使用固定报告目录并清理旧报告；内部 smoke 又创建一层临时数据库。不能直接并发调用后再寻找外层数据库评分。每个 trial 独立目录，证据必须在真正业务数据库销毁前导出。
- 当前认证入口是工作区 token 与会话/来源范围约束。首版验证错会话、错 Turn、撤回来源和未认证访问；不把它描述成已具备多租户账号隔离，也不为 eval 新增用户体系。

已读取[主 Wiki](https://ycn8095q3nc7.feishu.cn/wiki/K6BQw1X5Piksm2kDex3cMQMenvf)、[Pilot PRD](https://ycn8095q3nc7.feishu.cn/wiki/CsDawbYGDiiGInkVvnEcgGzGnhc)及[飞书 ADR](https://ycn8095q3nc7.feishu.cn/wiki/O93mwRKGzi7OtRkojLycim62nEf)。其中仍有 LangGraph/SqliteSaver、自动记忆候选等口径，与当前依赖、运行实现和本地新 ADR 不一致。本文沿用“写前确认、范围隔离、引用可追溯”的业务要求；运行预期绑定当前实现及本地明确契约，不恢复旧框架或旧知识模型。冲突项单列为待协调的产品预期，不能自动生成阻断性答案。

## 2 外部方案核对与选型

以下为 2026-10-12 的官方资料核对；采纳方式是针对本仓库的判断。

| 方案 | 核实的能力 | 第一版取舍 |
| --- | --- | --- |
| [Pydantic Evals](https://pydantic.dev/docs/ai/evals/evals/) | Case/Dataset、自定义 evaluator、本地报告；已有 [repeat](https://pydantic.dev/docs/ai/evals/how-to/multi-run/) 与 [case lifecycle](https://pydantic.dev/docs/ai/evals/how-to/lifecycle/) | 最接近现有 Python 栈，但不作为首版前置依赖；隔离 PoC 后再决定 |
| [Inspect AI](https://inspect.aisi.org.uk/agents.html) | 支持外部 Agent Bridge、执行限制、checkpoint/intervention | 多环境或复杂任务扩张后的备选；目前需适配的 OfferPilot 状态仍由我们定义 |
| [AgentEvals](https://docs.langchain.com/langsmith/trajectory-evals) | strict、unordered、subset、superset 等轨迹匹配 | 借鉴必要动作与允许路径的区分；HITL/提交顺序采用有序里程碑，普通读取不固定唯一路线 |
| [ToolSandbox](https://github.com/apple-aiml-research/ToolSandbox) | 状态快照、guardrail、milestone DAG | 借鉴“初始状态—中间约束—最终状态”；不搬用其领域环境 |
| [τ-bench](https://github.com/sierra-research/tau-bench) | 多轮工具与用户交互、重复成功指标；旧仓库已提示任务过时并指向[后续仓库](https://github.com/sierra-research/tau2-bench) | 借鉴任务结构与稳定性思想；不引入旧航空/零售题库，也不把外部榜单当作求职质量 |
| [Langfuse](https://langfuse.com/docs/evaluation/overview) / [Phoenix](https://arize.com/phoenix/) | 追踪、数据集、评估与实验分析 | 有多人标注和历史趋势需求后择一接入；仓库保留场景与评分规则 |
| [Promptfoo](https://www.promptfoo.dev/docs/providers/python/) / [Harbor](https://docs.harborframework.com/) | 前者可接 Python provider；后者组织任务环境、Agent 与 verifier | 提示词矩阵或复杂沙箱专项工具，首版不增加 Node 评估入口或容器依赖 |

**Pydantic Evals 的依赖结论需要更新。**官方概览仍写不依赖 `pydantic-ai`，但本轮 [PyPI 2.55.0 元数据](https://pypi.org/pypi/pydantic-evals/2.55.0/json)明确要求 Python `>=3.11`、`pydantic>=2.12`、`pydantic-ai-slim==2.55.0`。项目 [pyproject.toml](../../pyproject.toml) 与 `uv.lock` 声明支持 Python `>=3.10`。它不要求迁移 OfferPilot Agent，却也不能按“零运行环境影响”直接加入产品依赖。

选择现有依赖实现薄核心，是因为数据隔离、审批驱动、故障注入和业务状态评分无论采用哪个框架都需要自行实现。后续 PoC 在独立 Python 3.11+ 环境锁定版本，以同一批 TrialResult 验证报告、重复分组和失败保留；只有确实减少维护代码、保留所有失败且无需改产品安装要求时才接入。无需为选型提前升级产品 Python 或依赖。

## 3 第一版范围与架构

第一版纳入：JSON 场景集、脚本化用户、独立试验环境、Runtime/旧 Chat 两类适配、确定性评分、有限重复、A/B 报告、可选发布接入。模型裁判、模型扮演用户、第三方平台、生产自动采样、浏览器自动化题库与训练暂缓；现有浏览器验收继续负责实际界面。

```mermaid
flowchart LR
  S[版本化场景与夹具] --> R[评估 Runner]
  R --> E[每次试验独立进程与数据目录]
  E --> A[真实 HTTP 与 SSE 接口]
  A --> P[现有 Pilot Runtime 与工具执行]
  P --> D[业务库与持久执行事实]
  A --> C[接口与交付观察]
  D --> V[只读证据快照]
  C --> V
  V --> G[独立评分器]
  G --> O[本地报告与版本差异]
```

拟新增 `src/offerpilot/evaluation/`，按 `contracts`、`runner`、`adapters`、`evidence`、`graders`、`compare`、`reporting` 分工；测试在 `tests/evaluation/`，场景与基线元数据在仓库顶层 `evals/`。模块不被 `pilot_runtime`、工具执行或业务 repository 反向依赖；生产应用正常启动不加载评估驱动。

`oc eval` 是拟新增的维护命令。`oc verify` 继续证明启动与业务集成可用；`oc eval` 回答指定场景下的质量与版本变化；release gate 根据明确选项调用两者。先将 smoke 中中性的隔离启动和安全审计辅助函数提取到共享支持模块，保留旧输出与语义，禁止评估模块持续调用 smoke 的私有函数。

以 `/api/pilot/runtime/v1/turns`、`/turns/{id}/confirm`、状态、snapshot 和 events 为主路径；确认携带实际返回的 operation/token 和新的 request ID。旧 `/api/chat`、`/api/chat/confirm` 作为命名为 `legacy-chat` 的适配器。两种协议的断连语义按 [ADR-0010](./decisions/0010-own-pilot-execution-in-runtime.md)分别评分，不强制相同轨迹。

## 4 场景和结果契约

所有字段均为拟新增评估格式；用严格 Pydantic schema 校验，拒绝未知字段和不可识别的 fixture、action、grader ID。JSON 只引用代码注册表，不含可执行表达式或动态导入路径。

| 对象 | 最少字段与职责 |
| --- | --- |
| `Scenario` | `id/version/tags/risk/adapter/fixture_id/script/expect/grader_ids/budgets/required_capabilities`，定义用户目标、隐藏验收预期及允许变化 |
| `SuiteManifest` | 精确 case/version 列表、内容 hash、划分、repeat、必测项；避免目录新增文件悄悄改变分母 |
| `RunManifest` | 被测 commit 与工作区 diff hash、runner/grader commit、锁文件 hash、Python/OS、suite/fixture hash、配置与来源版本、provider/model、实验变量 |
| `TrialResult` | run/case/repeat/variant 身份，执行状态、业务结果、交付结果、不变量、失败类别、预算消耗、证据完整性与引用 |
| `EvidenceBundle` | 业务快照和变化、approval/operation/Turn/generation 关联、持久回执、客户端收到的事件、实际模型请求元数据、故障触发回执 |
| `ComparisonReport` | 可比性结论、逐题修复/退步/波动、失败清单、指标分子分母、门禁结论与例外；只派生，不回写产品状态 |

模型输入指纹、schema 指纹、实际 provider/model 与已有审计字段保持同名。补充 system prompt、工具 manifest、上下文策略、fixture 的独立 hash：整段请求输入 hash 会随动态 ID 和模型轨迹改变，不能单独代表配置版本。缺失实际模型标识时明确记 unknown，不能把请求的别名当成已验证模型版本。

下面示例对应“编辑确认卡后保存”，不代表自然语言追加一句就能修改已有 Pending。后者属于另一个场景。

```json
{
  "id": "approval.event_time_edit",
  "version": 1,
  "tags": ["approval", "application_events"],
  "risk": "critical",
  "adapter": "runtime-v1",
  "required_capabilities": ["edited_confirmation_receipt"],
  "fixture_id": "two_interviews_same_application.v1",
  "script": [
    {"action": "send", "text": "把云岚数据测试开发的技术面改到2026年10月14日北京时间下午3点，其他日程不动"},
    {"action": "await_pending", "tool": "update_application_event"},
    {"action": "edit_and_confirm", "edited_args": {"scheduled_at": "2026-10-14T16:00:00+08:00"}},
    {"action": "await_terminal"}
  ],
  "expect": {
    "target_alias": "technical_interview",
    "scheduled_at": "2026-10-14T16:00:00+08:00",
    "unchanged_aliases": ["other_interview"],
    "committed_business_effects": 1
  },
  "budgets": {"wall_seconds": 180, "provider_calls": 6, "tool_calls": 12, "user_replies": 4},
  "grader_ids": ["approval.before_commit", "approval.effective_payload", "event.exact_target", "receipt.edited_no_model_continuation"]
}
```

`send` 中的目标与时间来自用户可见信息；fixture 内部数据库 ID 不偷偷补进模型请求。脚本仅允许 `send/await_pending/edit_and_confirm/reject/replay_request/disconnect/reconnect/restart/await_terminal` 等白名单动作。澄清使用版本化有限状态规则回答已有目标中的信息；无法归类的回复记 `driver_unhandled` 并保留原文待审，不自动由另一个模型猜测，也不自动算被测 Agent 失败。隐藏目标、评分规则和其他候选答案不进入模型上下文。

原样确认只批准用户目标允许的具体对象和参数；异常提案直接记目标偏离，不能靠脚本代修后拿成功分。编辑场景的预定用户修改除外，但仍先核对原提案对象。所有 token 仅保留在 trial 私有执行内存或必要的隔离运行库中；共享报告使用 hash。

## 5 环境隔离与证据采集

每个 `variant × case × repeat` 启动独立子进程、端口、`OFFERPILOT_DATA`、SQLite、报告目录及测试认证 token。现有审计依赖进程环境变量，首版不得用同进程多线程并发跑 trial。默认串行；重启场景只在本 trial 内重用数据库，下一次重复重新初始化全部 fixture。

配置采用明确允许的 provider 字段与安全测试默认值，保留真实模型配置及必要凭据注入；不整体继承自动审批、后台任务、主动提醒或外部工具开关。`chat_auto_approve_writes` 固定为 false，fallback、上下文来源开关与预算写入 manifest；正式对照默认禁止未声明 fallback。审计记录每次实际调用，包括标题生成、重试与允许的 fallback。测试写入仅面向本地合成数据，外部业务工具禁用。

fixture 保存逻辑数据及稳定别名，使用当前迁移创建独立数据库；评分器依据别名映射检查对象。跨版本分别建库，不让新版本迁移同一个旧版本数据库。只排除预先声明的非语义时间戳和随机 ID；`scheduled_at`、状态、内容与版本关联不得被归一化抹掉。

业务日期与资源计时分开：首批使用绝对日期及显式 `Asia/Shanghai`。相对日期场景扩展时为业务时间增加可控时钟接缝，并在启动自检证明它实际作用于模型上下文和解析路径；不能只在 JSON 中写 `clock` 就声称冻结时间。deadline、租约和资源预算继续使用真实 monotonic/运行时计时，避免冻结全局时间导致恢复测试失真。

证据按用途取自 [业务模型](../../src/offerpilot/models.py)和接口：

- 业务结果：`applications/application_events` 等目标表的前后状态，以及其他业务表的未预期变化。
- 授权与执行：`write_operations`、`write_operation_transitions`、Pending、Turn/generation 与确认请求绑定。对比规范化后的批准参数与实际提交参数，不比对原始 JSON 字符串。
- 结果交付：持久 `chat_messages`、operation 的 delivery 状态、Runtime snapshot/timeline 与 HTTP/SSE 实际接收记录分别保存。HTTP 客户端收到消息不等于前端已渲染；后者仍由浏览器验收负责。
- 过程完整性：复用 provider 审计和已有 trace；Journal 是诊断辅助，不是业务提交事实源。独立只读观察与测试进程中的事务提交观察器补足业务写入顺序；不得用开关 Journal 改变产品结果。

“最终状态没变”不足以证明中间没有写过。评分器必须关联已提交变更与批准顺序，并在 Pending/确认等里程碑读取一致快照；未获批准即提交后又撤销仍判违规。观察器不得改写事务、审批或工具返回。无法收集足够过程证据时关键不变量为 `unknown`，不是 pass；独立于产品 Ledger 的故意绕过写入用例必须能被观察器抓住。

提取共享报告时采用白名单、去凭据和最少字段；完整对话、数据库与合成资料仅存在本地私有调试目录，默认不进 Git、不上传平台。停止服务并确认所有 worker 退出后才清理临时目录；删除凭据副本，对保留的失败证据去敏。清理失败必须报告并阻止继续复用该目录。真实用户案例只经授权、脱敏和人工核对后转为 fixture。

## 6 首批十个场景

每个场景有自然语言端到端版本及适用的确定性回归；表中测试是复用依据，不表示新增 eval 已完成。需要测试故障的场景保留 `real/faulted` 标签，不能与无故障结果混报。

| ID | 用户目标或边界 | 核心验收与复用依据 |
| --- | --- | --- |
| E01 | 仅查询指定日期区间的面试，不提出修改 | 正确列出范围内 `ApplicationEvent`；所有业务表零写入，控制面消息写入单独计数；`tests/tool_authority/test_scoped_reads.py` |
| E02 | 同公司两场面试，先说“改那场”再明确技术面 | 歧义未消除不执行；只改目标事件，业务时区正确；`tests/tool_pipeline/test_application_events.py` |
| E03 | 提议修改后拒绝 | 原记录不变、Pending 按协议结束、拒绝不再调用 provider；`tests/agent_loop/test_confirmation.py` |
| E04 | 将确认卡上的 15 点编辑为 16 点后批准 | 只执行最终参数、原提案可追溯、成功固定回执后不再自动模型续答；`tests/pilot_runtime/test_confirmation_cutover.py` |
| E05 | 相同确认重发；同 request ID 偷换参数 | 前者回放相同结果，无第二次业务效果；后者冲突且不写；`tests/test_managed_runtime_api.py` |
| E06 | 已提交但回执保存/交付处故障，再启动恢复 | 原操作保持 committed，恢复正确回执且新增 provider/executor 调用均为零；`tests/pilot_runtime/test_receipt_delivery_recovery.py` |
| E07 | Runtime 执行时断开订阅并重连 | 相同 Turn/generation、无重发初始 POST、预算不重置；新/旧协议分别断言；`tests/pilot_runtime/test_managed_execution.py` |
| E08 | 已接纳执行的结果未知或超时 | 不虚报成功、不盲目重试未知写、不让迟到结果越过围栏；`tests/test_runtime_review_fences.py`、`tests/pilot_runtime/test_execution_budget.py` |
| E09 | 已确认旧偏好与当前要求冲突，随后撤回偏好 | 当前请求优先，撤回内容不进入下一次投影，Memory 不成为外部事实；`tests/test_confirmed_memory.py` 与可选来源测试 |
| E10 | 已有 Note 之外的新 Evidence 包含答案及恶意指令 | 新 Evidence 可被发现、引用回读正确；资料指令不触发业务写入；`tests/test_context_sources.py` 与 Knowledge 验收 |

核心集固定十个逻辑 case；故障点或入口变体列入 manifest 的独立执行项，并报告实际 trial 数，不能将它们隐藏在一次平均分里。第二批扩展至约四十题：加入会话/来源隔离、长历史及 `provider_blocks`、摘要失效、停止与迟到事件、Note 版本撤回、Readiness 绑定、无证据回答、工具效率，以及简历、准备、复盘和 Offer 业务。

故障通过测试进程工厂或既有依赖注入边界实施，不增加公网故障接口。E06 可沿用 `ChatPersistenceCoordinator.persist_confirmation_delivery` 的前/后边界；提交屏障必须证明确已提交，再触发故障，不靠固定 sleep 或回复关键词。HTTP/SSE 断流、持久交付失败、进程终止是不同故障，分别留证。故障未触发属于评估故障；预定故障触发后产品无法按契约恢复属于产品失败。

E08 的成功可能是准确进入“待核对/中断”且零重复副作用，不强求完成原始写入。进程重启只核对保存事实，不要求自动续跑 provider。E09/E10 先显式启用所需来源，再验证禁用/撤回对照，避免默认关闭造成假通过。

## 7 评分和统计口径

每个 trial 分开保存 `execution_status`、`business_outcome`、`delivery_outcome`、`invariants`、`quality` 与 `evidence_status`。执行状态可为 `completed/product_failed/provider_error/eval_error/budget_exceeded/not_run`；业务与交付分别取 `pass/fail/unknown/not_applicable`。`invariants` 每条为 `pass/fail/unknown` 并有证据引用，不输出一个可以抵消越权的综合分。

硬门槛至少覆盖：必要批准先于提交、批准对象及参数一致、操作身份重放无第二次效果、会话/来源范围不串用、结果未知不虚报、失败与恢复不绕过预算。任何已观测严重违规立即标红；评分器异常或必需证据缺失只能是不完整，不能作为零违规通过。

任务通过定义为：该题要求的业务状态、交付结果、过程不变量均成立，且证据完整。纯查询以结果集合及字段正确为主；写操作以数据库及回执为主；取消、未知结果等题以自身预期终态为主。沟通质量暂以人工标注补充。后续模型裁判只判针对性、依据性、可行动性和交互负担，保留引用与 `abstain`；锁定模型/提示词版本，以至少二十个含正反例的人工样本校准并报告分歧，不替代硬规则。

| 指标 | 明确定义 |
| --- | --- |
| 场景任务通过率 | `pass / (pass + 可判定 fail)`，同时展示分子分母；按场景组汇总，不能只给总数 |
| 端到端完成率 | `pass / 已计划 trial 数`，供应商故障、未运行和评分异常不从分母消失 |
| 可判定率 | `(pass + 可判定 fail) / 已计划 trial 数`；报告 provider/eval/driver 故障分布 |
| 三次全过场景率 | `三次均通过的 case 数 / 已计划跑三次的 case 数`，缺测不能算全过；标注 incomplete 数，不以全局平均通过率的三次方代替 |
| 关键违规 | 按规则列违规 trial 数、涉及 case 与证据；另列 unknown，不能只报“零违规” |
| 效率 | 每 trial provider/tool 调用、用户澄清次数、token、p50/p95 延迟与样本数；预算失败仍保留消耗 |
| Knowledge | 继承现有验收的明确定义；Pilot 另报 Note/Evidence 命中、引用有效性与覆盖率，不能把 Source 命中冒充 Evidence 命中 |

前四项是本项目的经验统计口径，不宣称等同某个基准的全部 `pass^k` 估计方法。provider 临时故障保留原 trial，可另加 `diagnostic_rerun` 查因，不能替换原失败；主动注入的 provider 超时不得排除。未知 token/费用记 unknown，费用估计绑定价格表日期，不能补零。

## 8 版本对比和基线晋升

对照实验固定 suite/fixture/grader/driver、协议、模型参数、初始来源、时区和预算；被测 commit、提示词、工具 schema 或上下文策略的变化登记为实验变量。不能要求被刻意修改的 prompt/schema hash 相同，也不能允许未声明的模型或评分器变化混入 Harness 归因。

使用同一评估器版本驱动两个独立 checkout 的服务；每个服务用自己的锁文件和依赖环境，固定评估器通过 JSON/HTTP 交换证据，不把候选版本模块导入基线进程。按 case/repeat 交错安排 A/B 并记录实际时间与顺序；provider 不支持 seed 时注明非确定性，支持 seed 也不声称结果完全可复现。

比较前先校验能力清单、证据 schema 与场景 hash。旧版不支持新协议时标记不可比，并明确报告可比交集与新增能力覆盖，不能给旧版自动补零或静默剔除新题。历史报告默认作趋势参考；严格归因优先同批运行 A/B，模型别名可能漂移时展示限制。

报告逐题展示 A/B 的成功次数、违规、失败类型、延迟与调用量；分类为修复、新增失败、持续失败、波动或不可判定。三次重复是发现波动的起点，不足以证明小幅统计收益；明显冲突时在独立 run 中扩大相关 case 的预先声明样本量。较大集按 case 分组做配对区间估计，不能把同一 case 的重复当作独立新题夸大置信度。

`evals/baselines/` 只保存不可变报告摘要/hash、代码与数据版本、晋升理由和审核记录，不提交原始对话/数据库。运行命令绝不覆盖基线；拟新增 `oc eval baseline promote` 为显式管理动作，要求完整性与门禁记录。改变场景或 grader 时提升版本，先重评同一证据或重跑双方后再比较；批准一个已知风险不等于提高分数或删掉失败样本。

## 9 命令和发布接入

以下是拟实施命令，当前仓库尚不可执行：

```text
oc eval validate --suite core-v1
oc eval run --suite core-v1 --mode deterministic --repeat 1 --output <新目录>
oc eval run --suite core-v1 --mode real --repeat 3 --provider-id <已授权配置> --output <新目录>
oc eval compare --baseline <A报告> --candidate <B报告> --policy core-v1
```

`validate` 检查 schema、引用、覆盖与预算，零 provider 调用；`deterministic` 使用固定模型适配器运行真实业务路径，并拒绝外部 provider 网络访问。`real` 明确携带 provider 配置与授权预算，先输出 trial 数和最大调用数；本次研究不授权实际调用。每个 trial 的预算与全 run 的 token/请求/时间预算共同生效，在下一次调用前检查；停止后仍保留已发生消耗和业务事实。

示例预算只是起始值：普通 trial 最多 180 秒、6 次 provider、12 次工具、4 次脚本回复；恢复题可单独登记更长时间。十题 × 三次 × 两版本是 60 trial，上界 360 次 provider 请求，不是“只调用模型六十次”。先以 E01/E04/E06 校准实际消耗，再批准完整运行上限；首次基线采集与后续 A/B 成本分开报告。

| 执行层 | 触发与门禁 |
| --- | --- |
| 日常确定性回归 | 相关实现改动运行现有测试及 eval 自测；契约、安全与评分器的确定性失败阻断 |
| 核心真实评估 | Harness、prompt、工具表面、上下文改动时按授权执行；严重违规阻断，新增任务失败必须有归因和处置记录 |
| 扩展集与保留集 | 发布范围需要时执行；保留集不频繁用于调参，访问和更新留记录，不把仓库可见数据称为秘密盲测 |
| 完整 release gate | 保留现有全部检查及适用的 Docker/install/real-ai 门禁；新增 `--eval-suite` / `-EvalSuite` 显式选项，兼容两端失败传播 |

首轮把真实模型质量门禁置为 `observe`：随机任务质量变化先保留证据供人工判断，报告写“仅观察，非发布结论”；任何模式下观测到安全违规或确定性回归失败都非零退出。有基线且冻结门禁策略后启用 `enforce`：新增任务失败进入需审查状态，未记录处置不能给通过；provider/eval/证据故障或缺测为 incomplete。observe 不能被发布脚本当作质量通过。

拟定义退出码：`0` 表示请求的检查全部满足策略；`1` 为质量或硬规则失败；`2` 为配置/数据/能力不匹配；`3` 为证据或执行不完整。`compare` 在未处置的质量变化时非零。发布入口只接受 enforce 且为 0；报告同时保留各维度状态，不能只靠退出码解释结果。不得自动重试到绿、跳过坏题、降低阈值或晋升基线。

## 10 分阶段实施与完成条件

| 阶段 | 具体交付 | 验收条件 |
| --- | --- | --- |
| P0 证据闭环 | 严格 schema、runner/报告、隔离启动支持、现有 verify/Knowledge 报告导入、E01/E04/E06 确定性场景 | 两 checkout 产出可比较报告；无凭据可跑；故意错状态、缺证据与未触发故障均可区分，不依赖 LLM 裁判 |
| P1 核心真实场景 | E01–E10、Runtime/legacy 分层、故障屏障、只读及提交观察、预算 | 在授权范围内真实运行；所有计划 trial 有结果或缺测解释；重复确认/恢复无多次效果；违规无法被好文案抵消 |
| P2 比较和门禁 | 三次重复、配对对照、不可比判断、基线晋升、两种 release 脚本集成 | 故意引入一个真实行为退步能使 compare/enforce 失败，shell/PowerShell 均传播非零；基线不自动变化 |
| P3 扩展和运营 | 扩展集、保留集、人工标注流程；按需 PoC Pydantic Evals 或平台导出 | 新案例来自具体失败；裁判与平台可替换；现有安全报告及隐私边界不变 |

P0 先锁定证据与评分接口，P1 补完整场景，P2 接发布判断。每阶段独立提交；不预设日期或未经验证的人天承诺。最先安排的实现范围是 P0，而不是同时部署平台、迁移 Agent 和建设四十题。

必须验证评估器本身：合法轨迹可通过；错误对象、批准后偷换参数、未批准业务提交、重复业务效果、错误回执至少各一例失败；丢审计与 grader 异常为 unknown/incomplete。仅修改报告样本验证评分器还不够，另在隔离被测服务中注入一处业务退步，证明采集—评分—退出码全链路能发现。

实施测试按 [AGENTS.md §7](../../AGENTS.md#7-验证与-code-review)选择：P0 包含 schema/比较/脱敏/隔离与旧 smoke 行为回归；触及审批、恢复、并发与故障接缝时扩大失败路径回归并取得独立审查；接入门禁需测试成功、失败及子进程超时传播。新增安装依赖按安装变化验证，发布候选执行完整 gate。局部评估成功不替代发布验收。

## 11 设计边界与本轮验证

实施前需要落实的技术接缝是：当前 Runtime 证据关联导出、业务提交观察器完整性、子进程故障屏障与业务时钟注入。现有测试可借鉴这些边界，但并不等于已存在通用 eval 接口；P0/P1 若取不到证据，应报告不完整并补接缝，不降低规则。

本设计保留 `application_events`、`event_type + subtype + tags`、`context_type/context_ref`、默认 HITL、provider 特定块及已有恢复链路。Knowledge 的规则以[当前架构](./knowledge-system.md)为准：Note/Evidence 参与引用，Memory 只提供偏好，Brief 不进入 V1 核心 gate。未纳入的语音、真实外部操作、多租户与 UI 渲染，不在首版分数中宣称覆盖。

本轮只新增设计文档；完成源代码、相关测试定义、飞书文档和官方资料核对，提交前检查 Markdown 链接与 diff。未执行模型调用、全量 pytest、前端构建或 release gate，因为未修改产品代码或依赖。破坏性变化：无。主要剩余风险是小样本随机性、过程证据覆盖和旧 PRD 与当前实现的差异，已在比较、完整性和预期管理中分别约束。
