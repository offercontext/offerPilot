# Pilot 确定性 JD 确认设计

**设计基线：** `feat/20260805-application-jd-versions@6e18c1e`

**设计状态：** 已复审通过，进入 TDD 实施。

## 1. 背景

Application JD 版本功能已完成 UI 保存、历史、来源、CAS、幂等及下游版本冻结。受控 Provider 的真实浏览器 `Stage all` 已证明 JD、Opportunity Fit、材料包和面试准备业务链路可用。

当前发布阻塞集中在 Pilot 创建 JD 版本的第一步：Pilot 需要模型主动调用 `save_application_jd_version` 才能展示确认卡。真实 Provider 的表现不稳定：

- 部分模型能完成严格 Triage，但只返回普通文本，不调用写工具；
- 部分模型在直接 Agent 探针中会调用写工具，在真实 Chat API 多轮路由中只调用只读工具；
- 支持工具调用的模型又可能在流式响应或长结构化请求中超时、断流或返回 500；
- 未生成确认卡时，系统不会消费 token 或写入 JD，这一安全边界正确，但完整浏览器验收无法继续。

“用户明确要求保存 JD”是确定性的产品动作，不应依赖模型是否偶然选择正确工具。本设计把该动作改为服务端确定性确认流程，同时保留现有 Pilot 会话、HITL、幂等、CAS、审计和版本 Repository。

## 2. 目标与非目标

### 2.1 目标

- 用户通过 Pilot 快捷入口或明确自然语言指令进入 JD 保存流程；
- 服务端稳定生成 `save_application_jd_version` 待确认卡，不调用 Provider；
- 用户确认后直接调用现有 `ApplicationJDService`，不调用 Provider；
- UI 与 Pilot 继续共享相同的 JD 版本、幂等、CAS、来源和历史语义；
- 普通讨论、查看、总结和其他 Agent 工具保持现有行为；
- 明确写入流程在没有可用 AI 配置时仍可工作。

### 2.2 非目标

- 不构建通用自然语言意图平台；
- 不把其他 Pilot 写工具一并改成确定性流程；
- 不自动分析、摘要、重写或补全 JD；
- 不访问 `source_url`，不抓取招聘平台；
- 不自动触发 Opportunity Fit、材料包、面试准备、模拟面试或其他领域写入；
- 不新增第二套审批系统、JD 保存 API、数据库表或 Provider 路由功能。

## 3. 方案选择

采用“服务端确定性动作路由”方案。

不采用 `tool_choice` 强制模型调用，因为不同 Provider 对强制工具、Schema 和流式工具调用的支持不一致，仍然依赖模型生成参数。不采用独立 Pilot JD API，因为它会重复现有 Chat pending-action 和 Application JD 保存契约。

总体流程：

```text
用户消息或 Pilot 快捷入口
→ DeterministicPilotActionRouter
→ 收集唯一 Application 与 JD 原文
→ 服务端构造并持久化 PendingAction
→ 用户确认或拒绝
→ 现有工具验证器与 ApplicationJDService
→ 服务端固定中文结果与历史入口
```

该路径从进入动作到确认结果均不调用 Chat Provider、标题 Provider 或任何其他 AI 服务。

## 4. 组件边界

### 4.1 DeterministicPilotActionRouter

新增独立纯逻辑模块，负责：

- 识别公开快捷动作 `application_jd_save`；
- 识别严格限定的自然语言命令；
- 判断当前会话是否绑定唯一 Application；
- 管理“等待 JD 原文”到“待确认”的状态转换；
- 生成服务端 `tool_call_id`、幂等键和固定中文文案；
- 构造 `PendingAction`，但不执行工具。

Router 不访问 Provider，不写 JD，不直接操作其他领域 Repository。Application、当前 JD 版本和 Chat 状态通过明确依赖传入。

### 4.2 内部工具 Registry

`save_application_jd_version` 保留现有：

- JSON Schema；
- `always_confirm=True`；
- 参数验证器；
- 描述器；
- `ApplicationJDService(source_kind="pilot")` handler。

为工具增加内部可见性元数据，例如 `model_visible=False`。Agent 构造发给模型的工具列表时排除该工具，但确认接口仍能从完整 Registry 获取验证器与 handler。

升级前已经持久化的 JD 待确认卡仍由相同确定性确认路径处理，不失效。

### 4.3 Chat API

现有 `/api/chat` 和 `/api/chat/stream` 在调用 Agent 之前执行 Router。只有 Router 返回“未处理”时才进入现有模型流程。

现有 `/api/chat/confirm` 和 `/api/chat/confirm/stream` 在读取 pending action 后，若工具为 `save_application_jd_version`，直接进入确定性确认处理器；其他工具继续走现有 Agent resume 流程。

确认接口必须在判断 pending 工具之后才加载 Chat Provider，因此确定性 JD 确认不依赖有效的 AI 配置。

### 4.4 前端

Pilot 增加轻量“保存 / 更新岗位资料”快捷入口，不改变整体布局。入口只向现有 Chat API 发送公开 `pilot_action`，不调用 JD 保存 API。

现有确认卡继续负责展示、确认、拒绝和恢复；可编辑字段仅为 `jd_text` 与 `source_url`。Application、预期版本、来源入口和幂等键不可由客户端编辑。

## 5. 请求契约

Chat 请求增加可选字段：

```ts
type PilotAction =
  | {
      type: "application_jd_save";
      jdText?: string;
      sourceUrl?: string | null;
    };
```

约束：

- `pilot_action` 仅是进入 Chat 工作流的公开动作标识，不是写 API；
- 客户端不能传 `application_id`、`source_kind`、`expected_current_version_id` 或 `idempotency_key`；
- `application_id` 必须来自 `context_type=application` 与 `context_ref`；
- `source_kind` 固定为 `pilot`；
- 当前版本和幂等键由服务端生成；
- 旧客户端不传该字段时行为不变；
- 非法类型或未知 action 返回 422，不调用 Provider，不创建会话写入状态。

普通和流式 Chat 必须返回相同业务结果。流式路径只以 SSE 包装固定事件，不产生模型 delta。

## 6. 自然语言触发规则

自然语言入口只支持两类确定性形式：

1. 命令本身，例如“保存 JD”“更新当前岗位描述”“给当前投递补充岗位资料”；
2. 命令后使用中文冒号、英文冒号或换行携带完整正文。

识别规则采用有限词表和完整匹配：

- 动词：`保存`、`更新`、`补充`、`录入`；
- 对象：`JD`、`jd`、`岗位描述`、`职位描述`、`岗位资料`；
- 可选目标：`当前投递`、`这个投递`、`本次投递`。

如果命令后存在正文，必须有明确的 `:`、`：` 或换行分隔；分隔后的内容原样作为 JD，不做 Unicode 规范化、摘要或清洗，只使用现有 JD 校验检查空白和大小。

以下内容不得触发：

- 否定：`不要保存 JD`；
- 疑问：`如何保存 JD？`；
- 只读：`查看 JD`、`总结岗位描述`、`分析这份 JD`；
- 引用或讨论：`“保存 JD”这个按钮是什么意思`；
- 命令后没有分隔符却附带其他自然语言，例如 `更新 JD 需要注意什么`。

未匹配的消息进入普通 Agent。Router 不使用模糊相似度、模型分类或正则推断 URL。

## 7. 状态机

```text
idle
├─ 明确动作且已有 JD → pending_confirmation
├─ 明确动作但缺 JD → collecting_jd
└─ 非明确动作 → normal_agent

collecting_jd
├─ 用户提交非空 JD → pending_confirmation
├─ 用户取消 → cancelled
└─ Application 不可见 → missing

pending_confirmation
├─ 确认 → confirmed / stale / validation_error / result_unknown
├─ 拒绝 → rejected
└─ 重挂载 → 恢复同一卡片
```

### 7.1 收集 JD

缺少正文时，使用现有 `pending_clarification` 持久化：

- tool name 为内部 JD 保存工具；
- `tool_call_id` 为服务端随机 ID；
- 只保存 Application 身份和可选 `source_url`；
- 固定问题为“请粘贴完整岗位描述”；
- 下一条用户消息整体作为 JD 原文。

由于该工具不再向模型暴露，此类 clarification 只能由 Router 创建，后续消息可安全确定性接管。

### 7.2 生成确认卡

JD 原文完整后：

- 重新校验 Application 可见性；
- 读取当时的当前 JD 版本；
- 冻结 `expected_current_version_id`；
- 生成 16–128 位 ASCII 幂等键；
- 构造有效工具参数；
- 使用现有验证器检查；
- 原子持久化 pending action 和固定助手消息；
- 返回 `confirmation_required`。

确认卡持久化成功前不返回成功响应。响应丢失或页面重挂载后，从会话恢复相同 token、参数和幂等键。

### 7.3 已有待处理状态

- 已有 JD 确认卡：重复快捷入口或重复明确命令返回原卡；
- 正在等待 JD：下一条消息视为原文，快捷入口不重置流程；
- 已有其他工具确认卡：不覆盖，提示先处理当前动作；
- 已归档或删除会话：不创建或恢复写入动作。

## 8. 确认执行

确定性确认处理器复用现有确认 token、每会话锁、参数编辑验证和 Registry handler。

执行顺序：

1. 读取会话和 pending action；
2. 恒定时间比较 confirmation token；
3. 在确认锁内重新读取 pending，防止替换或重复确认；
4. 只允许编辑 `jd_text` 与 `source_url`；
5. 使用完整 Registry 验证最终参数；
6. 调用 `ApplicationJDService.create_version(source_kind="pilot")`；
7. 以 pending 字段 CAS 清除原卡，持久化工具结果和固定中文助手消息；
8. 返回创建或幂等重放得到的版本 ID、版本号、来源和历史入口。

确认成功后的固定文案不调用模型，例如“岗位资料已保存为 v2”。拒绝时不执行 handler，原子清除 pending 并写入“已取消保存岗位资料”。

现有 auto-approve 配置对该动作无效，始终要求逐次人工确认。

## 9. 幂等、并发与错误语义

### 9.1 幂等与并发

- 同一确认 token 并发提交只能有一个处理器进入 handler；
- 响应丢失且 pending 尚未清除时，原 token 与原 key 重试复用同一版本；
- pending 已清除但响应丢失时，前端通过 JD 当前版本和历史回读确认结果，不创建新 key；
- 两个会话冻结同一当前版本时，先确认者成功，后确认者收到 stale 冲突；
- 模型永远不能提供或覆盖幂等键、Application ID、来源或预期版本。

### 9.2 错误矩阵

| 场景 | 结果 | Pending 处理 |
|---|---|---|
| 缺少唯一 Application 上下文 | 固定中文提示选择投递 | 不创建 |
| 空白、超限、非法 URL | 422 | 保留卡片供编辑 |
| Application 不存在或不可见 | 404 | 清除并停止 |
| 当前 JD 已变化 | `409 application_jd_stale_current_version` | 用原文和 URL生成新卡、新 key、新 expected ID，明确提示重新确认 |
| 幂等键冲突 | `409 application_jd_idempotency_conflict` | 原卡失效；生成新卡并要求重新确认 |
| 数据库结果未知 | 5xx/result unknown | 保留原卡、token 和 key，只允许原尝试重试 |
| 确认成功或幂等重放 | 200 | 清除原卡，展示版本结果 |
| 用户拒绝 | 200 | 清除原卡，不写 JD |

stale 或 idempotency conflict 不能自动再次写入；新的确认卡必须由用户重新确认。

## 10. UI 与可访问性

- 快捷入口只在唯一 Application 上下文中启用；
- 无当前 JD 时显示“保存岗位资料”，已有当前版本时显示“更新岗位资料”；
- 不自动打开、自动提交或自动确认；
- 确认卡展示公司、职位、拟创建版本号、当前版本、JD 预览、字符数、来源 `Pilot`、可选 URL 和“不访问链接”说明；
- URL 仅作为文本和复制内容，不渲染可点击链接；
- 结果未知时冻结编辑和其他提交，只保留原尝试重试；
- 键盘焦点、Escape、Tab 顺序和屏幕阅读器名称沿用现有确认卡标准；
- 普通聊天界面和页面布局不改造。

若实现包含前端改动，合并前必须提供亮色、中文、宽屏截图，至少覆盖快捷入口、等待原文、确认卡和成功历史四个状态。

## 11. 安全与隐私

- JD 原文、source URL、确认参数和 Provider key 不写入诊断日志；
- 日志只记录动作类别、会话/Application 哈希、状态、错误码和耗时；
- source URL 永不发起 HTTP 请求；
- JD 原文不进入模型，因此其中的提示注入文本不会被执行；
- 不创建 Opportunity Fit、材料、面试、Knowledge、Offer、提醒或 Application 状态写入；
- 不允许客户端伪造 `source_kind=pilot`；
- 不允许含糊自然语言触发写入卡。

## 12. 兼容性与迁移

- 不新增数据库迁移；复用 Conversation 的 pending action 与 pending clarification 字段；
- 不新增 Chat 表上的 JD 专用状态；
- 旧 Chat 请求和普通 Agent 行为保持兼容；
- 旧 JD 待确认卡继续可确认；
- `save_application_jd_version` 仅从模型工具列表隐藏，不从内部 Registry、审计或确认处理器删除；
- UI JD 保存 API、ApplicationJDService 和所有下游 `jd_version_id` 契约不变。

## 13. 测试设计

### 13.1 纯逻辑

- 快捷动作、命令式自然语言、冒号/换行正文；
- 否定、疑问、引用、只读和模糊表达不触发；
- CJK、emoji、换行、60KB 边界和空白正文；
- 原文不改写、不提取 URL；
- 固定文案和服务端 key 格式。

### 13.2 API 与 Repository

- `/api/chat` 与 `/api/chat/stream` 结果一致；
- 显式动作、追问、重挂载、原卡恢复均有 Provider 调用次数 0；
- 新会话标题生成 Provider 调用次数 0；
- 普通消息仍进入 Agent；
- 模型收到的工具列表不包含 JD 保存工具；
- 内部 Registry 和确认 handler 仍包含该工具；
- 普通确认、流式确认、拒绝、编辑、重复确认和并发确认；
- stale 时原文保留、新 key 和新 token、再次人工确认；
- 结果未知使用原 key 重试；
- Application 删除、会话归档和 pending 替换；
- 零跨领域写入和 source URL 零外联。

### 13.3 前端挂载

- 无/有当前 JD 的快捷入口文案；
- 点击只发送 `pilot_action`，不调用 JD 保存 service；
- 等待原文、确认卡、编辑、拒绝、成功和历史回读；
- 未确认时 JD 版本数不变；
- 结果未知冻结并原尝试重试；
- 普通聊天不受影响；
- 所有 AI、JD 写入和导航 spy 符合预期。

### 13.4 浏览器验收

使用临时隔离目录和中文亮色案例：

```text
投递详情保存 UI JD v1
→ Pilot 点击“更新岗位资料”
→ 粘贴 JD 原文
→ 确认卡出现（Provider 调用 0）
→ 人工确认
→ Pilot JD v2 回读（Provider 调用仍为 0）
→ Triage、材料包、面试准备使用 v2
```

Stage A 必须证明 Pilot JD 确认完全不依赖 Provider。Stage B 仍使用真实 Provider 验证下游 AI；其失败不得反向影响已经确认的 JD v2。

## 14. 建议实现边界

建议新增：

- `src/offerpilot/ai/deterministic_actions.py`
- 对应纯逻辑测试。

建议修改：

- `src/offerpilot/ai/tools.py`
- `src/offerpilot/ai/agent.py`
- `src/offerpilot/repositories/chat.py`
- `src/offerpilot/api.py`
- Chat/Pilot 请求类型、服务、快捷入口和确认卡测试；
- Application JD smoke、浏览器 Harness 与发布报告。

禁止借机修改：

- Application JD 数据模型和迁移；
- Opportunity Fit、材料包、面试准备或模拟面试契约；
- Provider 配置、fallback、证据校验或真实 AI 重试次数；
- 其他 Pilot 写工具的行为。

## 15. 验收标准

设计实现只有在以下条件全部满足时才可进入合并审核：

- 显式 Pilot JD 保存从触发到确认结果的 Provider 调用次数为 0；
- UI 与 Pilot 继续共享相同版本 Repository、幂等和 CAS；
- 未确认、拒绝、冲突和结果未知均无重复版本；
- 普通 Chat 与其他工具回归通过；
- 受控浏览器 Stage all 通过；
- 真实 Provider Stage B 至少完整通过一次；
- 后端完整分组、前端完整分组、Ruff、Mypy、TypeScript、构建、local smoke/verify 和独立代码复审通过；
- 前端有改动时，亮色中文截图已由用户确认；
- 工作区干净，未在报告中保存密钥、JD、简历或模型原文。
