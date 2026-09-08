# Pilot 引导式岗位评估设计

日期：2026-07-21
状态：已复审通过
范围：将既有 Opportunity Fit 的 Triage、Deep Review 和材料交接接入绑定 Application 的 Pilot 会话；不改变 Opportunity Fit API、数据模型或材料包生命周期。

## 1. 目标与非目标

### 目标

Pilot 负责把用户从“评估当前岗位”引导到可审阅的岗位评估卡片，复用既有 Opportunity Fit API 保存冻结快照、生成 Triage、生成 Deep Review，并在用户明确选择后把冻结 Resume/JD 交给现有 Material Kit 抽屉。

### 非目标与安全边界

- 只允许在已绑定 Application 的 Pilot 会话中启动，不提供全局岗位选择器。
- 只接受用户粘贴的 JD；不读取 `job_url`、不抓取 URL、不访问招聘平台。
- 不新增 AI 工具调用、数据库表、迁移或 API 契约。
- 不改变 Application 投递状态，不自动创建 Material Kit，不自动生成或接受简历改写，不自动投递。
- 不把模型输出渲染为 Markdown 聊天消息，也不伪造工具进度或平台回执。
- 用户数据、JD、公司/职位名、简历名、AI 正文和证据摘录保留原文；固定界面文案使用中文。

## 2. 现有能力复用

Pilot 仅负责入口、引导、确认和承载卡片；数据与写入仍由现有能力负责：

| 能力 | 复用接口/模块 | Pilot 责任 |
| --- | --- | --- |
| 创建 Triage | `POST /api/applications/{application_id}/opportunity-fit-reviews` | 收集输入、展示发送确认、提交幂等键 |
| 历史评估 | 既有列表/详情接口 | 恢复冻结快照和已生成结果，不读取当前漂移来源 |
| Deep Review | `POST /api/applications/{application_id}/opportunity-fit-reviews/{review_id}/deep-review` | 仅在有 Triage 且用户再次确认后调用 |
| 材料交接 | 现有 Material Kit 抽屉和预填 props | 传递冻结 `resume_id` 与 `jd_text`，不写材料包 |
| 错误处理 | Opportunity Fit 与材料流程的安全中文映射 | 按错误码/HTTP 状态展示固定文案，禁止透传原始错误 |

既有 Opportunity Fit 的冻结快照、证据门控、幂等和软删除语义保持不变。历史评估展示的 JD 与 Resume 必须来自该评估的冻结快照。

## 3. 入口与交接架构

### 3.1 Application 上下文入口

在已绑定 Application 的 Pilot 会话中增加“评估当前岗位”入口。入口载荷只包含当前 Application 上下文引用，不允许从 Pilot 自行读取 URL 或任意岗位列表。

投递详情页可增加“在 Pilot 中评估”入口：

1. 创建该 Application 上下文的 Pilot 草稿/会话入口；
2. 打开 Pilot；
3. 直接挂载岗位评估流程卡；
4. 不发送模型请求，直到用户完成输入并确认发送。

### 3.2 一次性交接令牌

`AppShell` 持有内存中的一次性 `MaterialKitHandoff`，由 Pilot 卡片写入、由 `ApplicationDetail` 通过原子消费函数读取。原始 handoff 不作为可写对象传给子组件，令牌不落库、不进入 URL、不发送给 AI：

```ts
type MaterialKitHandoff = Readonly<{
  applicationId: number;
  reviewId: number;
  resumeId: number;
  jdText: string;
}>;
```

`AppShell` 内部可以使用不对外暴露的 opaque token 防止实现层误用，但 token 不传给 `ApplicationDetail`，也不由 `ApplicationDetail` 校验。AppShell 只提供以下消费接口，不暴露 handoff 的可写 state：

```ts
consumeMaterialKitHandoff(applicationId: number): MaterialKitHandoff | null;
```

该函数在一次同步操作中由 AppShell 检查 `applicationId` 是否匹配，匹配时返回只读记录并立即清除，不匹配或重复消费返回 `null`。`ApplicationDetail` 只调用该函数并使用返回的冻结 `resumeId`/`jdText`，不自行校验 token；Pilot 卡片不能通过 props 或共享引用继续修改已交接对象。

交接流程：

```text
Pilot Opportunity Fit Card
  └─ 用户点击“准备材料”
      └─ AppShell 写入一次性 handoff
          └─ 打开 ApplicationDetail 的 Material Kit 抽屉
               └─ ApplicationDetail 调用 AppShell.consumeMaterialKitHandoff(applicationId)
                   └─ MaterialKitDrawer 只读预填 resumeId/jdText
```

消费后立即清除令牌；刷新前未提交的输入只存在当前 Pilot 草稿状态，刷新后不恢复。历史评估恢复不使用旧的临时输入，而是读取详情接口返回的冻结快照。

“准备材料”只打开并预填 Material Kit 抽屉，不调用生成材料包接口、不创建/更新 Material Kit、不改变投递状态。

## 4. Pilot 原生流程卡

新增前端原生 `PilotOpportunityFitCard`。卡片使用结构化 React/Ant Design 组件，不依赖 Markdown。

### 4.1 状态机

```text
collect_input
  → confirm_triage
  → triage_loading
  → triage_ready
  → confirm_deep_review
  → deep_review_loading
  → deep_review_ready
  → material_handoff
```

任意请求失败回到当前可重试状态并保留用户输入；取消确认回到上一状态且不发请求。历史评估直接进入 `triage_ready` 或 `deep_review_ready`，但所有结果标注为冻结快照只读内容。

### 4.1.1 Application 上下文与草稿归属

卡片实例由 `(applicationId, pilotDraftKey)` 唯一标识：

- `pilotDraftKey` 在 Application 上下文首次创建草稿时生成一次，由 AppShell 持有并传给唯一的活动卡片；重新挂载、侧栏切换或抽屉切换不得重新生成；
- AppShell 同一 Application 上下文只保留一个活动草稿和一个权威卡片状态，不能同时把同一草稿交给两个卡片实例；
- 卡片的临时输入、Triage 结果、`triageAttemptKey` 和交接状态都归属于该 `(applicationId, pilotDraftKey)`；切换 Application 时先丢弃/冻结旧卡片引用，再加载新上下文；
- 历史评估详情是只读状态，不能与未提交草稿共享输入对象或幂等键。

这样可以避免 Pilot 侧栏重挂载或 Application/抽屉切换后出现两个表单、两个幂等键或一次交互重复发起两条评估。

### 4.2 收集输入

卡片展示：

- 当前 Application 的公司名和职位名，作为动态数据原样显示；
- Resume 选择器，只列可见 Resume；
- JD 多行输入，只接受用户粘贴文本，去除首尾空白后不得为空；
- 用户断言多行输入，按换行分割、trim、过滤空行，最多 10 条，每条最多 500 字；
- 固定提示：“这些内容将发送给当前配置的 AI 服务”；
- “发送给 AI 进行岗位评估”按钮和取消路径。

输入限制在前端即时提示并禁用按钮，后端 422 仍作为最终防线。输入状态不写入 Application、Resume、Material Kit 或 Opportunity Fit 记录，直到用户确认并调用 Triage API。

首次点击确认发送时，卡片为当前 `(applicationId, pilotDraftKey)` 生成一次 `triageAttemptKey`，并将其作为请求的 `idempotency_key`。请求超时、网络错误、响应丢失或用户点击重试时，只要输入未变化且未显式取消，必须复用该 key；不能为重试生成新的 UUID。Triage 成功后清除该 key；用户编辑 Resume、JD 或任意断言，或显式取消本次发送确认时立即使旧 key 失效，下一次确认生成新 key。Deep Review 不创建新的 Triage key，继续使用 `review_id` 和既有后端幂等语义。

### 4.3 Triage

点击 Triage 后先显示确认弹窗，明确列出将发送的 Resume、JD 和用户断言；用户取消则不发请求。

成功后使用结构化分组展示：

- 带证据引用的摘要；
- hard constraints；
- fit signals；
- gaps；
- 待确认问题；
- 截止日期。

证据来源和路径遵循既有 Triage 契约。JD 只作为岗位要求和分析方向来源，不能展示为候选人事实。用户断言始终单独标记为用户提供、未外部核验。

Deep Review 按钮只有在当前 Triage 成功存在时启用。

### 4.4 Deep Review

点击 Deep Review 后再次显示确认弹窗；取消不调用接口。

成功后展示：

- strengths；
- gaps to address，所有明确 gap 必须保留非空证据引用；
- questions to clarify；
- recommended path；
- next actions。

所有 Deep Review 完成后都显示材料交接选择。若 `recommended_path=prepare_materials`，显示主按钮“准备材料”；若建议路径为 `clarify_first` 或 `do_not_pursue`，仍显示次级按钮“仍要准备材料”。点击次级按钮必须再弹出一次明确确认，说明该选择与 AI 建议路径不同，用户确认后才执行 handoff。模型建议只能提供建议，不能阻断用户决定。任一按钮只触发一次性交接，不自动生成材料、不打开投递操作。

## 5. 历史恢复与来源语义

- 已生成评估通过现有历史列表/详情接口恢复。
- 详情响应必须提供该评估冻结的 Resume 标识/内容摘要和完整冻结 JD 文本，或由受控只读交接响应提供；不得用当前 Application 备注或当前 Material Kit JD 替代。
- 如果 Application 或评估不可见/已软删除，显示固定中文 404 提示并清理当前卡片；不得显示 Axios 原文。
- 历史 Triage/Deep Review 的引用、摘要和 AI 正文来自保存的评估 JSON；Resume/JD 后续变化不重算、不静默覆盖。
- 点击“准备材料”时，handoff 中的 `resumeId`/`jdText` 必须与冻结评估一致；交接后仍由 Material Kit 处理当前的 Resume 可见性和后续人工操作。

## 6. Opportunity Fit 中文文案与安全错误

新增 Opportunity Fit 专用文案/错误映射模块，供 `PilotOpportunityFitCard` 和现有 `OpportunityFitReviewDrawer` 共用；不引入全局国际化。

固定中文范围包括：卡片标题、说明、输入标签、占位符、发送/取消/确认按钮、加载/空状态、Triage/Deep Review 分组标题、证据来源标签、历史状态、404/422/409/502 和未知错误提示、无障碍标签。

错误展示规则：

- 仅根据稳定错误码或 HTTP 状态映射固定中文；
- 禁止展示 `response.data.error`、Axios `message`、`Error.message` 或未知服务端 `issues` 原文；
- `material_proposal_unverifiable` 等已知安全错误码使用对应固定产品文案；
- Provider 502 无不可验证错误码时使用“AI 服务暂不可用，请稍后重试”；
- 未知错误使用统一中文兜底；
- 动态 JD、Resume、公司/职位名、AI 正文和证据摘录不经过文案映射。

同时移除/替换 `OpportunityFitReviewDrawer` 中现有可能直接展示服务端/Axios 英文错误的路径。

## 7. 不变量与失败处理

1. Pilot 只在 Application 上下文中显示岗位评估入口。
2. 用户确认发送前不调用 Triage/Deep Review API。
3. 只有 API 契约明确保证“不写入”的响应才允许清除当前 `triageAttemptKey`：输入校验 `422`、调用前置条件明确失败的 `404`，以及带稳定错误码 `opportunity_fit_provider_error` 或 `opportunity_fit_unverifiable` 的 `502`。这些响应不产生新的评估结果；Deep Review 失败不覆盖已有结果。
4. 普通 `500`、网关错误、没有稳定安全错误码的 `5xx`、响应体无效/无法序列化，以及超时、断网、响应丢失，统一属于“结果未知”。客户端不得声称服务端未写入评估，不得清除或更换原 `triageAttemptKey`，必须优先使用同一 key 重试以取得后端幂等返回。历史列表或详情只能作为只读查看入口，不能按时间、摘要或其他猜测匹配记录，也不能据此清除当前尝试 key。
5. 相同幂等键沿用后端幂等返回，不重复创建评估。
6. 评估过程中 Application 软删除时，后端既有 404 语义生效；前端清理卡片，不创建材料交接。
7. 材料交接是导航/预填动作，不是写入动作；不会创建 Material Kit、修改投递状态或接受简历 Proposal。
8. 不产生招聘平台网络请求，网络请求只允许到本地 API 与已配置 AI Provider。

## 8. 测试与验收

### 前端单元/组件测试

- Application 上下文存在时显示入口；无 Application 的全局 Pilot 不显示岗位评估入口；
- 卡片状态机从输入、确认、Triage、Deep Review 到材料交接的正向路径；
- 确认弹窗取消不发请求，加载期间按钮禁用；
- Resume/JD/断言输入限制和 trim 后请求体；
- Triage/Deep Review 的加载、成功、幂等结果和历史恢复；
- Triage 的超时、断网、响应丢失、普通 500/网关 5xx 和无效响应体均显示“结果未知”、保留同一 `triageAttemptKey`，重试不生成新 key；仅 422、调用前置条件 404，以及带 `opportunity_fit_provider_error`/`opportunity_fit_unverifiable` 的 502 清除 key 并按明确失败处理；
- 软删除/404、422、Provider 502、不可验证 502 和未知错误均只显示安全中文；
- 证据标签、引用路径/摘录原文保留；
- “准备材料”写入 handoff 并打开现有抽屉，未调用生成 Material Kit 或接受 Proposal；由 AppShell 原子校验 `applicationId` 并消费，ApplicationDetail 不自行校验 token；
- handoff 单次消费和 Application 不匹配时拒绝消费；
- 固定英文短语扫描仅检查已知固定短语，不禁止英文动态数据。

### 后端回归

沿用既有 Opportunity Fit API 专项测试，确认 API、快照、证据门控、幂等、软删除竞态、错误码和数据模型无变更；材料包、Proposal、Evidence Bundle 和 Application 状态回归保持通过。

### 构建与浏览器验收

执行前端全量测试、生产构建，并用内置浏览器走查：

1. 从绑定 Application 的 Pilot 进入“评估当前岗位”；
2. 输入合成/专用测试 Resume、JD 和 1–2 条断言，确认后生成 Triage；
3. 确认 Deep Review 后查看证据、gap 和问题；
4. 点击“准备材料”，确认 Material Kit 预填冻结 Resume/JD；
5. 验证未自动生成 Material Kit、未改变投递状态；
6. 刷新/打开历史评估，确认恢复冻结文本而不是当前备注；
7. 检查浏览器网络仅访问本地 `/api` 与已配置 AI Provider，无招聘平台请求；
8. 不输出 API Key、完整 Resume、完整 JD 或完整证据快照。

### Real-AI 验收

`real-ai` 验收使用临时隔离数据目录复制现有 AI 配置，创建合成 Application、Resume、JD 和断言。除 API 级 Triage/Deep Review smoke 外，必须启动同一构建的 Pilot 前端并用内置浏览器从该 Application 上下文完成真实闭环：

1. 从 Application 上下文进入 Pilot 的“评估当前岗位”；
2. 填写 Resume、JD 和断言，确认发送，验证 Triage 请求只发出一次幂等 key；
3. 完成 Triage，再确认并完成 Deep Review；
4. 点击“准备材料”或在非 `prepare_materials` 路径下确认“仍要准备材料”；
5. 验证 Material Kit 抽屉收到冻结 Resume/JD，但数据库中没有自动创建/更新 Material Kit，没有改变 Application 投递状态，没有接受 Proposal；
6. 检查浏览器网络请求只到本地 `/api` 与已配置 AI Provider，不访问招聘平台；
7. 清理临时数据目录并断言无残留 Application、评估、Material Kit 或 Proposal。

模型可以返回安全的空 Triage/Deep Review 结果；这记录为安全结果，不强行推进材料交接。所有失败只保留安全类别，不记录原始模型输出、API Key 或完整敏感快照。该浏览器闭环是 real-AI 验收的一部分，不得由只调用 API 的 smoke 替代。

## 9. 破坏性变化

无。既有 Opportunity Fit API、Material Kit API、数据模型、证据门控和人工确认流程保持不变。
