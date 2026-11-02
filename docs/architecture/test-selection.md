# 快照绑定的测试选择器（master 移植）

这是开发反馈工具，**不是 release gate**。计划和执行摘要始终 `release_eligible=false`；ready-for-review / 发布仍需现有完整门禁。完整任务被 skipped 不能充当发布证据。

## 来源与安全基线

- 来源分支 `chore/20261031-harness-master-integration`，SHA `9f235b1dc2b249298320466fdc2009dda3e3f8f6`；初始 master 为 `7fca9101fb78fceca15b4ac169cd52561551100c`。
- 独立 runtime 安全前置：`682a8d33881d21c841294d17d34ade105567c250`，补齐两个状态读取竞态回归。
- `tests/selection-master-migration.json` 记录真实收集摘要、每条保留/新增/不可用 mandatory identity，以及每个映射增删。原 `selection-safety-v1.json` / `-v2.json` 保持来源字节，作为历史，**不宣称本移植满足原 v1/v2 完整验收**。
- 独立注册 `selection-safety-master-v1.json`：完整保留 69 条来源非 evaluation identity，新增其余 48 条 selector 守卫，共 117 条。缺失、改名、降级或重复身份不能通过验证；执行结果缺项、重复、skip 或证据不完整不能算成功。
- 初始目录为 325 个后端文件 / 7,983 nodes 和 298 个 Vitest 文件；共 619 P0 / 2 P1 / 2 P2 文件。这种保守分级不承诺立刻获得模块级提速。新增测试必须显式审查并登记 metadata，不能按路径猜测或静默漏测。

## 用法与既有语义

先安装仓库锁定的 Python dev 与 frontend 依赖，在同一 checkout 和 Python 环境执行：

```sh
python scripts/select_tests.py validate
python scripts/select_tests.py plan --base <commit> --profile dev --output artifacts/test-plans/<unique-run>
python scripts/select_tests.py execute --plan artifacts/test-plans/<unique-run>/selection-plan.json
```

`validate` 做真实 pytest / Vitest **收集**，不是执行测试。`plan` 输出 `selection-plan.json` 和原因说明，不覆盖已存在目录。仓库内输出只允许被 Git ignore 的 `artifacts/test-plans/` 下，也可放仓库外。

计划后不得更改源码、Git index、依赖或配置；变化后重新计划。同一 CI job 内完成 plan / execute，不能把其他 checkout 或环境的计划当执行授权。

- `dev`：直接修改的测试 ∪ 影响模块所有优先级 ∪ 影响 surface 的全部 P0 / unknown。
- `merge`：在上述基础上增加全局 P0。
- `full`：全部 collected 测试和来源完整 required checks。

影响分析保留反向 `depends_on` 闭包、旧新映射并集、rename 两端，以及 committed / staged / worktree / untracked。继承的未知 `src/` fallback **仅后端 surface**，不能据此认定共享代码不影响前端；CI 外层必须将未知或高风险改动回现有 full。

## 明确未迁移的能力

未迁移来源 Harness evaluation 生产栈、suite 或双 checkout 六次验收；其 42 条 mandatory evaluation identity 在审计中逐项列为不可用，不删除或重命名历史来假装通过。

`full`、full-risk 扩张或 evaluation 影响仍在 `required_checks` 保留 `harness_p0`，但计划显式返回：

```json
{"selection_status":"blocked","unsupported_checks":["harness_p0"],"needs_full":true,"release_eligible":false}
```

成功生成 blocked 计划时 `plan` 退出 0；`execute` 在任何测试或命令启动前做整个计划的能力检查，返回 3。外层可在**计划阶段**回现有 master 完整 release gate，但该 gate 只验其原有范围，不等于来源额外 Harness 验收。已经执行失败不能用成功 fallback 洗掉。

初期 Draft 接线只允许三个已核验样式角色：`web/src/theme/tokens.css`、`web/src/layout/Sidebar.module.css`、`web/src/layout/TopBar.module.css`，且仍需原 dev 计划为纯 frontend、无 risk expansion / fallback / deleted tests / unclassified / unsupported。这是按 AGENTS §7 的展示范围缩减，不是把所有前端视为低风险；`services/chat.ts`、`services/pilotRuntime.ts`、认证/http、共享 types、确认/恢复 controller 以及装配多业务写入与草稿的 AppShell 均回 full。多文件和重命名两端取并集，出现一个未允许路径就回 full。依据见 [Pilot 地图](code-map/pilot-runtime.md) 与 [设置/桌面地图](code-map/settings-desktop.md)。其他范围和计划错误明确回现有 full。不得另行放宽 selector 的 content-only allowlist，尤其 prompt、template 和 runtime Markdown。

## Windows 前端命令边界

Windows 的 `npm.cmd` 经批处理入口受命令长度限制，298 个文件过滤参数实测达 14,831 字符，不能用 Linux 成功冒充 Windows 已验证。执行器仅在非空、无重复、规范化的 selected frontend 身份与冻结 catalog **完整集合相同**时省去冗余文件过滤参数；仍运行原 `npm test`，保留 pre/post 生命周期、配置、report、worker 参数及严格执行对账。真正子集仍携带原文件过滤参数。

Windows 命令先检查引用后的 UTF-16 长度（保守 6,000 上限，为 npm/cmd 展开留余量）和批处理元字符。无法安全表达时，规划阶段回 full；执行阶段则在任何选测/后端命令启动前 blocked，不截断、不分批、不在执行失败后 fallback 洗绿。完整 Windows 候选的早期契约另以两个极小 Vitest 文件验证真实 npm/ProcessTree、全集合/子集和失败传播，不另给每个 pytest shard 安装前端依赖；其证据只是这条薄链，不等于完整 selected 或 release 验收。

## 平台限制

保留来源 ProcessTree 子进程所有权与清理行为；唯一平台适配是以 `sys.platform` 判别 Windows，使 mypy 能分别检查平台代码，不屏蔽 Windows API 类型错误。Linux cleanup 回归和平台静态检查不替代真正 Windows ProcessTree / PowerShell 分组验证；未取得 native 证据前不能声称 Windows 完整门禁通过。

`group_catalog` 的真正组合验证还需阶段一 `agent_loop` 归组修复；本移植的 AST 纯函数导入已包含 `Get-OrdinalDuplicates`，以兼容阶段一 `Get-Groups` 的重复检查依赖。尚未接线或取得 native Windows 结果时，不宣称分组验收已通过。
