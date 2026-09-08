# Product Convergence Audit Release Verification

日期：2026-07-27
基线：`f074325`
分支：`feat/20260724-evidence-gated-interview-preparation`

## 自动化门禁

| 检查 | 结果 |
|---|---|
| 后端收集 | 1514 tests collected；agent 425、domain 71、knowledge 658、proposals 271、misc 89，覆盖并集 1514/1514，missing=0、extra=0 |
| 后端分组执行 | 425 passed；71 passed；654 passed + 4 skipped；271 passed；89 passed |
| Windows skip | 仅 4 个符号链接权限用例，统一原因“当前环境没有创建符号链接的权限” |
| `uv run ruff check .` | 退出码 0 |
| `uv run mypy src` | 退出码 0 |
| `cd web; npm.cmd test -- --run` | 退出码 0 |
| `cd web; npm.cmd run build` | 退出码 0 |
| `uv run oc smoke --static-dir web/dist` | 退出码 0 |
| `uv run oc verify --profile local --static-dir web/dist` | 退出码 0，临时隔离目录，源目录快照不变 |
| `uv run oc verify --profile real-ai --static-dir web/dist` | 退出码 0，临时隔离目录，真实 Provider 连通；源目录未写入 |

`scripts/windows-pytest-groups.ps1` 的直接单次执行超过本地工具 900 秒限制；随后使用该脚本相同的收集 manifest 与分组规则逐组执行，五组均退出码 0，覆盖集合精确一致。没有把单次超时报告为测试通过。

## 浏览器验收

使用现有 AI 配置、隔离临时数据目录和真实本地服务完成阶段一：

- 顶层“面试”→“准备面试”→选择合成 Resume→粘贴 JD→人工确认 AI 调用→真实生成。
- 生成结果包含准备方向、经历故事提示、复习点、确认问题和证据引用；证据来自 JD/Resume，未显示内部快照。
- 关闭后重新进入同一面试，打开历史记录并查看冻结结果。
- 阶段一零跨领域写入断言通过；隔离库未产生 Material Kit、Knowledge、Question、Memory、Reminder、Mock Session 或投递状态变更。

阶段二尚未完成：浏览器内从投递详情进入 Pilot 岗位评估并人工确认后，Triage 两次真实调用均返回安全的 `502 opportunity_fit_unverifiable`，没有创建 Review，因此未继续执行 Deep Review，也未将浏览器阶段二标记为通过。相同 Provider 和隔离环境下，`real-ai verify` 的 API 路径已成功覆盖 Triage、Deep、复盘建议、知识确认和 Chat 逐次确认。

未观察到招聘平台或外部 JD 请求；浏览器请求限定为本地 `/api`、静态资源和已配置 AI Provider。临时服务、数据和配置副本已停止并清理。

## 代码与工作区

本次仅补充发布验证所需的测试夹具、隔离验证回归和文档报告；没有业务 API、数据库迁移或产品功能变化。工作区在提交前应保持干净；剩余风险是阶段二真实浏览器 Provider 输出稳定性，需重新验收后再推送或合并。

## 2026-07-28 门禁脚本复核

针对复审反馈，分组脚本已改为解析每组 JUnit 报告，精确校验 skip node id 与原因；完整 manifest、组内和跨组 node id 都在去重前检查重复。修复后重新执行各组并使用同一 JUnit 规则校验：agent `425 passed, 0 skip`；domain `71 passed, 0 skip`；knowledge `654 passed, 4 allowed skip`；proposals `271 passed, 0 skip`；misc `89 passed, 0 skip`。四个允许 skip 均为符号链接权限测试，原因均为“当前环境没有创建符号链接的权限”。

由于本地工具对单次进程有 900 秒上限，包装脚本本身仍未在单次调用中跑完；上述五组是按脚本相同文件分组、收集和 JUnit skip 校验逻辑逐组重跑，未将单次超时报告为通过。
