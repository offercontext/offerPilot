# 九项定向驱动的离线验证入口

该入口只运行本地假 Provider，不读取真实凭据或消费真实费用授权。模拟输出不计入正式真实场景通过率，也不替代完整本地 release gate。

## 冻结与执行

在待验 checkout 中创建仅包含 `candidate` 的 variants JSON，指定 checkout 和解释器绝对路径。调用离线 CLI：

```powershell
oc eval targeted-offline-plan --suite evals/suites/harness-core-v1.json `
  --variants variants.json --output artifacts/harness/new-offline-session `
  --select E01.runtime_read --select E02.runtime_clarify --select E03.runtime_reject `
  --select E04.runtime_edit --select E05.runtime_replay `
  --select E06.legacy_saved --select E06.runtime_saved --select E06.legacy_missing `
  --select E07.runtime_reattach
oc eval targeted-offline-run --plan artifacts/harness/new-offline-session/targeted-plan.json
```

父计划保留 `harness-core-v1` 的全部13个 real 身份、用例和 fixture hash、必需规则、代码/锁文件/依赖环境快照，单候选、单轮。九项选择必须精确匹配；未知、重复、越界或快照不匹配在发送前拒绝。另外四项在本计划中始终 `not_run/incomplete`，不迁移历史人工确认。

模拟子进程使用另一个 run ID 和 `deterministic` mode，并显式记录 `simulation_only=true`。输出将父身份与子结果分别绑定；模拟行不能匹配正式父计划的试验身份。诊断探测不作场景成功判断。

阶段调度与恢复位于 `core_targeted_kernel`，封存核验位于 `core_targeted_evidence`，持久请求算法为 `TargetedLedger`。离线入口只提供后端和模拟身份，复用同一个内核；`OfflineLedger`仅固定模拟标记。正式父试验身份、独立诊断身份和模拟子身份由同一解析函数区分。账本可在本地持久化后、ACK前调用外部权威锚；该锚写入失败仍保留已允许预留，不发ACK。

`targeted-offline-run` 的退出码是选中九项的模拟结果：全部通过为0，存在已知失败为1，缺失/中断且没有已知失败为3；配置、账本或绑定错误为2。生成报告成功本身不代表九项完整通过。

## 额度与恢复

- 探测和九项目标共用一个持久化请求计数，最多27次；探测最多1次，因此目标最多26次。每项目标最多8次，仍受原有调用、工具及时间保护约束。
- SDK和网关自动重试均为0，fallback为空，title model禁用。每次实际 HTTP 发送前，控制器先完成额度、到期及当前快照检查，再持久化账本和独立 anchor，最后 ACK。失败、超时和未知计费的已允许请求继续保留模拟预留。
- 整个会话复用现有 `store_lock` 的内核互斥机制，锁文件位于该模拟会话内部；不获取或修改真实共享费用总账。`--simulated-cny` 只控制隔离的人工算术额度，不是报价或真实费用授权。
- `run-budget.jsonl` 包含原请求身份、阶段、epoch/seq、输入/输出保护额度和 hash 链。`ledger-anchor.json` 在每次 ACK 前记录计数、尾 hash 与保留金额。恢复要求两者一致，并核对 state 的已提交账本前缀。
- started/unknown 项永不自动重发；完成项须核验 child identity、结果文件、封存证据及请求计数。缺失账本、半行或完整行截尾、错绑均保守拒绝，不自动重建或归零。使用同一个 `targeted-plan.json` 恢复；修改源代码后重新冻结新会话。
- 任一预留被拒绝后，该会话后续执行及恢复都停止，不因保留金额尚未等于上限而尝试其他场景。已允许但失败、超时或结果未知的预留不退回。
- 探测成功要求封存的控制收口、`facts.terminal_state=completed`、驱动完成及HTTP200观察共同成立；恢复重算并核对 `probe_ok` 和控制关闭标志。驱动读到业务失败终态不等于Runtime成功，状态文件也不能将失败探测改成成功。
- 恢复时核对 evidence trial key、marker、handshake（含产品与数据库路径）、coverage、private path 和完整必需规则，并用同一冻结评估器只读重算结果以拒绝伪造结论，不覆盖 raw。所有已有raw的阶段须校验每条attempt身份与范围，全部已记录预留（含拒绝）按身份、epoch、seq、输入和输出额度逐条匹配并拒绝重复。已收口阶段还要求所有attempt恰好对应允许预留；未知阶段可保留保护在预留前截断的意图，另记`partial`、拒绝数及未预留意图数，不将它们计为实际发送或完整覆盖。
- `stage_status=completed` 只表示控制记录收口；`control_records_closed` 与 `trial_execution_status`、原始 verdict 分别保留。恢复先统一将 started 改为 unknown，即使授权已到期；完成阶段缺失结果时拒绝恢复。raw 已落盘但父状态未提交时，保留 raw，阶段仍为 unknown，整体不能通过。

局部模拟故障仅用于离线测试，不能改变产品 HITL、业务逻辑、评分规则或阈值。合法确认流程仍由原 driver 驱动；假 Provider 只能连接试验进程的 loopback 端点。

## 发布边界

当前 `core-release-v5.json` 要求19项 deterministic 各3轮、独立审查、严格基线比较；`review_rules=[]`。裁判在设计 §10 中为可选能力，校准0/20不是所有发布的自动必经阻塞。完整本地 gate 与发布政策仍分别需要对应快照的实际证据。

已批准历史基线位于 `harness-policy-binding-fix/jobAggregation/artifacts/harness/approved-baselines-v24`，ID为 `harness-core-f78346f0-d3-20261015`。其冻结评估器与后续候选不同，严格比较的兼容性需要后续处理；本入口不修改、重新晋升或移植旧基线分数。旧57/57仍只绑定旧快照。本轮不启动真实Provider、裁判、Bash排查或完整gate。
