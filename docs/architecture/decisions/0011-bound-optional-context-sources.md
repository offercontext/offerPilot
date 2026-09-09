# ADR-0011：按确认、范围和预算装配可选上下文

- Status: Accepted（实现验证见关联计划）
- 日期：2026-09-09
- Decider：用户授权连续实施的 Harness 路线图 `5b3bbb2` §10（P4）
- 依据：[Knowledge 架构](../knowledge-system.md)、[执行所有权](0010-own-pilot-execution-in-runtime.md)

## Context

Pilot 需要复用准备重点、已确认偏好、知识成果和较早对话。它们的有效期、可信程度及确认来源不同，
不能直接拼接成最新用户指令，也不能绕过现有证据校验、Provider 总预算或业务写入确认。

## Decision

四类 Contributor 使用独立开关、版本与字节预算，并继续服从统一 Projector 总预算。
可选数据放在当前请求之前，以参考数据封装，不产生工具结果或授权。Manifest 只保存安全诊断与指纹。
来源读取通过有 deadline、只读事务和大小限制的 ContextSourceLoader，投影不执行模型生成。
读取遇到已识别的临时锁竞争或 deadline 时，本次省略可选来源并记录 unavailable；不自动换绑
Readiness，也不替换原历史。作用域、完整性和未知读取错误仍终止投影，不能借降级绕过验证。

- Readiness 由用户在投递会话明确选择目标面试、简历和确认版本。复用领域 SelectionLoader 校验当前
  来源、Evidence、撤回状态与指纹；不推断目标、不转成长期能力标签。
- Confirmed Memory 只保存用户明确确认的表达与排序偏好。提供查看、版本化编辑、撤回及删除；
  幂等 Mutation/CAS 属于管理命令，Agent 不自动写入。Memory 不能证明外部事实。
- Knowledge 使用当前有效 Note 与 Evidence 两条召回通道，交替排序并分别保留预算；已被选中 Note
  覆盖的 Evidence 去重，未进入 Note 的 Evidence 仍可召回。引用必须回读到当前 Source Snapshot。
  Note 编辑产生新版本并保留 Evidence 绑定；归档、删除、源更新及撤回会阻止旧内容注入。
- 较早对话摘要通过显式命令生成确定性摘录，不调用 Provider。最多读取 32 条、32 KiB 来源，每日最多
  4 次生成；同一来源使用缓存。保留消息 ID 范围、来源摘要值、生成版本和输出摘要值。
  用户陈述与模型推断分开，未校验业务事实不进入 supported facts。原消息始终保留。

摘要只替换与来源完全一致的普通历史前缀，并保持完整对话轮次。工具链、provider_blocks、最近消息
保留原文。摘要未纳入预算、来源变更或缓存失效时，回退到原有有界历史选择，不自动重新生成。

## Consequences

来源失效可在下一次模型投影前生效，开关对照不改变当前请求、工具表面或 HITL。代价是新增少量
版本与幂等记录，以及来源校验查询。摘录属于有损压缩；界面显示省略数量，并允许撤回。

这些能力不引入向量库、自动 Wiki、Brief 消费或未经确认的记忆写入。真实模型质量与费用验收由
用户另行安排，自动测试证明范围、失效、预算及协议行为，不替代质量判断。

## Alternatives Considered

1. 把所有历史信息追加为最新 user 消息：可能遮蔽当前请求并让参考数据被解释成授权，因此不采用。
2. 在每次投影时用模型总结全部历史：费用、延迟和输入无法稳定约束，因此采用显式生成与缓存。
3. 只召回 Note：未沉淀的原始细节会被隐藏，不符合 Knowledge 的双通道约束。

## Related

- [连续交付计划](../../superpowers/plans/2026-09-09-harness-p3b-p5-delivery.md)
- [Knowledge 主文档](../knowledge-system.md)
