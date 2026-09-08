# P0 修改确认后的确定性回执

日期：2026-09-08。状态：已实现的施工快照；长期决定见
[ADR-0006](../../architecture/decisions/0006-edited-confirmation-receipts.md)，验收见
[验证记录](../../../artifacts/2026-09-08-harness-p0/verification.md)。

开工基线：`origin/main 4978f6f245bfdd7b396c3f8b221a4d698d29a218`，已重新 fetch。
分支：`feat/20260908-harness-p0-receipts`；使用独立 worktree，不包含主工作区未提交改动。
依据：开发交接方案 `5b3bbb2` 的 §5、§13—15，以及开工基线中的 Runtime 提取契约
`docs/superpowers/specs/2026-08-21-pilot-runtime-orchestration-extraction-design.md`
（已随主线历史文档归档移除，当前行为以 ADR-0006 与 Runtime 实现为准）。
已只读核对飞书主 PRD 的写操作人工确认边界。

## 1. 范围与行为

模型 Agent 写操作在用户合法修改参数、有效参数与原提案确有差异且 Ledger
可靠证明提交成功后，由代码生成回执，结束本次确认后的自动续答。该路径不得调用
Provider、上下文检索、模型摘要或模型标题生成。

原样批准、空编辑、规范化后等价编辑、改回原值、拒绝、非法参数、失败与未知结果
保持既有协议。自动批准、确定性 Pilot 动作、Product Action 不进入本分支。
不新增 Timeline、后台任务或第二套 Ledger，也不改变后续独立用户请求调用模型的能力。

## 2. 参数、策略与事实

- 使用既有 `prepare_pending_action` 和工具参数规则比较原提案与最终有效参数。
  不采用跨工具的宽松转换；缺失、null、false、0 按既有契约区分。
- 原 ToolCall 与原提案保持不变。批准参数、策略版本与原 operation 关联，策略在接受
  批准时固定；不能仅根据本次 HTTP 请求是否包含 `edited_args` 决定恢复行为。
- 策略绑定必须可校验，不改写既有确认指纹，不保存明文凭据，不从 Journal 推断策略。
- 可信终态 payload 是当时提交结果的事实源；当前业务对象值不能冒充历史提交值。
  缺乏字段明细时回退到“操作已保存，部分明细暂不可用”。
- 不根据编辑字段数量宣称“只改了这些字段”，不宣称整个任务完成。
  回执明确说明本次确认后的自动续答已结束。

操作记录增量保存 `confirmation_strategy_version`、
`confirmation_strategy_fields_json` 和 `confirmation_strategy_fingerprint`。
字段列表仅记录需解释的字段名称；固定策略为 `edited_confirmation_receipt_v1`。
HMAC 关联 operation、请求指纹、终态 payload 摘要、策略和字段列表；终态 transport
同时保留策略标记以检测单独删除策略字段。旧操作这组字段均为空，保留原行为。
任何部分缺失、未知策略或关联不一致均不能降级为模型续答。

## 3. 执行与交付边界

沿用原权限、Pending CAS、Ledger 执行与提交。仅在可信 committed 之后、下一次模型
准备之前分支。通过 `ConfirmationCoordinator.final_delivery` 保存原 ToolMessage
与确定性 Assistant 消息，保留 delivery claim、lease、generation/fence、原子消息
保存、Pending 清理和 Undo。不得在业务提交后直接返回字符串。

sync/SSE 使用同一业务结果与保存事实。SSE 仅在交付成功后释放原工具结果事件；
保存失败不得提前向客户端宣告回执已交付。保留 checkpoint、原工具配对、
`provider_blocks` 和批准执行能力的释放。

## 4. 失败、恢复与回滚

| 边界 | 要求 |
|---|---|
| 批准未提交 | 不显示成功；依照既有 CAS/事务语义恢复 |
| 执行明确失败或未知 | 不进入成功回执，不盲目重跑 executor |
| 已提交、回执构造失败 | 可信最小展示或等待恢复，禁止转回模型 |
| 已提交、保存失败或进程重启 | 复用原 operation、原策略与终态结果，不重复执行 |
| 已保存、响应丢失 | 重放原交付，最多一份逻辑回执 |
| 重复确认、sync/SSE 交叉重试 | 保持确认 fingerprint/conflict 与交付唯一性 |
| Journal 关闭、故障或预算耗尽 | 业务结果与回执不依赖诊断可用性 |
| Undo 失败或冲突 | 保留原提交成功事实，独立表示撤销结果 |

采用增量持久化变更，不重置用户数据；旧操作没有新策略证据时沿用旧恢复语义，
不推测其编辑意图，不重写已有历史交付。回滚先停止接纳新策略，保留已绑定策略的
读取、对账与交付。旧二进制不认识新策略时不可直接降级，应保留兼容读取或前向修复。
这一回滚限制属于部署约束；本次没有新增能拦截任意旧二进制的数据库启动锁。

## 5. 验证约定

先增加可控失败测试，断言原 Offer 修改样例一次写入、确认后零 Provider、无反向
Pending。覆盖非命中分支、原提案、真实结果、配对、Undo、新请求、各提交边界故障、
重复确认及重启恢复。运行相关现有确认、Ledger 和普通/SSE 回归。

独立代码审查针对实际 diff 核验策略完整性、授权与交付恢复。发布门禁逐项核验退出码，
不能仅引用 PowerShell 脚本末尾文字。真实模型只在隔离虚构数据与限定次数下验收，
未运行时明确记录，不用一次真实模型成功替代协议测试。
