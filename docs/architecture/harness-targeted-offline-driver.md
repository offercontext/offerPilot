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

## 正式授权入口与离线验证范围

`oc eval targeted-plan`、`targeted-preflight`、`targeted-run` 共用上述调度、证据核验和持久请求算法。正式后端使用原 real 父身份，探测通过独立的 `diagnostic_probe` 参数和独立诊断 run ID 执行，不能伪装成 offline profile，也不把完整 E01 当探测。正式结果仍保留13项，其他四项保持未执行。该入口的存在不表示已经运行或获准发布。

生产装配只从 OS 账户目录下 `.codex/evaluation` 的权威注册表读取账户与授权；不接受账户路径、注册表根或 grant claim 根的 CLI / 环境变量覆盖。当前阶段没有安装真实注册记录，也没有预留真实费用。未配置、目录可被其他主体写入或证据不齐时，只读预检返回配置错误。权限检查只读取 owner/DACL、拒绝 symlink/junction；测试仅调整新建的空临时目录权限，不改变全局或真实账户 ACL。该信任边界不抵抗同一 OS 用户或管理员主动伪造授权，哈希也不是人工身份认证。

权威配置以安全标识符命名，绑定规范绝对 `account_path`、其已批准 genesis hash、10元总授权、单会话上限及授权原文引用。相对路径、账户副本或配置迁移不能沿用旧计划。审批记录另以稳定 `authorization_id` 绑定整份计划 hash、grant hash/ID、到期及原始授权证据，不能换 grant UUID 消除同一次授权占用。配置必须来自已有可信授权，入口不自行注册或扩充额度。

冻结计划命令必须提供 `--suite`、`--variants`、`--profile`、`--grant`、`--price-audit`、`--gate-receipt`、`--authority`、`--authorization`、`--output` 和九项 `--select`。它不加载凭据或发送请求；按当前快照验证13父项、8次局部保护、最多27次总请求与零重试，并复制 grant、核价与本地完整 gate 回执。核价需匹配完整 profile、源资料 hash 和有效期；gate 必须有同一代码/锁文件快照的完整原生命令成功回执。模拟回执不能通过生产预检。现有估价草案不满足这些新运行条件。

运行前使用 `targeted-preflight --plan <新目录>/targeted-authorized-plan.json` 只读核对全部绑定、最新账户余额与独立授权记录。实际 `targeted-run` 先取得原共享账户 `task-budget-paid.lock` 的同一个内核字节锁，再取得会话锁，持有到全部子进程处理结束。账户总授权仍为10元，每次 cap 不超过最新可用余额及2.394210元，不创建新额度。

双 authorization/grant claim 在发送前持久化到权威根，仅相同计划、run 和目录可以恢复；部分 claim 不自动补齐。账户分配先写 intent，再原子替换原账户，最后写 receipt。中途断裂、账本缺失/截尾、配置或历史 hash 不符一律停止并保留分配，不通过新 session 解锁。旧 schema 的所有 closed_runs 和未知计费预留逐项只读核对；旧行不改写、不当作实际账单或退款。

每次 pre-ACK 都重查授权、核价、代码、账户版本及权威 spend anchor；本地请求行和 anchor 持久化后，再提交权威 anchor，全部成功才 ACK。允许发送数、HTTP响应数、transport header 观察数和无响应的未知窗口分别报告；预留不证明实际发送或服务端接收。失败、超时或 ACK 结果未知的允许请求全额保留。

`control_records_closed`、`owned_subjects_exited`、control reader 退出与功能 verdict 独立记录。正式后端及测试中的授权后端恢复时，任一旧阶段的进程退出无法证明就停止新阶段；已证明退出的 unknown 阶段仍不重发、不改判通过。功能九项通过但控制/账户尚未安全收口时，保留 `selected_status=pass`，会话为 `session_status=incomplete`、退出码3。

只有全部已启动的受拥有进程树及 control reader 退出得到证明、ledger/权威锚及封存证据一致，才追加 closed_run 和关闭账户分配。释放的是未使用分配差额，已允许的未知费用不退。先保存派生报告，并将其 hash 与 ledger hash 一同绑定进账户 close 事务；报告必须有对应已提交账户 receipt 才证明闭账。closed 会话恢复为只读，篡改 stage、矩阵、结论、金额或 transport 字段都会失败。若该账户后来又完成其他会话，旧 session 当前安全拒绝恢复，需用封存证据进行只读审计。

空账本/权威初始锚写齐前中断也会保留 allocation；当前没有自动补初始化或人工解锁命令，需独立审计授权后另行处理。此限制阻止继续付费，不会重置已预留请求。完整关闭事务的部分写入同样保留为审计阻塞。

正式导出只在准备安全结账时先写完整13项 `run-manifest.json` / `trial-results.json`，再将两文件的精确路径和 hash 绑定进报告与账户关闭事务。closed 恢复只读核对文件、hash及计划/矩阵内容，缺失或冲突均拒绝，不重建。若账户仍 open 而正式导出或其临时文件已开始写入，当前直接保留 allocation 并要求审计，既不覆盖冲突文件，也不继续发送。本阶段只用不可导入的 toy 数据测试封存原语，真实 Provider 的正式导出分支尚未实际执行；模拟集成仍不生成真实父结果集合。

零费用集成通过内部装配注入同一账户/claim实现的临时根和 loopback 后端；不提供生产绕过开关。模拟子身份与真实父身份始终隔离，不导出可冒充真实父运行的结果集合，真实请求数为0。集成需要实际九项 CLI、27次边界、原生竞争控制器、父进程中断与恢复；原语及合成证据测试只作补充。

## 发布边界

当前 `core-release-v5.json` 要求19项 deterministic 各3轮、独立审查、严格基线比较；`review_rules=[]`。裁判在设计 §10 中为可选能力，校准0/20不是所有发布的自动必经阻塞。完整本地 gate 与发布政策仍分别需要对应快照的实际证据。

已批准历史基线位于 `harness-policy-binding-fix/jobAggregation/artifacts/harness/approved-baselines-v24`，ID为 `harness-core-f78346f0-d3-20261015`。其冻结评估器与后续候选不同，严格比较的兼容性需要后续处理；本入口不修改、重新晋升或移植旧基线分数。旧57/57仍只绑定旧快照。本轮不启动真实Provider、裁判、Bash排查或完整gate。
