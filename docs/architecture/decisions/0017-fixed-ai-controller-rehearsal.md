# ADR 0017 固定模型验证控制器的离线迁移

- Status: 第一阶段无密钥实现；生产控制面待单独批准与验收
- Date: 2026-10-27
- Decider: 用户批准非 master 实现与无密钥演练；未批准 master 合并或安全设置修改
- 依据: 已审固定审批入口迁移方案及当前 companion helper 的四批历史证据

## Context

原 companion 付费入口用逐批 H/R 变量绑定精确代码，用户手工复制负担较重。
不能通过删除 SHA 检查或让请求提交自己声称可信来减轻负担。
环境分支规则不是 workflow allowlist；受信执行代码必须独立于候选数据。

## Decision

先在独立开发分支实现纯数据核验与 fake 执行，生产身份未配置，live 拒绝。
保留原 helper 和真实费用历史，独立演练 workflow 不引用 environment 或 provider secret。
候选内容不得作为代码、依赖、缓存、命令或 URL 执行。

后续设计是在 master 登记无密钥入口，实际 dispatch 使用整支锁定的控制分支，
环境仅允许该分支。一次性外部 controller SHA 只在执行器升级时变化，
每批不可变输入、完整证据、账本和本人批准仍绑定本次运行。
这些未来设置与收费路径不由本阶段自动建立。

## Consequences

可验证候选注入、身份错配、坏 artifact、重复批次、未知成本和时间边界，
不能把 mock 平台合同通过称为真实保护就绪。保留单 owner 信任根：
只读 token 看不到所有保护/bypass 设置，本人必须在上线和维护时核验。
失败或未知状态不得变成免费、重试授权或新预算；默认回滚是冻结。

## Alternatives Considered

1. 复用旧 H/R 数值：不能批准新代码，拒绝。
2. 在请求提交里自动推导 H/R 后自验：缺乏独立信任基准，拒绝。
3. 新建独立验证仓库：隔离更清楚，但多出凭据迁移和跨仓库历史成本，非默认。
4. 控制分支逐批 push 请求：破坏长期锁定，非最终推荐；本阶段开发分支 push 只跑无密钥测试。

## Related

- [控制器说明](../../../scripts/companion_ai_controller/README.md)
- [原 helper](../../../scripts/companion_ai_validation/README.md)
- [GitHub 手动工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [环境与部署保护](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [分支保护读取权限](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection)
