# 基于真实复盘的自适应练习设计

状态：已批准进入实施

## 1. 背景与目标

OfferPilot 已能保存面试复盘、生成证据化复盘建议，并将可复用事实整理为面试故事。当前缺口是把用户已经确认保存的复盘问题转成一次具体、可完成、可追溯的练习，而不是继续增加无来源的题库内容。

第一期只实现一条闭环：

`已保存复盘 → 证据化练习建议 → 用户确认开始 → 完成练习 → 只读历史`

系统不生成综合能力分、不预测录用概率、不替用户判断能力，也不会自动启动 AI。推荐和练习模板均由确定性规则生成，Provider 调用次数必须为 0。

## 2. 产品边界

### 2.1 本期包含

- 面试首页展示最多一条当前最值得处理的复盘练习建议。
- 练习模块新增“复盘训练”页，展示待处理建议、进行中练习和已完成记录。
- 用户在开始前查看“观察到的问题、为什么现在练、冻结来源”，并显式确认。
- 练习中填写回答、复盘感受和非数值自评，确认后形成不可变完成记录。
- 读取时派生来源状态 `current | changed | missing`，但不覆盖历史。

### 2.2 本期不包含

- 不调用 AI，不自动生成问题，不写 Question、Knowledge、Memory、Interview Story 或 Application 状态。
- 不记录 Story Usage，不增加 Story Usage 表、字段或入口。
- 不提供能力总分、岗位成功率、录用概率、排名或劝退结论。
- 不在 Pilot 中增加入口；Pilot 可在后续复用同一练习 API。
- 不做实时自适应追问或多轮模拟面试。

## 3. 事实源与推荐规则

唯一推荐来源是已保存的 `InterviewReviewProposal.practice_focuses`。每条 focus 必须有至少一个可验证的 `interview_note` evidence ref；系统不把 AI 文案当作候选人事实，而将其标记为“系统观察”。冻结来源仍显示用户原始复盘摘录。

按 evidence path 选择固定练习模板：

| 来源路径 | 练习类型 | 标题 | 固定练习提示 |
| --- | --- | --- | --- |
| `/difficulty_points` | `difficulty_breakdown` | 拆解卡住的关键一步 | 写出当时卡住的具体节点，并用三步说明下一次如何推进。 |
| `/self_reflection` | `answer_reframe` | 重构一次更清晰的回答 | 重新组织一次回答：先结论，再给关键事实，最后说明影响。 |
| `/questions` | `question_decode` | 练习问题解码 | 写出面试官可能在验证什么，再给出一版针对性的回答。 |
| `/mood` | `pressure_rehearsal` | 复盘压力情境 | 写出压力出现的触发点，并准备一句稳定节奏的过渡表达。 |

推荐排序固定为：来源 proposal 创建时间倒序、proposal id 倒序、focus 在原数组中的顺序。相同 focus 已存在 `in_progress` 或 `completed` 计划时不再重复推荐。首页只取第一条；训练页显示全部可用建议。

## 4. 数据模型与冻结语义

新增 `adaptive_practice_plans`：

- 身份：`id`、`application_id`、`application_event_id`、`interview_note_id`、`interview_review_proposal_id`、`focus_id`。
- 幂等：全局唯一 `start_idempotency_key`、`start_input_fingerprint`；完成使用唯一 `completion_idempotency_key`、`completion_fingerprint`。
- 冻结内容：`drill_kind`、`title`、`observation`、`reason`、`prompt`、`source_path`、`source_excerpt`、`source_hash`、`source_fingerprint`。
- 生命周期：`status=in_progress|completed`、`revision`、`created_at`、`updated_at`、`completed_at`。
- 完成内容：`response_text`、`reflection_text`、`self_assessment=needs_work|clearer|confident`。

开始练习时在同一短事务中重新读取 Application、Event、Note 与 Proposal，核对归属、focus、来源摘录和 fingerprint，然后冻结计划。完成练习使用 `expected_revision` CAS；相同完成 key 与相同输入稳定重放，不同输入返回稳定冲突。

读取时重新读取 Note 对应字段：路径仍存在且 hash 相同为 `current`，存在但变化为 `changed`，Note/Application/Event 不可见或字段缺失为 `missing`。历史冻结内容永不改写。

迁移号为 `0021_adaptive_interview_practice`，与现有 `0019` Story、`0020` Outcome 并存。

## 5. API 契约

- `GET /api/interview-practice/recommendations`
- `GET /api/interview-practice/plans`
- `POST /api/interview-practice/plans`
- `POST /api/interview-practice/plans/{plan_id}/complete`

开始请求只接受 `proposal_id`、`focus_id`、`expected_source_fingerprint`、`idempotency_key`。服务端不信任客户端传入的观察、提示或来源文本。

完成请求接受 `expected_revision`、`response_text`、`reflection_text`、`self_assessment`、`idempotency_key`。回答不能为空，所有文本有明确长度上限。

稳定错误码：

- `adaptive_practice_not_found`
- `adaptive_practice_source_conflict`
- `adaptive_practice_idempotency_conflict`
- `adaptive_practice_revision_conflict`
- `adaptive_practice_invalid_payload`

所有接口都是本地数据库读写，不调用 Provider。

## 6. 前端体验

### 6.1 面试首页

在页头与事件列表之间展示一张“下一项复盘训练”卡：标题、系统观察、为什么现在、冻结来源摘要和“查看并开始”按钮。加载失败不伪装成“没有建议”，而显示可重试的轻量错误状态。

### 6.2 练习工作台

在现有练习页加入三个页签：`复盘训练 | 题库 | 刷题打卡`，默认行为保持题库；从面试首页进入时聚焦复盘训练。

复盘训练分为：

- 推荐区：确定性建议卡与来源标签。
- 练习区：开始确认后展示固定提示、回答输入、可选复盘和三档语义自评。
- 历史区：已完成记录只读展示回答、复盘、自评、冻结来源及来源变化。

按钮最小点击区域 40px；宽屏采用主练习区 + 证据侧栏，窄屏自然堆叠。沿用当前 OfferPilot 的亮色视觉语言，不改变全局布局。

## 7. 恢复、并发与安全

- 开始操作未知时前端保留原 key并冻结确认控件，只允许同 key 重试。
- 确定性 409 清理旧 key，重新加载建议；不自动新建计划。
- 完成操作未知时保留 completion key、文本与选择，只允许同 key重试。
- 同一 proposal/focus 最多一个进行中或已完成计划，由数据库唯一约束保证。
- 所有列表和详情严格按可见 Application/Event/Note 过滤，不泄露已删除或跨投递数据。
- API、UI、浏览器验收均断言 AI/Chat、Question、Knowledge、Story、Application 状态写入为 0。

## 8. 验收

- 纯函数覆盖推荐映射、排序、非法 evidence、空数组和来源状态。
- Repository 覆盖开始幂等、CAS、归属、来源漂移、删除、完成重放和跨连接并发。
- API 覆盖稳定状态码、长度与枚举校验、Provider 0 调用。
- 前端真实挂载覆盖首页推荐、开始确认、完成、历史、重挂载未知结果与零外联。
- 亮色中文宽屏浏览器验收使用候选人“筱哲”，产出三张截图：面试首页建议、练习进行中、完成历史。
