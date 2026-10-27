# 固定模型验证控制器的无密钥演练

此目录是迁移第一阶段：固定六例的数据协议、平台身份与历史账本核验，
以及独立 fake 执行。**不是已上线的收费入口。** 生产登记未配置，live 必须拒绝。
所有演练平台身份和批准记录均为合成 fixture，不能证明真实环境保护已生效。

## 当前范围

- 开发分支 `feat/20261027-fixed-ai-controller`，不修改 master、companion 或旧 R。
- 新演练 workflow 不引用 environment、provider secret 或真实付费 job。
- 候选文件只作为受限 JSON 字节读取；不得导入候选代码、安装候选依赖或执行其输出。
- 复用当前受信源码中的纯账本验证，保留四批真实历史及第四批缺失 requests 的事实。
- 新记录明确标记 rehearsal，不可当真实费用、平台批准或生产 CI 证据。
- 单 owner 的保护不防 owner 恶意或账号失陷；同一允许分支上的所有 workflow 均处于信任域。

## 离线验证

```sh
uv sync --frozen
uv run python -m scripts.companion_ai_controller.test_gate
uv run ruff check scripts/companion_ai_controller scripts/companion_ai_validation
uv run mypy scripts/companion_ai_controller/*.py --follow-imports=silent --explicit-package-bases
```

完整 gate 包含原 helper suite、新控制器 suite 和平台契约测试，保存逐节点
setup/call/teardown aggregate 到 `artifacts/companion-controller-offline.json`。
skip、deselect、重复或遗漏节点均不算通过。fake 执行命令及产物以 `rehearsal --help` 为准。

## 费用与时间没有重置

原 campaign 为 `one-shot-20261021`：48 次、411934 micro-CNY，
总上限 20000000 micro-CNY，每次预留 3000000。当前六例原字节不改写，
最多六次，不重试、不补第七次。截止仍为 2026-10-28 00:00 UTC，开始前需剩完整 20 分钟。
这是保守估算账本，不是 provider 账单的独立硬上限。

fake 时钟是测试数据，不是延长真实授权。任一真实 paid-started 必须消耗批次尝试，
取消、缺失工件或未知费用均阻断后续准入；无法证明发送数时保留整批最坏 18 元负债。
独立无密钥演练没有真实 paid job，不提供用户可控的 dry-run 计费豁免。

## 以后可复用什么

拟由本人一次性配置 `OFFERPILOT_COMPANION_AI_CONTROLLER_SHA`，
只在升级执行器时更新；它不是每批 H/R，也不批准任意数据。
运行输入中的候选 SHA、证据和请求仍需核验及本人最终 Review。
当前连接没有 dispatch 写工具；没有已验证的已登录执行面时，
体验仍是用户 Run workflow 加 Review 两步，不能声称已实现自动触发。

## 未实施且仍需本人明确批准的上线事项

1. master 只登记手动入口；真实控制分支固定审查版本后整体锁定。
2. 本人设置固定 controller SHA；核验完整分支保护、禁止绕过、强推和删除。
3. 停用旧付费入口、排空 waiting/queued/running，保持全部运行历史。
4. 本人改变环境只允许精确 control 分支，保留唯一本人 reviewer 与 no bypass。
5. 无真实密钥的平台演练先完成，再由本人配置/迁移密钥，助手不读取值。
6. 登记实际 workflow/environment/policy 身份并独审新上线快照；当前 fixture 不能替代。
7. 另行确认本批执行及截止；旧 R 目前仍未激活，不因本阶段而标为已替代或启动。

普通 contents/actions 只读 token 不能读取完整 branch protection；公开 rulesets
也会隐藏部分 bypass 信息。不得增加长期管理员 token 掩盖这个边界，
最终 Review 仍依赖本人确认控制设置未变。若要求逐批机器证明全部隐藏设置，仍是上线阻塞。

详见 [迁移架构记录](../../docs/architecture/decisions/0017-fixed-ai-controller-rehearsal.md)。
