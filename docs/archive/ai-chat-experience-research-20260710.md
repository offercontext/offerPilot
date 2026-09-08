# OfferPilot AI 对话体验优化调研

日期：2026-07-10

分支：`feat/20260710-ai-chat-experience-research`

代码基线：`origin/main@6ad35d9`

飞书事实源：OfferPilot 主 Wiki，revision `763`

外部样例基线：`Shubhamsaboo/awesome-llm-apps@2892e8d`

## 结论摘要

OfferPilot 当前已经不是“缺一个聊天框”的阶段。主线具备双形态 Pilot、真实 token 流、工具过程、证据、持久化会话、写入审批、停止/重试、会话管理和最近一次写入撤销，基础可信度明显高于常见的 LLM demo。

下一阶段最值得做的不是增加更多装饰或暴露推理过程，而是把 Pilot 从“能聊天、能调工具”升级成“可控地完成求职任务”：

1. **先补齐上下文闭环**：当前业务页除 Offer 场景外基本只传 workspace 上下文，尚未真正携带当前 tab、实体和筛选条件。
2. **把审批从二选一升级为可修订**：用户应能在执行前编辑字段、补充说明或拒绝并给出反馈，而不只是确认/取消。
3. **解除 pending write 对其他工作的阻塞**：待审批应属于会话，不应让用户无法新建另一个只读会话。
4. **建立结构化 UI message/run 契约**：停止依赖前端从 `tool_calls` 字符串和 tool result 文本中猜 evidence，为实体卡、产物、引用、反馈和可恢复运行提供稳定底座。
5. **让输出成为可继续操作的产物**：简历建议、面试复盘、行动计划、Offer 对比应渲染为领域组件，可审阅、编辑、保存或导出，而不只是一段 Markdown。

建议采用“**控制与上下文先行，结构化消息契约随后，深度 Agent 工作区最后**”的渐进路线。不要直接照搬一个通用 Deep Research 界面。

## 1. 第一性原理

### 1.1 用户要的不是对话，而是推进求职任务

用户打开 Pilot 的真实目标通常是：理解当前局面、获得可信建议、完成一次数据操作或产出可复用材料。对话只是降低表达门槛的交互方式。

因此，AI 对话体验的最小闭环应是：

```text
表达目标 → 确认上下文 → 展示计划/进度 → 给出有依据的结果
        → 审阅或修订 → 执行/保存 → 验证结果 → 可恢复、可反馈
```

任何不能减少用户不确定性、操作成本或错误风险的 UI 元素，都不是核心优化。

### 1.2 六个必须保证的性质

| 性质 | 用户需要回答的问题 | OfferPilot 应保证什么 |
| --- | --- | --- |
| 定向 | Pilot 现在在帮我处理什么？ | 当前 tab、实体、筛选和能力边界可见、可移除 |
| 可见 | 它进行到哪里了？ | 展示任务级进度、工具状态和失败点，不展示隐藏思维链 |
| 有据 | 为什么得出这个结论？ | 证据去重、排序、可定位到本地实体或文档 |
| 可控 | 它会改什么？ | 风险分级、审批前可编辑、拒绝可反馈、写后可撤销 |
| 可恢复 | 中断或答错怎么办？ | 停止、重试、修订、分支、断线恢复互不冲突 |
| 连续 | 下次还需要从头讲吗？ | 会话短期记忆和用户可管理的长期记忆分层 |

### 1.3 成功标准

评价重点不应是“消息数”或“停留时长”，而应是：

- 用户是否以更少步骤完成目标任务。
- AI 是否使用了正确上下文和可核验依据。
- 写操作是否在用户理解影响后执行。
- 失败后是否能继续，而不是重头再来。
- 输出是否进入 OfferPilot 的业务闭环，而不是停留在聊天记录里。

## 2. 当前能力基线

### 已经做得好的部分

- Pilot 普通 tab 和业务页右侧栏共用助手能力，符合最新产品 IA。
- SSE 已覆盖 token delta、status、tool call/result、confirmation 和 completed 事件。
- 会话、pending action、pending clarification 和最近一次 undo 持久化。
- 过程时间线与 evidence 列表让用户知道 AI 查了什么。
- 确认卡已能展示目标、before/after、风险提示、workflow 和长草稿摘要。
- 支持停止当前回复、失败重发、确认失败重试/取消、写后撤销。
- 支持会话置顶、归档、重命名、删除和上下文移除。
- 无 key、provider 降级和工具不支持状态有明确提示。

这些能力已经覆盖 Microsoft HAX 中的能力说明、过程解释、停止/忽略和部分纠错原则。后续应保护现有信任边界，避免为“更自主”弱化 HITL。

### 真实走查观察

本次在本地开发数据上用内置浏览器走查 Pilot tab、业务页右侧栏和一个待审批的新建投递流程：

- 本地有 148 个未归档会话，左侧栏没有搜索、分组、分页或虚拟化，标题大量截断，历史会话定位成本很高。
- 点击“新建对话”后，只要其他会话存在 pending action，界面会自动重新选中第一个 pending 会话；用户无法暂时搁置审批并开启另一个只读任务。
- pending 卡只提供确认和取消，字段只读，部分正确的提议也必须整体取消后重新描述。
- 右侧证据面板会连续展示多个同名实体和原始 ISO 时间；本次 29 条投递来源中前几条均为重复的 `Smoke Co`，信号密度偏低。
- 业务页通用右侧栏没有传当前 view、选中实体或筛选条件；目前真正的实体上下文主要来自 Offer → application 绑定。
- assistant message 没有复制整段、编辑问题、重新生成、分支、反馈、模型/耗时/用量等消息级操作与元数据。
- 归档 API 支持 `include_archived`，但 UI 没有归档箱和恢复入口，归档在产品层面接近单向隐藏。

开发数据量不等于真实用户量，但它提前暴露了会话和证据结构在规模上升后的退化方式。

## 3. `awesome-llm-apps` 中值得借鉴的模式

该仓库是可运行模板集合，不是经过用户研究验证的 UX 标准，因此只把它作为实现模式来源，并用 HAX、LangGraph 和 AI SDK 官方资料校验方向。

### 3.1 Chat + Workspace，而不是把所有内容塞进消息气泡

`generative_ui_agents/ai-deep-research-agent` 将聊天放在左侧，把 plan、files、sources 放在独立 workspace；工具调用同时渲染为 live status card，并更新并行的产物区。

对 OfferPilot 的启发不是复制 38/62 布局，而是：

- 对话流负责意图、确认和叙述。
- 右侧上下文区负责当前任务状态、来源和产物。
- 简历改写、复盘草稿、Offer 对比、行动计划使用领域组件，不退化成 Markdown。

### 3.2 先生成计划，再让用户选择执行范围

`research_agent_gemini_interaction_api` 把复杂任务拆成 plan → select → research → synthesize；用户可以选择哪些子任务进入执行。

对 OfferPilot 更适合做成“仅复杂任务触发”的轻量 plan card，例如：

- “为下周三面试做准备”先列出资料检查、JD/简历差距、题目练习和日程更新。
- 用户勾选执行范围，再进入读工具或逐步审批写工具。
- 简单查询不展示计划，避免制造额外确认疲劳。

### 3.3 记忆必须可见、可管理

`multi_llm_memory` 把跨模型共享记忆放在独立侧栏，并允许用户查看。它证明了“检索到的记忆应成为 UI 对象”这一实现方向，但样例直接存 assistant answer，不适合原样采用。

OfferPilot 应区分：

- 会话短期上下文：当前 thread 的消息和 checkpoint。
- 领域事实：简历、投递、事件、复盘、知识库，继续以数据库为事实源。
- 用户偏好记忆：目标城市、岗位偏好、表达风格等，必须可查看、编辑、删除并带来源。

### 3.4 逐条反馈连接评测闭环

`agentic_rag_math_agent` 在每条 answer 后收集 👍/👎 并保留问题、答案和反馈。样例实现很简单，但揭示了当前 OfferPilot 的明显空白：没有消息级质量信号，无法判断优化是否真的有效。

OfferPilot 应优先收集“是否帮助推进任务”，负反馈再选原因：上下文错误、事实错误、没按要求操作、表达问题、过慢。默认本地保存，是否上传由用户明确选择。

### 3.5 输出要可下载或进入业务对象

`resume_job_matcher` 将匹配报告保存在 session 并提供 Markdown 下载。OfferPilot 已有更完整的数据模型，应进一步做到：

- “保存为简历定向版本”“保存为复盘草稿”“创建行动项”“打开对应实体”。
- 导出是兜底，不应替代结构化落库和 HITL。

## 4. 优化机会与优先级

| 优先级 | 机会 | 当前证据 | 推荐动作 |
| --- | --- | --- | --- |
| P0 | 真正的页面上下文 | 除 Offer 绑定外，新会话多为 workspace；capabilities 只按 general/nego 静态切换 | 传递 `view + entity refs + filters + selected text` 的快照；用可移除 chips 展示；实体卡补“问 Pilot” |
| P0 | pending 会话隔离 | 新建对话会被第一个 pending 自动抢回；composer 全局不可用 | pending 只阻塞所属 thread；rail 显示待处理徽标；允许开启其他只读会话 |
| P0 | 审批前修订 | ProposalCard 仅确认/取消 | 支持字段级 edit、补充说明、reject with feedback；高风险工具继续 always-confirm |
| P0 | 会话可找回 | 无搜索/日期分组/归档箱；自动标题仍是首条消息截断 | 增加搜索、今天/本周分组、归档箱恢复；后台异步生成标题，失败回退确定性标题 |
| P1 | 结构化 UI message/run | 前端从 JSON 字符串和 tool result 文本推断步骤与 evidence | 定义 versioned `parts[]` 与 message metadata；保留 message/run id、状态、来源、artifact、approval |
| P1 | 领域 Generative UI | assistant 主要输出 Markdown，工具只显示通用 timeline | 为 application/event/resume/note/offer/plan 定义卡片和审阅动作；未知工具保留安全 fallback |
| P1 | 证据质量 | 同实体重复、原始时间、上下文面板只按出现顺序截断 | 后端返回稳定 resource ref；前端按实体去重、相关度排序、人类时间格式、点击定位 |
| P1 | 回答修订与分支 | 只有失败重发，无编辑上一问/重新生成/从此处分支 | 使用 message id 建立 edit → regenerate → branch；保留原分支避免破坏审计 |
| P1 | 反馈与本地评测 | 无 assistant message 反馈入口 | 👍/👎 + 原因；记录 context/tool/run/latency，建立固定求职任务回归集 |
| P1 | 运行恢复和长任务 | SSE run 只在连接内存在，刷新后不能续传 | 持久化 active run/checkpoint；复杂任务支持后台运行、重连、从失败步骤恢复 |
| P1 | 可控记忆 | 有会话历史和领域数据，但无用户管理的偏好记忆 | 记忆来源、最后使用时间、查看/编辑/删除；不把模型回答自动当事实 |
| P1 | 成本与性能可见 | message 无 model/token/latency metadata；飞书已列为 v0.2 | 完成后折叠展示模型、耗时、token/估算成本；保持默认界面安静 |
| P2 | 多模态/语音 | composer 只支持文本 | 按既定 v0.3+ 规划接入附件、截图、音频；先定义隐私和生命周期，不提前扩 v0.1 |

## 5. 三种推进方案

### 方案 A：只补 UI 快赢

新增会话搜索、消息反馈、复制/重试、证据格式化、上下文 chips。

- 优点：改动小，用户可快速感知。
- 缺点：继续依赖字符串解析，审批编辑、产物、run 恢复会反复返工。
- 适合：作为 P0 收口的一部分，但不能作为完整路线。

### 方案 B：控制/上下文先行 + 消息契约渐进升级（推荐）

第一批修复 pending 隔离、页面上下文、审批编辑和会话找回；第二批引入 versioned message parts，再建设领域卡片、反馈、运行恢复和记忆。

- 优点：先解决用户最痛的控制问题，同时为 P1 能力建立稳定边界。
- 缺点：需要一次后端 schema、API、SSE、前端 type 和存量消息兼容设计。
- 适合：OfferPilot 当前成熟度和 v0.1 → v0.2 节奏。

### 方案 C：直接升级为完整 Deep Agent 工作区

一次性引入计划、多 Agent、后台运行、文件工作区、长期记忆和完整 generative UI。

- 优点：长期能力上限高。
- 缺点：范围过大，会把“求职闭环”稀释成通用 Agent 平台；显著增加状态、权限、成本和失败恢复复杂度。
- 结论：不推荐当前采用。只抽取 plan/workspace/typed tool UI 三个模式。

## 6. 推荐的契约方向

不要立即替换现有 API；可以在 `pilot-sse-v2` 和新 message payload 中渐进引入：

```json
{
  "message_id": "msg_...",
  "run_id": "run_...",
  "role": "assistant",
  "status": "streaming | completed | interrupted | failed | waiting_approval",
  "parts": [
    { "type": "text", "text": "..." },
    { "type": "tool", "tool_call_id": "...", "name": "list_applications", "state": "completed" },
    { "type": "source", "resource": { "type": "application", "id": "31" }, "title": "..." },
    { "type": "artifact", "artifact_type": "interview_plan", "data": {} },
    { "type": "approval", "action_id": "...", "allowed": ["approve", "edit", "reject"] }
  ],
  "metadata": {
    "model": "...",
    "latency_ms": 0,
    "tokens_in": 0,
    "tokens_out": 0,
    "context_snapshot_id": "ctx_..."
  }
}
```

关键约束：

- UI message 是展示事实源，model message 是经过裁剪的推理输入，两者不要继续混为一个 `content` 字段。
- source 使用稳定资源引用，不把整行数据库 JSON 永久复制到消息里。
- context snapshot 记录“本轮实际看到了什么”，同时允许用户移除敏感上下文。
- approval action 有独立 id 和状态，支持 edit/reject feedback，并保持幂等。
- 不存储或展示模型隐藏 chain-of-thought；只存计划、工具事实、证据和用户可验证的摘要。

## 7. 建议实施顺序

### P0：v0.1 对话收口

1. 修复 pending action 跨会话抢占，新会话和其他只读会话保持可用。
2. 把当前 view/entity/filter 注入 ChatPanel，并补实体卡“问 Pilot”。
3. ProposalCard 支持 edit/reject feedback；继续保留 always-confirm 和 undo。
4. 会话搜索、日期分组、归档箱恢复、可靠标题。
5. 证据去重、时间格式化、点击定位。

### P1：v0.2 结构化助手

1. 设计并迁移 `pilot-sse-v2` / UI message parts。
2. 增加领域卡片和 artifact review/save/export。
3. 加入 edit/regenerate/branch 和消息级反馈。
4. 增加 active run 持久化、断线恢复和复杂任务 plan card。
5. 上线可管理的用户偏好记忆、用量/成本可见和 Skill UI。

### P2：v0.3+

按既有产品版本接入截图、附件、音频/转写、模拟面试和谈薪深度工作流。不要因对话 UI 可承载就提前扩大版本边界。

## 8. 验收指标

默认本地统计，用户明确同意后才上传匿名指标：

| 维度 | 指标示例 |
| --- | --- |
| 任务完成 | 从提问到 answer / saved artifact / approved write 的完成率与耗时 |
| 上下文正确 | 用户移除/纠正上下文比例；错误实体导致的负反馈比例 |
| 控制 | approval edit、reject、undo 比例；pending 跨会话阻塞必须为 0 |
| 恢复 | 中断后恢复成功率；retry 后成功率；重复写入率 |
| 证据 | 有依据回答占比、source 定位成功率、重复 source 比例 |
| 质量 | 消息 helpful rate；负反馈原因分布；固定任务回归通过率 |
| 性能 | time-to-first-token、首个工具状态时间、完整任务耗时、P95 |
| 成本 | 每个成功任务 token/估算成本，而不是单次 message 成本 |

## 9. 风险与非目标

- 不把工具调用数量当作智能程度；多工具可能只是低效。
- 不暴露隐藏思维链；“解释”只展示计划、事实、证据和影响。
- 不因自动审批方便而取消高风险动作的 always-confirm。
- 不把聊天记录、模型回答或点击行为未经审阅直接写成长期事实记忆。
- 不以 `awesome-llm-apps` 的 demo UI 代替真实用户测试；优先用 5–8 个高频求职任务做可用性走查。
- 不在本轮研究中改变现有数据库、API 或产品行为。

## 10. 资料来源

- [OfferPilot 主 Wiki](https://ycn8095q3nc7.feishu.cn/wiki/K6BQw1X5Piksm2kDex3cMQMenvf)，本次回读 revision `763`。
- [Awesome LLM Apps](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/2892e8dc9049e1d71d18079dc22c5b9b72fadbfe)。
- [AI Deep Research Agent：tool cards + workspace](https://github.com/Shubhamsaboo/awesome-llm-apps/tree/2892e8dc9049e1d71d18079dc22c5b9b72fadbfe/generative_ui_agents/ai-deep-research-agent)。
- [Research Planner：plan → select → execute](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/2892e8dc9049e1d71d18079dc22c5b9b72fadbfe/advanced_ai_agents/single_agent_apps/research_agent_gemini_interaction_api/research_planner_executor_agent.py)。
- [Multi-LLM Memory：可见记忆面板](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/2892e8dc9049e1d71d18079dc22c5b9b72fadbfe/advanced_llm_apps/llm_apps_with_memory_tutorials/multi_llm_memory/multi_llm_memory.py)。
- [Agentic RAG Math Agent：逐条反馈](https://github.com/Shubhamsaboo/awesome-llm-apps/blob/2892e8dc9049e1d71d18079dc22c5b9b72fadbfe/rag_tutorials/agentic_rag_math_agent/app/streamlit.py)。
- [Microsoft HAX Guidelines for Human-AI Interaction](https://www.microsoft.com/en-us/haxtoolkit/ai-guidelines/)。
- [HAX：Show contextually relevant information](https://www.microsoft.com/en-us/haxtoolkit/guideline/show-contextually-relevant-information/)。
- [HAX：Support efficient correction](https://www.microsoft.com/en-us/haxtoolkit/guideline/support-efficient-correction/)。
- [HAX：Encourage explicit feedback on individual outputs](https://www.microsoft.com/en-us/haxtoolkit/pattern/g15-a-encourage-explicit-feedback-on-individual-system-outputs/)。
- [LangGraph Human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)。
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。
- [Vercel AI SDK：UIMessage](https://ai-sdk.dev/docs/reference/ai-sdk-core/ui-message)。
- [Vercel AI SDK：Message Metadata](https://ai-sdk.dev/docs/ai-sdk-ui/message-metadata)。
- [Vercel AI SDK：Resume Streams](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-resume-streams)。
