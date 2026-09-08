# OfferPilot Agent Harness 调研与改进建议

日期：2026-08-16

## 结论

OfferPilot 的 harness 并不是“做得太少”，而是已经累积了大量正确但分散的安全机制：冻结输入、幂等键、lease/CAS/fencing、严格证据校验、受控 Provider、真实 Provider、CDP 网络审计、跨领域写入审计和脱敏日志。当前主要问题是这些机制没有被一个统一的运行协议、诊断协议和评测数据集串起来，导致：

1. API、前端和 harness 对同一错误的恢复动作可能漂移；
2. 真实 Provider 的随机失败会阻塞后续阶段，难以区分产品回归和外部波动；
3. 诊断依赖日志和脚本内推断，缺少可按操作关联的结构化 trace；
4. 多个 PowerShell harness 逐步膨胀，验证规则重复且难以演进；
5. 单次端到端“通过/失败”无法回答真实成功率、修复率和延迟分布。

这次用户遇到的失败属于第 1 类：后端已把 `excerpt_mismatch` 判为不可验证的终态，但 Interview Studio 仍显示“保留原 key，可安全重试”。同时，模型被要求逐字复制 `source/path/excerpt`，把一个本可由程序完成的确定性映射交给了概率模型，增加了不必要的失败面。

## 本轮已落地的改进

### 1. 终态错误与未知结果分流

- `mock_interview_unverifiable` 不再显示“使用原 key 重试”。
- 界面进入不可继续提交的终态，明确提示重新开始。
- 重新开始前先删除失败 Attempt，再生成新的 Attempt key / question key。
- Provider/网络结果未知仍保留冻结输入和原 key，对账语义不变。

### 2. 提问引用改为“模型选 ID，服务端展开”

Provider 不再复制 `source/path/excerpt`，只返回：

```json
{
  "question": "……",
  "evidence_ids": ["ev_003"]
}
```

服务端从本次冻结目录中把 `ev_003` 展开为精确的 `source/path/excerpt`，再沿用原有严格校验和持久化格式。这样没有放宽证据契约，却从结构上消除了标点、空格、Unicode 或长文本复制造成的 `excerpt_mismatch`。

这个模式与仓库 Knowledge Brief 已采用的 `evidence_ids` 思路一致，也符合“让模型做选择，让程序做确定性转换”的边界。

## 当前 harness 做得好的地方

- **状态安全**：幂等键、lease、generation revision、provider token 和 CAS 能防止迟到结果覆盖新 owner。
- **证据安全**：引用必须落在冻结目录中，无法验证时 fail-closed。
- **写入边界**：浏览器验收会检查跨领域写入，避免一个功能顺手污染 Knowledge、Memory、Offer 等领域。
- **网络边界**：浏览器只允许访问本地静态资源和 `/api`，Provider 出站单独经代理审计。
- **环境隔离**：真实验收复制配置到临时数据目录，结束后清理服务、浏览器和数据库。
- **分层门禁雏形**：已有纯函数/API 测试、受控 Provider、real-AI 和 CDP 浏览器闭环。

这些基础不应推倒重来。

## 当前设计问题

### P1：恢复策略没有单一事实源

同一个 `error_code` 的 HTTP、Attempt 状态、是否保留 key、是否允许重试、前端按钮和 harness 断言分别散落在 API、repository、React 和 smoke 脚本中。本次 `mock_interview_unverifiable` 漂移就是直接结果。

建议新增统一的恢复策略表，例如：

```text
error_code
→ disposition: retry_same_key | restart_new_attempt | reload_source | edit_input
→ attempt_retention
→ input_frozen
→ user_action
```

API、前端和 harness 都通过同一份契约测试消费，禁止各自写文案推断。

### P1：模型承担了不必要的确定性工作

逐字复制路径和摘录并不能提升提问质量，却会制造 `excerpt_mismatch`。DeepSeek 官方也明确提示 JSON 模式仍可能返回空内容，并要求在提示中明确 JSON、提供示例、合理设置输出上限以避免截断。[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)

应继续把“自由生成”缩小到真正需要模型判断的字段：

- 模型生成问题文本；
- 模型选择有限 evidence ID；
- 服务端展开引用、排序、规范化、计算指纹和保存。

后续可把 Mock Interview feedback 的引用也迁移到同样的 ID 选择协议。

### P1：缺少 Provider 能力探针与适配矩阵

当前主要通过 `supports_json_schema` 一个布尔值决定结构化输出策略，但 Provider 能力至少还包括：

- JSON object 与 JSON Schema 是否支持；
- strict tool calling 是否支持、是否需要 beta endpoint；
- thinking 模式是否开启；
- thinking + tool call 时是否必须回传 `reasoning_content`；
- streaming 中断语义；
-支持的 JSON Schema 子集和输出 token 上限。

DeepSeek 的 strict tool calling 目前需要 beta endpoint，而且官方列出的 schema 子集不支持数组的 `minItems/maxItems`；thinking 模式的 tool call 还要求后续请求带回 `reasoning_content`。[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls)、[DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)

建议启动时执行零写入、低成本 capability probe，得到本次运行的能力快照；不满足能力时提前选择 text-JSON + 本地 validator，或 fail-fast，而不是进入完整业务流程后才暴露差异。

### P1：端到端脚本过重，阶段之间耦合

当前 7 个主要浏览器/real-AI 脚本合计数千行，最大的 Application JD harness 超过 1000 行。大量流程、轮询、诊断和清理逻辑直接写在 PowerShell 中，造成：

- 规则重复；
- 通过源码字符串断言来测试脚本行为；
- 前一随机阶段失败后，后续阶段完全没有证据；
- 修复脚本本身的成本逐渐接近修业务代码。

Anthropic 的经验是先使用最简单可行架构，仅在评测证明必要时增加复杂度，并通过逐项消融判断哪些 harness 组件真正有价值；其长任务 harness 也强调把工作拆成可验证的小块并用结构化产物交接。[Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)、[Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps)

建议把 harness 拆成：

1. Python 场景运行器：状态机、请求、断言、清理；
2. JSON 场景清单：输入、允许写入、预期操作和恢复语义；
3. CDP 驱动器：只负责真实 UI 操作与网络证据；
4. PowerShell 薄入口：配置环境并调用运行器。

### P1：诊断不是一等公民

当前能记录 `failure_category`、request ID hash 和部分 fingerprint，但不同阶段的字段不统一，部分结论仍需从日志文本或数据库状态反推。

OpenAI Agents SDK 把 workflow、turn、generation、tool、guardrail、handoff、transcription 和 speech 都建模为 trace/span，并支持 workflow name、trace ID、group ID 以及关闭敏感输入输出记录。[OpenAI Agents tracing](https://openai.github.io/openai-agents-python/tracing/)、[OpenAI Agents run configuration](https://openai.github.io/openai-agents-python/running_agents/)、[OpenAI Agents sensitive-data configuration](https://openai.github.io/openai-agents-python/config/)

OfferPilot 可采用本地、脱敏的统一 trace envelope：

```text
run_id / scenario_id / operation_id
attempt_id / generation_revision / idempotency_key_hash
provider / model / capability_snapshot_hash
input_fingerprint / schema_fingerprint
started_at / first_byte_ms / completed_ms
provider_outcome / validator_stage / failure_category / repair_count
lease_owner_transition / final_disposition
browser_request_id / response_error_code
```

默认不记录 JD、简历、回答、提示词、模型原文和密钥。

### P1：只有“单次是否通过”，没有可靠性评测

真实 Agent 需要同时评测最终结果、单步选择和完整轨迹。LangSmith 官方把 Agent eval 分为 final response、single step 和 trajectory，且建议组合使用，而不是只看最终页面是否出现。[LangSmith agent evaluation approaches](https://docs.langchain.com/langsmith/evaluation-approaches)、[Agent Evals](https://docs.langchain.com/oss/python/langchain/test/evals)

建议建立脱敏固定数据集，至少记录：

- contract pass rate；
- terminal semantic failure rate；
- provider/network unknown rate；
- repair rate与 repair 成功率；
- p50/p95 首字节和总耗时；
- 平均 Provider 调用次数；
- 同 key 重放是否 0 次额外调用；
- 每个阶段的轨迹正确率；
- 模型/配置/提示版本之间的对比。

真实 Provider 门禁应使用小样本、有限次数和明确预算，不用“失败就一直重跑”。

### P2：恢复与重放缺少统一 checkpoint 语义

OfferPilot 已有数据库快照和 fencing，但每个领域自行实现恢复。LangGraph 的 durable execution 要求状态可序列化，把非确定性外呼放入可 checkpoint 的 task，并在恢复时读取已保存结果而不是重复外呼；它也明确要求外部副作用具备幂等性。[LangGraph Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)、[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

不建议引入 LangGraph 依赖来重写产品，但可以借用其规则：

- 每次 Provider 外呼是独立 operation checkpoint；
- checkpoint 输入必须可序列化并有 fingerprint；
- 相同 operation 的恢复先读持久化结果；
- 只有 lease 过期且 fencing 成功的新 owner 才能再次外呼；
- workflow 版本变化不能让在途 Attempt 按错误步骤恢复。

## 建议实施顺序

### 第一阶段：统一可靠性契约

1. 建立共享 `error_code → recovery disposition` 契约与跨层测试。
2. 将其余需要精确引用的模型输出逐步改成 evidence ID 选择。
3. 定义统一脱敏 trace envelope，并让 API、受控 Provider、real-AI 和 CDP 复用。

### 第二阶段：重构 harness

1. 新建 Python 场景运行器和 JSON 场景清单。
2. 先迁移 Mock Interview，再迁移 Story、Offer、JD。
3. 保留现有脚本并行一段时间，对比新旧结果后删除重复逻辑。

### 第三阶段：建立 eval 数据集与看板

1. 收录历史脱敏失败类别与人工构造边界样本。
2. 每个模型/配置运行有限重复实验。
3. 以成功率、修复率、延迟、成本和轨迹正确率作为放行依据。
4. 把生产失败 trace 经过脱敏和人工确认后回流到离线数据集。LangSmith 官方也推荐“线上失败回流离线数据集”的闭环。[LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation)

## 不建议做的事

- 不因一次 `excerpt_mismatch` 放宽逐字证据验证。
- 不把 Provider 500、网络未知和语义失败统一成“重试”。
- 不用增加无界重试来提高表面通过率。
- 不立即引入多 Agent evaluator 增加费用；先用确定性 validator 和小型固定数据集。
- 不把所有现有 harness 推倒重写；先迁移一个领域并做新旧对照。
