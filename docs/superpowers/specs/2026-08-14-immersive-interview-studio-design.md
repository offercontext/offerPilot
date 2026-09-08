# 沉浸式模拟面试与证据引导设计

- 状态：已完成产品方向确认，待开发复审与实施
- 日期：2026-08-14
- 设计基线：`af8f0d1e035a1a04e9a1421d976e78c7f91f8997`
- 分支：`feat/20260814-immersive-interview-studio`
- 本文范围：产品与技术设计；本提交不包含实施计划或产品代码

## 1. 背景与问题

OfferPilot 已具备事件绑定的文本模拟面试、浏览器本地录音、按需离线转写、实时语音节奏复盘、表达成长档案和 Haru Pilot 角色，但当前体验仍是多个能力的拼接，而不是一个完整的“进入面试—连续作答—结束复盘”流程。

当前基线中可观察到以下问题：

1. 模拟面试挂载在固定宽度约 `620px` 的 Drawer 中，无法形成沉浸式面试场景，语音回答、题目、状态和复盘卡片彼此挤压。
2. 回答提交后，用户还要点击“生成下一题”；虽然后端已经能基于已完成 Turn 生成后续问题，前端并未把它组织成自然的逐轮对话。
3. 顶层“面试”页面主要展示已绑定 Application 的面试事件。没有投递记录或尚未安排面试的用户，不知道如何开始练习。
4. 面试准备界面已经把当前 JD 设为只读，但仍使用“粘贴 JD”等历史文案，用户会误以为输入框失效。
5. JD、简历、面试事件和准备建议分别存在，但缺少明确的准备清单，没有解释“为什么需要、缺什么、去哪里补、哪些内容会发送给 AI”。
6. Haru 已支持状态、缩放、隐藏和 Pilot 完成通知，但位置固定；在不同页面和面试工作台中可能遮挡内容，也无法由用户摆放。

本设计把这些问题收敛为同一个目标：在不削弱证据门控、人工确认、幂等恢复和隐私边界的前提下，建立一个可发现、可进入、可连续练习的沉浸式模拟面试工作台。

## 2. 目标与非目标

### 2.1 目标

- 顶层“面试”首先成为面试准备中心，清楚展示两种练习方式及各自的前置条件。
- 支持“真实投递面试”和“快速练习”两种模式。
- 真实投递模式继续绑定 Application、已确认 JD 版本、已保存简历和已安排面试事件。
- 快速练习不要求创建虚假的投递或日程，只冻结用户明确确认的岗位名称、JD 与简历。
- 面试使用独立全屏工作台；回答确认后自动请求下一题，形成连续对话。
- 文本与语音回答共享同一 Turn、幂等、证据和反馈语义。
- Haru 在普通页面和面试工作台均可拖动，并分别记住位置。
- 每个前置条件、AI 输入、冻结快照、来源变化和结果未知状态均有可见说明。

### 2.2 非目标

- 不增加摄像头、视频面试、表情或情绪识别。
- 不生成录用概率、能力总分、人格判断、紧张程度或岗位排名。
- 不做云端音频上传、云端语音评分或 Live2D 口型同步。
- 不在停顿后自动提交回答；语音转写必须由用户核对并确认。
- 不自动创建 Application、ApplicationEvent、Knowledge、Memory、Story、Question 或提醒。
- 不把快速练习混入投递漏斗、日历、投递统计或 Application 历史。
- 不删除现有事件绑定 Mock Interview API，也不改变已确认历史的只读语义。

## 3. 产品信息架构

### 3.1 顶层“面试”改为面试准备中心

进入“面试”后，页面按以下顺序展示：

1. 模式选择卡：
   - **围绕真实投递练习**：适合已经有投递和已安排面试的用户。
   - **快速练习**：适合还没有投递、只想针对一个岗位开始练习的用户。
2. 当前模式的“开始前检查”：每项显示 `已就绪 / 需要补充 / 来源已变化 / 暂时不可用`。
3. 主行动按钮：所有必填项就绪后才启用“进入模拟面试”。
4. 已安排面试与历史练习。
5. 表达成长、面试故事库及其他现有面试资产入口。

首次进入时不播放强制导览，也不覆盖整页。说明与补齐动作直接放在对应检查项中，让用户在任务上下文内完成准备。

### 3.2 真实投递模式检查项

| 检查项 | 就绪条件 | 缺失时动作 | 对 AI 的影响 |
| --- | --- | --- | --- |
| 投递 | 当前可见且未软删除的 Application | “选择投递”或“新增投递” | 提供岗位归属，不把整个投递历史发送给 AI |
| 岗位资料 | Application 存在当前已确认 JD Version | “更新岗位资料” | 发送冻结 JD 文本和允许的路径 |
| 简历 | 用户显式选择一份当前可见、已保存的 Resume | “选择简历” | 发送冻结的已保存简历内容；不读取编辑器草稿 |
| 面试安排 | 属于该 Application、`event_type=interview`、已排期且可见 | “安排面试” | 发送必要的面试类型/时间上下文，不发送无关日程 |
| 准备建议 | 可选，且来源仍为 current 的已确认建议 | “查看准备建议” | 只发送用户显式勾选的建议及其冻结来源 |

主按钮在前四项满足时启用。准备建议永远是可选项，缺失时不能阻止练习。

### 3.3 快速练习模式检查项

| 检查项 | 就绪条件 | 缺失时动作 | 写入边界 |
| --- | --- | --- | --- |
| 岗位名称 | 1–200 个 Unicode code point 的非空文本 | 就地填写 | 仅进入本次冻结练习档案 |
| 岗位描述 | 用户粘贴文本并明确勾选“已核对，本次按此岗位资料练习” | 就地填写并确认 | 创建 Quick Practice Case 时冻结；不抓取 URL |
| 简历 | 用户显式选择一份当前可见、已保存的 Resume | 就地选择 | 冻结当前已保存版本；不修改 Resume |

填写过程只保存在 AppShell 持有的本地草稿中。只有用户点击“确认并进入模拟面试”后，才创建独立的快速练习档案；创建档案不调用 Provider。

### 3.4 文案修正

所有只读 JD 控件统一改为：

- 标题：`当前 JD（只读）`
- 说明：`模拟面试使用当前已确认的岗位资料版本。`
- 操作：`更新岗位资料`

只有快速练习的未确认草稿使用“粘贴 JD”。任何只读控件不得继续显示成可编辑输入的文案。

## 4. 沉浸式 Interview Studio

### 4.1 页面形态

采用独立全屏工作台，而不是扩大 Drawer 或在当前页面上叠加 Modal。进入后隐藏 OfferPilot 左侧导航、普通页面头部和 Pilot 侧栏，保留浏览器自身导航能力。

工作台由四层组成：

1. **顶部控制栏**：退出、模式名称、岗位/公司、`第 n / 5 轮`、来源状态、结束面试。
2. **中央对话时间线**：按顺序展示面试官问题、候选人已确认回答、生成状态和错误恢复卡。
3. **底部固定回答台**：文本/语音模式、录音、暂停、转写核对、提交；内容增长时内部滚动，不把整个页面向下撑出视口。
4. **右侧可折叠依据栏**：本轮冻结的 JD、简历、可选准备建议和来源状态；默认收起，不抢占对话空间。

宽屏下中央内容最大宽度保持可读，右侧依据栏以覆盖式或可折叠列出现。窄屏下依据栏改为底部 Sheet；回答台始终留在安全视口内。

### 4.2 进入与退出

- 从准备中心点击“进入模拟面试”后，先在本地确认所有必填来源，再创建/恢复 Attempt。
- Attempt 创建成功后才进入第一题；创建结果未知时留在准备中心并冻结输入，使用原 key 检查或重试。
- 用户可随时点击“结束面试”。有未确认转写、未提交文本或保存结果未知时，退出前显示明确确认。
- 退出不会自动生成反馈，也不会把未确认内容写入任何领域。
- 完成的 Turn、已保存表达快照和已确认复盘按现有规则保留；仅未持久化的本地草稿会被丢弃。

## 5. 连续对话与追问

### 5.1 轮次生命周期

每一轮严格经过：

```text
question_ready
→ answering
→ transcript_review（仅语音）
→ answer_submitting
→ answer_confirmed
→ next_question_generating
→ question_ready
```

文本回答由用户点击“提交回答”；语音回答先停止录音、完成本地转写、由用户核对并确认文字，再调用同一个 Answer API。停顿、静音、页面隐藏或达到建议时长都不能自动提交。

回答 API 成功或通过原 key 对账为成功后，前端自动调用“生成下一题”API，不再展示普通的“生成下一题”按钮。它仍是两个独立写入步骤和两个独立幂等键，不能合并成一个不可恢复的长请求。

### 5.2 下一题选择

下一题输出增加以下受限元数据：

```text
question_kind: follow_up | new_topic
parent_turn_no: integer | null
topic_root_turn_no: integer
basis_refs: [{ source, path, excerpt }]
```

规则：

- `follow_up` 必须引用已提交回答中的具体逐字片段，`parent_turn_no` 指向被追问的 Turn。
- `new_topic` 必须引用冻结 JD、Resume、已选择准备建议或服务端版本化固定问题。
- 同一 topic 最多连续追问 2 次，之后必须切换新 topic 或结束。
- 模型不能引用未确认转写、编辑器草稿、未选择建议、Knowledge 全库或其他会话。
- 证据不足时使用服务端版本化安全问题，不允许模型凭空补充候选人事实。

第一期固定默认 5 轮，不提供评分式难度滑杆。用户可在任何一轮回答确认后结束；达到第 5 轮时停止自动生成下一题，进入“本轮已完成”状态，由用户决定是否生成复盘建议。

### 5.3 结果未知与失败

- Answer 裸网络失败或裸 5xx：保留原 Answer key、冻结文字，先读取 Turn，再决定同 key 重放。
- 下一题 Provider 未知：保留原 Question key，时间线显示“下一题结果待确认”；不得创建新 Turn 或再次提交回答。
- 来源变化：Attempt 的冻结来源仍用于本轮历史；尚未开始的练习回到准备中心重新确认。已经开始的 Attempt 不因当前 JD/Resume 更新而中断。
- 确定性 4xx：按稳定 `error_code` 分流；不得只按 HTTP 状态显示通用错误。
- 用户主动结束时，如果下一题仍在生成，先停止继续推进并使用 fencing/CAS 防止迟到问题进入已结束 Attempt。

## 6. 双上下文领域模型

### 6.1 `InterviewPracticeCase`

快速练习使用独立聚合，不创建虚假的 Application 或 ApplicationEvent。

```text
InterviewPracticeCase
  id
  idempotency_key                 UNIQUE
  request_fingerprint_sha256
  position_name_snapshot
  jd_text_snapshot
  jd_fingerprint_sha256
  resume_id
  resume_content_snapshot_json
  resume_fingerprint_sha256
  status                          active | archived
  created_at
  archived_at                     nullable
```

约束：

- Case 创建后内容不可变；更换岗位、JD 或简历必须新建 Case。
- `resume_id` 是审计标识；历史显示使用冻结快照，不使用后来变化的当前 Resume。
- JD 只接受用户粘贴的原文，不抓取 URL、不补全、不做 AI 清洗。
- 同 key 同指纹返回原 Case；同 key 不同指纹返回 `409 interview_practice_case_idempotency_conflict`。
- Case 归档只影响新 Attempt 入口，不删除已有练习历史。

### 6.2 `MockInterviewAttempt` 上下文

为 Attempt 增加明确的上下文联合类型：

```text
context_kind: application_event | quick_practice
application_id: nullable
event_id: nullable
practice_case_id: nullable
```

数据库与 Repository 同时保证：

- `application_event`：`application_id`、`event_id` 非空，`practice_case_id` 为空。
- `quick_practice`：`practice_case_id` 非空，`application_id`、`event_id` 为空。
- 任何“全部为空”或“两种上下文混用”的行均不可创建。
- 现有数据迁移为 `context_kind=application_event`，原 ID、状态、fingerprint 和历史不变。
- 幂等唯一性使用稳定上下文命名空间，不能让不同上下文复用同一 key。

冻结 `input_snapshot_json` 统一为规范化结构，但分别携带：

- 真实模式：Application/Event/JD Version/Resume/可选 Preparation 的冻结事实。
- 快速模式：Practice Case 的岗位、JD、Resume 冻结事实。

### 6.3 Turn、反馈与表达成长

- `MockInterviewTurn`、Feedback Proposal 和 Review Draft 继续归属于 Attempt，不复制两套表。
- `question_source_snapshot_json` 增加第 5.2 节的追问元数据，旧历史读取时缺失字段按 `new_topic` 兼容展示，不回写旧行。
- 快速练习也支持本地录音、离线转写和表达成长快照，因此 `VoiceCoachingSnapshot` 同步采用与 Attempt 一致的联合上下文：真实模式保留 Application/Event；快速模式关联 Practice Case。
- 表达成长全局列表可展示两种来源；快速练习显示“快速练习 · 岗位名称”，不得伪造公司或投递链接。
- 第一期不允许从快速练习自动创建 Story、Knowledge 或正式 Interview Review；以后若增加，必须由用户从已确认 Turn 显式发起。

## 7. API 边界

### 7.1 快速练习档案

```text
POST /api/interview-practice-cases
GET  /api/interview-practice-cases?limit=50&before_id=<id>
GET  /api/interview-practice-cases/{case_id}
POST /api/interview-practice-cases/{case_id}/archive
```

创建只冻结输入，不调用 AI。列表与详情只返回安全快照及来源状态，不返回内部数据库路径或 Provider 配置。

### 7.2 快速练习 Mock Interview

```text
POST   /api/interview-practice-cases/{case_id}/mock-interview/attempts
GET    /api/interview-practice-cases/{case_id}/mock-interview/attempts
GET    /api/interview-practice-cases/{case_id}/mock-interview/attempts/{attempt_id}
DELETE /api/interview-practice-cases/{case_id}/mock-interview/attempts/{attempt_id}
POST   /api/interview-practice-cases/{case_id}/mock-interview/attempts/{attempt_id}/turns
POST   /api/interview-practice-cases/{case_id}/mock-interview/attempts/{attempt_id}/turns/{turn_no}/question
POST   /api/interview-practice-cases/{case_id}/mock-interview/attempts/{attempt_id}/finish
POST   /api/interview-practice-cases/{case_id}/mock-interview/attempts/{attempt_id}/review-drafts
```

真实投递继续使用现有 `/api/applications/{application_id}/events/{event_id}/mock-interview/...`。两套路由必须委托同一个上下文无关的领域服务，保持完全一致的 Turn、Provider、lease、CAS、证据和失败语义；不得复制一套弱化校验的 Repository。

Voice Coaching 为 quick practice 增加对称的 Case 路由，读取与创建仍校验 Attempt/Turn/Case 的完整归属。

### 7.3 兼容与稳定错误码

- 现有 Application/Event API 与响应字段继续可用。
- 新的联合响应显式返回 `context_kind` 与对应的安全上下文摘要。
- 新增稳定错误码至少包括：
  - `interview_practice_case_not_found`
  - `interview_practice_case_invalid_payload`
  - `interview_practice_case_idempotency_conflict`
  - `mock_interview_context_mismatch`
  - `mock_interview_question_result_unknown`
- API、前端、Smoke 与浏览器 Harness 均按 `error_code` 分流，不以裸 `409/502` 猜测状态。

## 8. Haru 全局拖动与面试状态

### 8.1 位置偏好

新增版本化本地偏好：

```json
{
  "version": 1,
  "normal": { "x_ratio": 0.92, "y_ratio": 0.82 },
  "interview_studio": { "x_ratio": 0.88, "y_ratio": 0.68 }
}
```

- 普通页面共享 `normal` 位置；单独 Pilot 页也属于普通页面。
- Interview Studio 使用独立位置，避免普通页面的摆放遮挡回答台。
- 坐标相对于可用视口归一化，不保存裸像素；窗口改变后重新换算并夹紧。
- 存储不存在、非法、NaN、Infinity 或版本未知时回到右下角安全默认位。
- 右键菜单保留隐藏与缩放，并增加“恢复默认位置”。
- 隐藏 Haru 后，普通页面继续使用默认 Pilot 侧栏；Interview Studio 顶栏提供“显示 Haru”，不把普通侧栏塞回全屏工作台。

### 8.2 拖动交互

- 支持鼠标、触摸和触控笔，使用 Pointer Events 与 pointer capture。
- 位移超过 6 CSS px 才进入拖动；未超过阈值仍视为点击并打开/聚焦 Pilot。
- 拖动时禁止文本选择，不触发右键菜单或缩放。
- 松开、`pointercancel`、窗口失焦和组件卸载均正确清理捕获状态。
- Haru 必须夹紧在安全区域内，不能完全移出视口，也不能覆盖 Studio 的退出按钮、结束按钮和底部提交主按钮。
- 键盘用户可从右键菜单选择“左下 / 右下 / 右中 / 恢复默认”，不要求用拖动完成摆放。

### 8.3 面试活动状态

Haru 复用现有 runtime，只增加受限状态映射：

```text
idle
question_ready
speaking
waiting_for_speech
listening
speech_paused
transcribing
reviewing_voice
thinking
completed
error
```

- 下一题生成完成但页面不在前台时，Haru 使用 `question_ready` 气泡提示“下一题准备好了”。
- 回答/反馈异步完成时继续使用现有成功或失败通知；点击后回到准确 Attempt/Turn。
- `error` 只表达“需要查看”，不做焦虑、情绪或能力推断。
- `prefers-reduced-motion: reduce` 下保持静态首帧和文字状态，不持续播放动作。

## 9. 状态归属与恢复

### 9.1 前端状态归属

- 面试准备草稿、快速练习未确认输入、当前 Attempt key、Turn key、Question key、Feedback key 和结果未知状态由 AppShell 或独立 Studio Controller 持有，组件重挂载不丢失。
- Interview Studio 只接收归一化的 `InterviewContext`，不在组件内部猜测第一个 Application/Event/JD/Resume。
- 路由切换、刷新和浏览器返回时，先用冻结 key/ID 回读，再决定恢复、显示只读历史或回到准备中心。
- 已提交回答不可编辑；要修改回答必须新建 Attempt。

### 9.2 Provider 与费用边界

- 自动生成下一题只是自动触发已有一次 Question Provider 调用，不增加隐藏重试。
- 同一 Turn 只允许一个活动 question owner；lease 有效时同 key 重放返回 pending，不再次调用 Provider。
- Provider 未知结果只允许原 key、原冻结输入恢复；不得因 UI 自动流程生成新 key。
- 用户主动结束后不再生成下一题，因此不会产生额外费用。

## 10. 隐私与跨领域写入边界

每次开始前明确展示：

- **发送给 AI**：冻结 JD、冻结简历中允许的结构化内容、已选择的准备建议、已确认的本 Attempt 问答。
- **仅在浏览器处理**：原始音频、PCM、VAD 帧、未确认临时转写、Haru 位置与缩放。
- **仅本地持久化**：用户确认的回答文字、Attempt/Turn、可选表达成长快照、用户确认的复盘草稿。

快速练习不得修改 Application、ApplicationEvent、Application Outcome、投递状态、日历、Knowledge、Memory、Story 或 Offer。打开准备中心、切换模式、查看检查项和拖动 Haru 均为零 API 写入；Haru 位置仅写 localStorage。

日志和诊断只记录稳定错误码、上下文类型、ID 哈希、fingerprint、计数和耗时，不记录 JD/Resume/回答原文、音频、密钥或 Provider 原始输出。

## 11. 无障碍、响应式与视觉要求

- Studio 使用明确的主区域、时间线和回答区语义；进入后焦点移到当前问题标题，退出后恢复到原入口。
- 所有按钮与输入命中区域不小于 40px。
- 生成状态使用 `aria-live=polite`；错误与结果未知使用可聚焦状态卡，不只依赖颜色。
- 语音录制计时使用等宽数字；文本、语音和键盘操作具有等价路径。
- Haru 拖动不是唯一操作方式，预设位置可由键盘完成。
- 窄屏下保持顶部退出、当前题和回答提交可见；对话时间线独立滚动，不能把回答台顶到屏幕外。
- 亮色、暗色、高对比度、200% 缩放和 reduced-motion 下保持相同业务语义。

视觉方向采用已确认的 A 方案：克制的全屏面试房间，中央对话为主，Haru 是陪练角色而不是覆盖内容的悬浮广告；不引入第二套导航或游戏化评分面板。

## 12. 测试与验收要求

### 12.1 后端

- 迁移：旧 Attempt 全部迁为 `application_event`；联合上下文 CHECK、唯一键、回滚与现有迁移共存。
- Practice Case：边界长度、Unicode、空白、fingerprint、幂等、归档、来源快照不可变。
- 双上下文：归属、跨 Case/Application/Event 访问、软删/物理删、历史读取和错误码。
- 自动下一题：回答成功后只有一次 Question 调用、两组 key 独立、live lease 重放不重复 Provider、迟到结果 fencing。
- 追问契约：`follow_up` 必须引用 Turn，`new_topic` 必须引用冻结来源或固定问题；连续追问上限与第 5 轮停止。
- Quick Voice Coaching：无音频持久化、来源链归属、列表与删除语义。
- 跨领域负向断言：Application/Event/Knowledge/Memory/Story/Offer/Question 等非目标表写入均为 0。

### 12.2 前端

- 准备中心两种模式的 loading/error/partial/ready 状态，未知不得显示为已就绪。
- 真实模式缺 Application、JD Version、Resume、Interview Event 的逐项补齐与恢复。
- 快速模式输入、确认、Case 创建结果未知、刷新恢复和不创建虚假投递。
- Studio 全屏进入/退出、焦点、浏览器返回、窄屏 sticky composer、长对话滚动。
- 文本与语音确认后自动下一题；不得在静音、转写未确认或 Answer 结果未知时前进。
- Haru 普通/Studio 双位置、拖动阈值、点击不误触、视口夹紧、重置、隐藏、缩放、触摸与键盘预设。
- 组件卸载、请求晚到、上下文切换和 StrictMode 下不产生重复请求或 runtime。

### 12.3 浏览器验收

使用中文候选人“筱哲”，亮色为主并补暗色、窄屏与键盘检查。至少保存以下宽屏截图：

1. 面试准备中心—真实投递模式检查项。
2. 面试准备中心—快速练习 JD 确认。
3. 全屏 Studio—第一题与 Haru。
4. 语音转写核对后提交。
5. 基于上一回答生成的追问及证据说明。
6. 第 5 轮完成与生成复盘入口。
7. Haru 在普通页面与 Studio 的两个不同自定义位置。

真实浏览器闭环必须分别完成：

```text
真实投递：选择 Application/Event/JD Version/Resume
→ 进入 Studio
→ 完成至少 2 轮（含 1 次 follow_up）
→ 主动结束并查看反馈

快速练习：输入岗位名称并粘贴确认 JD、选择 Resume
→ 创建 Practice Case
→ 进入同一 Studio
→ 完成至少 2 轮
→ 历史中显示为“快速练习”且不出现在投递/日历
```

网络审计须证明浏览器只访问本地静态资源与 `/api`；Provider 出站由后端审计。写入审计须逐表核验允许写入集合。临时服务、浏览器、音频、模型缓存测试副本、数据库和端口必须在 `finally` 清理。

## 13. 实施边界与合并策略

本分支刻意基于已完成语音成长档案的 `af8f0d1e035a1a04e9a1421d976e78c7f91f8997`，因为本设计直接依赖最新的 Voice Coaching Attempt/Turn 生命周期。开发不得把它误当成当前远端 main 的独立小功能，也不得在未核对迁移号和中心文件差异时盲目 rebase。

推荐新增独立模块：

```text
src/offerpilot/repositories/interview_practice_cases.py
src/offerpilot/services/interview_contexts.py
web/src/features/interviewStudio/**
web/src/features/interviewReadiness/**
web/src/types/interviewPracticeCase.ts
web/src/services/interviewPracticeCases.ts
```

预计显式冲突面：

```text
src/offerpilot/db.py
src/offerpilot/models.py
src/offerpilot/schemas.py
src/offerpilot/api.py
src/offerpilot/repositories/mock_interviews.py
src/offerpilot/repositories/voice_coaching.py
web/src/layout/AppShell.tsx
web/src/components/InterviewV01View.tsx
web/src/components/MockInterviewDrawer.tsx
web/src/features/pilotMascot/**
```

实现时应把 `MockInterviewDrawer` 的领域逻辑逐步抽到共享 Controller/Service，再由全屏 Studio 消费；不得复制一份新的请求状态机后留下两套不一致实现。旧 Drawer 可在同一发布中改为打开 Studio 的兼容入口，确认没有调用方后再单独删除组件，不删除公开 API。

## 14. 破坏性变化与迁移

公开行为没有破坏性变化：现有 Application/Event Mock Interview、历史、反馈、Voice Coaching、Pilot、Story 和 Knowledge 契约继续可用。

内部数据库需要迁移 `MockInterviewAttempt` 与 `VoiceCoachingSnapshot` 的上下文列和可空性；迁移必须为所有旧行填入 `application_event` 并保持原数据完整。若 SQLite 需要重建表，必须显式复制全部列、索引、唯一约束和外键，增加升级/回滚/旧数据读取测试，不能依赖本地 reset 掩盖问题。

## 15. 风险与控制

- **中心文件冲突**：以独立 repository、context adapter 和 feature 目录减小冲突；合并前按真实 fork point 计算文件交集。
- **自动流程导致重复费用**：Answer 与 Question 分离，原 key 恢复，lease/CAS/fencing 保持不变；不做隐藏重试。
- **Quick Case 被误当投递**：独立表、独立路由、联合上下文 CHECK、跨领域写入测试共同阻断。
- **Haru 遮挡主操作**：安全区域、视口夹紧、Studio 独立位置、键盘预设和重置入口。
- **复杂前置仍让用户迷失**：准备中心只显示当前模式所需条件，每个缺失项就地给出唯一下一步，不使用只读假输入框。
- **长对话撑出视口**：Studio 使用固定视口、内部滚动时间线和 sticky composer，并做真实窄屏验收。

## 16. 已否决方案

1. **把现有 Drawer 直接拉宽**：仍受 AppShell 与侧栏约束，不能解决长对话和回答台出屏。
2. **全屏 Modal 覆盖当前页面**：焦点、浏览器返回、刷新恢复和移动端复杂度更高，且背景页面仍在运行。
3. **快速练习自动创建假投递/假日程**：污染投递事实、日历和漏斗，违反领域边界。
4. **只做一次产品导览**：不能解决来源变化、后续缺项和新用户的动态准备问题。
5. **停顿后自动提交或自动结束**：可能把未完成回答和错误转写作为事实写入。
6. **把 Answer 与下一题合成单请求**：无法分别恢复两个写入结果，容易重复 Provider 调用。
7. **普通页面与 Studio 共用一个 Haru 像素坐标**：不同布局和视口下容易遮挡或离屏。

## 17. 开发完成条件

只有同时满足以下条件才可声称完成：

- 两种模式均能从准备中心独立进入同一全屏 Studio。
- 回答确认后自动且幂等地获得下一题，至少证明一次有 Turn 证据的 follow-up。
- Quick Practice 不创建或修改任何投递/日程/知识/故事数据。
- Haru 可在所有普通页面与 Studio 拖动，双位置持久化并满足无障碍与安全区域规则。
- 旧 Application/Event 历史与 Voice Coaching 记录迁移后可读。
- 后端完整分组门禁、前端完整分组门禁、静态检查、生产构建、local smoke/verify、real-AI 和中文真实浏览器闭环均通过。
- 所有截图已回读检查，不存在裁切、溢出、窄屏布局或来源文案错误。
