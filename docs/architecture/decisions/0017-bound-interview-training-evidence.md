# ADR-0017: 将训练证据限定在用户选择的练习计划

- Status: Implemented on feature branch; browser, release and real-user acceptance pending
- 日期：2026-11-09
- Decider：用户同意“面试训练记忆”小闭环方向；本记录说明首版实现边界
- 依据：现役 AdaptivePracticePlan、readiness 来源门控与用户确认的最小范围

## Context

专项练习已经保存回答、反思、自评，但无法从这些字段判断回答是否借助提示或参考答案。
已有完成记录不等于掌握，单次自评也不是能力事实。用户希望下一次练习能参考上一次证据，
展示推荐原因并允许纠正；不应为此自动扫描历史或建立全局能力画像。

## Decision

- 在已完成 V2 Plan 下增加用户显式保存的训练证据，不修改 Plan 完成回执或 readiness 信号。
  首版仅支持用户选择的“幂等与重试”技能；不根据复盘文字自动归类，不迁移旧回答。
- 辅助程度 `unknown/independent/hinted/reference` 是用户声明的作答条件，不是答案评分。
  未记录的历史条件始终未知，回答质量或自评不能用于回填辅助程度。
- 原回答记录引用当前 Plan 的完成回答；复测使用两个固定不同情境。用户看到来源、题目、
  回答及用途后主动保存。不调用模型，不自动写入 Knowledge、Memory、Question 或其他投递。
- 记录、更正和撤回使用计划级 revision 与命令幂等键；相同键改变内容冲突。结果未知时保留
  原命令重试，不以新键重建。重试读取当前状态，不复活已撤回或已更正的旧回答。
- 撤回清空新增证据中的回答并停止其推荐用途；不删除原 Plan 回答或已有备份。历史命令回执
  不保存回答正文。新记录与更正受来源、目标及冻结指纹的实时检查；来源失效仍允许撤回。
- 建议只引用当前有效、未撤回的本 Plan 记录，解释记录时间、辅助条件和下一练习情境。
  对比只说明作答条件变化，不声称“能力提升”“已掌握”或招聘预测。来源失效时暂停建议。
- 不改变现有 Preparation 显式选择契约；本版训练回答不进入其 Provider 输入。
  不增加 AI 写工具，不改变 HITL、Provider catalog 或既有跨领域确认协议。

## Consequences

首版能体验“保存条件与回答 → 换场景复测 → 查看依据 → 更正或撤回”的本地闭环，
但不能证明长期学习效果或真实模型建议质量。新表是加法迁移，无历史删除或破坏性迁移。
受限的技能与情境刻意避免变成通用课程系统；后续扩展需先看真实使用证据。

## Alternatives Considered

1. 在原 Plan completion 上直接追加自动 Memory 写入：把练习完成偷换为跨领域确认，拒绝。
2. 用模型推断旧回答是否独立并打掌握分：缺乏可观测依据，且引入费用与误判，拒绝。
3. 全局能力图谱和复杂遗忘算法：不能先证明建议更有效，超出首版最小范围，暂缓。

## Related

- [面试业务地图](../code-map/interview.md)
- [复盘到准备的历史设计](../../superpowers/specs/2026-08-30-review-to-readiness-feedback-loop-design.md)
- [Knowledge 唯一事实源](../knowledge-system.md)
- [小样本验收矩阵](../interview-training-evidence-evaluation.md)
