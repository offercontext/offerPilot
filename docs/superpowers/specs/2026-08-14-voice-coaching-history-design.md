# 语音面试成长档案设计

状态：已批准进入实施

日期：2026-08-14

## 1. 背景与目标

OfferPilot 的语音模拟面试已经完成三层能力：浏览器录音与朗读、按需离线 Whisper 转写、实时 VAD 与单次表达节奏复盘。当前复盘只存在于已挂载的练习会话；关闭 Drawer 后，用户无法回看历史变化，也无法从已确认结果进入下一次针对性练习。

第四期新增“语音面试成长档案”：用户在确认回答并完成原有回答提交后，可再次确认保存一条不可变的表达快照；系统只使用这些已确认记录计算可解释趋势，并允许用户返回原投递、原面试事件，带着一个明确训练重点重新开始模拟面试。

本期不保存音频，不上传 PCM，不调用 AI 生成趋势，不生成能力总分、录用概率、人格、情绪、口音或紧张程度判断。

## 2. 方案选择

### 2.1 采用：独立快照与确定性重练闭环

- 快照只在回答已提交后由用户点击“保存到成长档案”创建。
- 服务端从已回答 Turn 读取问题与确认文本，客户端不能替换事实文本。
- 浏览器只提交本地测量值、可选自我感受和用户主动选择的训练重点。
- 趋势从快照确定性聚合，返回参与计算的快照 ID 和原始指标。
- 用户点击“重练这道题”后回到原 Application / Event，打开新的 Mock Interview Attempt，并在界面展示本轮训练重点。

### 2.2 不采用：直接扩展 Knowledge、Story 或 Adaptive Practice

语音节奏是用户确认的浏览器测量，不是候选人事实或外部反馈。写入 Knowledge / Memory 会混淆事实职责；关联 Story Version 会在 Story 第一期之外间接引入 Story Usage；复用现有 Adaptive Practice 会把基于面试复盘原文的训练和基于本地语音指标的训练混成同一来源契约。

### 2.3 不采用：云端语音评分或全双工面试官

云端方案会引入持续音频上传、Provider 费用、网络稳定性和新的隐私授权。本期仍保持本地音频处理与文本确认边界。

## 3. 领域模型

新增迁移 `0022_voice_coaching_snapshots` 和一张表 `voice_coaching_snapshots`。

```text
VoiceCoachingSnapshot
  id
  attempt_id
  turn_id                    UNIQUE
  application_id
  event_id
  idempotency_key            UNIQUE
  request_fingerprint_sha256
  question_text_snapshot
  confirmed_answer_text_snapshot
  answer_sha256
  measurement_source         local_browser_measurement
  total_duration_ms
  voiced_duration_ms
  pause_count
  longest_pause_ms
  speech_rate_cpm            nullable
  filler_occurrences_json
  reflection_text
  focus_kind                 nullable
  origin_snapshot_id         nullable
  created_at
```

约束：

- 每个 `MockInterviewTurn` 最多一条成长快照。
- `attempt_id`、`turn_id`、`application_id`、`event_id` 必须属于同一 Mock Interview 链路。
- Turn 必须为 `answered`，且 `answer_text` 非空。
- 服务端从 Turn 冻结 `question_text`、`answer_text` 与 `answer_sha256`；请求体不接受这些字段。
- `measurement_source` 固定为 `local_browser_measurement`，只证明用户确认了浏览器测量，不表示服务端验证过音频。
- 快照创建后不可更新；用户可物理删除单条快照。
- 删除 Attempt 或 Turn 时级联删除快照；删除来源快照时，后续快照的 `origin_snapshot_id` 置空。
- 本期不增加 Story Usage，不写 Knowledge、Memory、Application Outcome 或 Adaptive Practice 表。

## 4. 写入契约与校验

创建请求仅包含：

```json
{
  "idempotency_key": "voice-coaching-save-018f6f6d7a8b4c2d",
  "total_duration_ms": 72000,
  "voiced_duration_ms": 25000,
  "pause_count": 1,
  "longest_pause_ms": 3000,
  "speech_rate_cpm": 118,
  "filler_occurrences": [
    { "text": "然后", "count": 2, "transcript_offsets": [12, 38] }
  ],
  "reflection_text": "结果部分还可以更简洁",
  "focus_kind": "long_pause_control",
  "origin_snapshot_id": 17
}
```

服务端执行以下机械校验：

- `idempotency_key` 使用现有安全格式和长度规则。
- `1 <= total_duration_ms <= 299000`。
- `0 <= voiced_duration_ms <= total_duration_ms`。
- `0 <= pause_count <= 300`，`0 <= longest_pause_ms <= total_duration_ms`。
- `speech_rate_cpm` 为空或位于 `1..1000`。
- 口头禅最多 20 种；每项文本 1..20 Unicode code point，count 1..100，offset 最多 100 个且按升序、不重复、位于确认文本 code point 范围内，并能逐字匹配该文本。
- `reflection_text` 最多 1000 Unicode code point。
- `focus_kind` 仅允许 `long_pause_control`、`filler_reduction`、`pace_consistency`；为空表示普通记录。
- `origin_snapshot_id` 如存在，必须可见，且与当前快照属于同一用户本地数据域；它只记录用户从哪条建议进入重练，不证明能力变化。

请求指纹覆盖 Turn 身份、服务端确认文本摘要、所有本地指标、自我感受、训练重点和来源快照。

幂等语义：

- 同 key、同指纹：返回原记录，HTTP 200。
- 同 key、不同指纹：`409 voice_coaching_idempotency_conflict`。
- Turn 已有快照但 key 不同：`409 voice_coaching_snapshot_exists`。
- Turn 未回答、归属不匹配、来源快照不可见或字段非法：`422 voice_coaching_invalid_payload`。
- Attempt、Turn、Application 或 Event 不存在：`404 voice_coaching_source_not_found`。
- 网络或裸 5xx 结果未知：前端保留原 key 并冻结输入；先读取当前 Turn 快照，再决定同 key 重放，绝不生成新 key。

## 5. API

```text
POST   /api/applications/{application_id}/events/{event_id}/mock-interview/attempts/{attempt_id}/turns/{turn_no}/voice-coaching-snapshot
GET    /api/applications/{application_id}/events/{event_id}/mock-interview/attempts/{attempt_id}/turns/{turn_no}/voice-coaching-snapshot
GET    /api/interview/voice-coaching/snapshots?limit=50&before_id=<id>
GET    /api/interview/voice-coaching/trends
DELETE /api/interview/voice-coaching/snapshots/{snapshot_id}
```

- 列表按 `id DESC` 稳定排序，`limit` 为 1..100。
- DELETE 为幂等物理删除；不存在时仍返回 204，但不得删除其他资源。
- 趋势读取不写数据库、不调用 Provider。

## 6. 确定性趋势与建议

趋势使用最近最多 30 条快照。展示数量不足时明确说明，不补齐、不估算。

可展示指标：

- 最近 5 次与此前 5 次的中位总时长、中位最长停顿、中位语速。
- 每分钟口头禅次数：`sum(count) / sum(total_duration_ms) * 60000`。
- 所有指标附参与计算的快照 ID；少于 2 条时只展示单条记录，不生成变化结论。
- 文案只陈述方向，例如“最近 5 次最长停顿中位数比此前 5 次少 600ms”，不写“能力提升 20%”。

下一项训练使用固定优先级规则，且必须返回触发规则与来源快照：

1. 最近 3 条中至少 2 条 `longest_pause_ms >= 2500`：`long_pause_control`。
2. 最近 3 条合计每分钟口头禅不少于 3 次：`filler_reduction`。
3. 最近 3 条语速的最大值与最小值相差至少 40%，且都有有效语速：`pace_consistency`。
4. 都不满足时不生成训练建议，只显示“继续积累已确认记录”。

同一批数据始终得到同一建议；不得让模型解释或重排规则。

## 7. 前端流程

### 7.1 保存快照

`VoiceAnswerComposer` 在用户确认转写后向 `MockInterviewDrawer` 传递只读 `VoiceDeliverySummary`。原有回答提交仍是第一道确认；只有回答 API 成功或通过原 key 恢复为已回答后，Drawer 才显示第二张卡：

```text
表达复盘尚未保存
本次测量只保存在浏览器；保存后可用于个人趋势。
[可选：写下自己的感受]
[保存到成长档案]
```

- 保存按钮不会重新提交回答，也不会调用 AI。
- 未保存就关闭时，仅丢弃本地摘要，不影响已经提交的回答。
- 保存结果未知时冻结卡片，提供“检查保存结果”；读取不到记录且原请求仍可安全重放时才使用原 key 重试。
- 已保存时显示“已保存到表达成长档案”和“查看成长档案”。

### 7.2 成长档案页面

面试顶层页面增加“表达成长”入口，进入独立 `VoiceCoachingGrowthView`：

- 顶部为最近趋势和下一项训练卡。
- 下方按时间倒序展示记录，包含问题、确认回答预览、测量指标、自我感受和来源状态。
- 长文本使用安全截断与展开，不渲染 HTML。
- 删除前明确确认；删除后重新读取趋势。
- 没有数据、部分读取失败、来源 Application/Event 已删除分别展示不同状态，不能把未知显示成正常趋势。

### 7.3 返回原题重练

训练建议的“重练这道题”使用建议来源中最新的可见快照：

- 导航到其 ApplicationDetail。
- 打开相同 Event 的 Mock Interview Drawer。
- 创建新的 Mock Interview Attempt，不复用旧 Attempt 或 Turn。
- Drawer 顶部显示“本轮训练重点”和来源快照 ID；训练重点只影响本地提示，不进入 AI prompt，不改变问题或证据契约。
- 新回答确认并保存时，将 `focus_kind` 与 `origin_snapshot_id` 写入新快照。
- Application/Event 不可见时按钮禁用，历史记录仍可查看。

Pilot 第一期只增加一个只读快捷入口“查看表达成长”，执行导航而非自动发送趋势给 Provider；不新增写工具或自动提醒。

## 8. 状态、隐私与删除

- 原始音频、PCM、临时字幕、VAD 帧和暂停时间轴永不进入请求体、日志或数据库。
- API/诊断日志只记录快照 ID、指标字段数量、请求指纹和稳定错误码，不记录确认文本、自我感受或口头禅原文。
- 列表和趋势仅面向当前本地用户数据域；本项目当前无多租户身份时，仍必须通过 Attempt/Turn/Application/Event 归属链校验，不能依赖客户端 ID。
- 删除只影响所选成长快照；不删除 Mock Interview、Review Draft、Story、Knowledge 或 Application 数据。
- 趋势缓存不得在删除后继续展示旧数据。

## 9. 测试与验收

后端测试覆盖：

- `0022` 迁移与 `0018..0021` 共存、幂等执行、外键与唯一约束。
- Turn 已回答、未回答、空回答、跨 Attempt/Application/Event、已删除来源。
- 所有数值边界、NaN/Infinity、Unicode code point、口头禅逐字匹配和 offset。
- 同 key 重放、同 key 改输入、不同 key 重复 Turn、结果未知恢复、物理删除。
- 趋势中位数、窗口、来源 ID、固定规则优先级、数据不足及零 Provider 调用。

前端测试覆盖：

- 只有确认文本并成功提交回答后才出现保存卡。
- 保存、重复挂载、结果未知、读取恢复、确定失败解冻、关闭丢弃本地摘要。
- 趋势 loading/error/partial/empty，删除后刷新，来源失效。
- “重练这道题”精确导航、创建新 Attempt、保留训练重点且不修改 AI 请求。
- Pilot 快捷入口只导航；无音频、AI、Knowledge、Memory、Story 或 Application 写入。

浏览器验收使用中文候选人“筱哲”、亮色模式和至少 `1440×900`：

```text
完成一题语音回答
→ 确认文字并提交
→ 保存成长快照
→ 打开表达成长档案
→ 查看趋势及来源
→ 点击重练这道题
→ 新 Attempt 显示训练重点
→ 完成并保存第二条快照
→ 趋势确定性更新
```

如无法自动注入真实麦克风，可注入本地 Mock PCM 展示界面，但必须在截图与报告中标注；它不能作为语音识别准确率或设备兼容性证据。

## 10. 文件边界与兼容性

优先新增独立 repository、趋势纯函数、前端 service/types/view；中心文件只做模型、路由和导航注册。预计修改范围包括：

```text
src/offerpilot/db.py
src/offerpilot/models.py
src/offerpilot/schemas.py
src/offerpilot/api.py
src/offerpilot/repositories/voice_coaching.py
tests/test_voice_coaching_*.py
web/src/types/voiceCoaching.ts
web/src/services/voiceCoaching.ts
web/src/features/mockInterviewVoice/**
web/src/components/MockInterviewDrawer.tsx
web/src/components/VoiceCoachingGrowthView.*
web/src/components/InterviewV01View.tsx
web/src/layout/AppShell.tsx
相关挂载测试、浏览器 harness 与发布报告
```

破坏性变化：无。现有文字回答、语音一期、离线 Whisper 二期、实时陪练三期、Mock Interview API、Story、Knowledge、Adaptive Practice 和 Pilot 写入语义全部保留。
