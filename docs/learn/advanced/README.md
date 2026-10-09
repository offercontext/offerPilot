# 从概念走到源码：OfferPilot Harness 进阶导读

这部分接在[二十二篇概念入门](../README.md)之后，回答“这些机制到底怎样实现，以及 OfferPilot 为什么这样组织”。可以直接按专题阅读，不必重新读完全部入门篇。

建议具备简单的 Python、JSON、函数调用和数据库读写基础。事务、CAS、租约等概念会结合失败场景解释；本系列不要求先熟悉某个 Agent 框架。

## 想动手，先从小实验开始

[最小实验](../examples/README.md)只使用 Python 标准库，模拟“查询 → 待确认 → 保存 → 重复确认”。先观察 0→1→1 三个数量，再读本系列的生产实现。它是可选入口，不要求所有入门读者安装项目。

## 六个专题

| 顺序 | 文章 | 读完要能解释什么 |
| --- | --- | --- |
| 1 | [拆开 Agent Loop](01-agent-loop.md) | 一轮模型调用如何变成工具执行；读、写、暂停和结束怎样分支 |
| 2 | [Harness 怎样拆分职责](02-harness-boundaries.md) | Loop、Runtime、工具、领域存储与诊断为什么分别承担不同工作 |
| 3 | [从工具请求到数据库写入](03-write-approval.md) | 参数、确认身份、事务、重复请求和实际业务效果怎样关联 |
| 4 | [模型真正收到了什么](04-context-projection.md) | 上下文怎样分配预算、保持完整性、形成可核对的模型输入 |
| 5 | [取消、断线和重启](05-cancellation-and-recovery.md) | 执行代次、租约、提交围栏、重新订阅与恢复展示的区别 |
| 6 | [运行记录与验证](06-observability-and-validation.md) | 怎样用业务结果、运行记录和失败实验验证 Harness |

每章都包含具体问题、流程或职责图、源码入口、设计取舍及边界。适合通过现有测试观察的地方，提供有明确断言目标的命令；教学伪代码明确标注为不可直接运行。

## 源码与图的阅读约定

源码与截图的核对日期为 **2026-10-08**；阅读路径与小实验于 **2026-10-09** 更新。源码基线是远端 `master` 提交 [`c0a447bb`](https://github.com/offercontext/offerPilot/tree/c0a447bbe7be8976fe9a53c2bcbf3b91dad0eeac)，本分支是在该版本上增加教学文档。关键源码链接固定到这次提交；本地测试链接方便在同一 checkout 中阅读，不意味着其他版本的函数和行号完全相同。

文中区分三种材料：

- **源码与 ADR 所描述的实现**：给出具体入口，说明只覆盖哪条路径；设计记录不单独当作功能验收。
- **教学示例与结构图**：依据机制绘制，但示例中的公司、ID、日期和返回文字不是真实执行数据。
- **实际运行素材**：2026-10-08 使用隔离虚构数据和真实模型新采集了 14 张截图，其中 11 张为产品界面、3 张为真实只读诊断输出的排版截图；[采集条件与证据](../images/runtime-20261008/README.md)统一记录。

流程图不依赖启动产品。正文提供静态 SVG，旁边保留可编辑的 Mermaid 图源；即使阅读器不支持 Mermaid，也能看图。图中只展示本章需要的分支，不表示完整调用栈或所有异常路径。

维护时修改折叠区的图源，并同步重新导出 `docs/learn/images/flowcharts/` 中对应的 SVG；复查中文标签、箭头与正文是否一致。入门与进阶目前共 23 张静态流程图。

原有 14 处素材占位已替换为实际观察、截图或诊断输出，并说明各自验证到哪里。相同操作的素材可在不同章节复用；没有把服务重启、并发重试等未执行场景写成通过。

## 第一次运行源码测试

小实验与下面的产品源码测试是两个层次。后者需要 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和项目开发依赖。为避免切换你已有的工作区，用一个新的阅读目录固定到本文引用的产品源码提交：

```sh
git clone https://github.com/offercontext/offerPilot.git offerpilot-source-reading
cd offerpilot-source-reading
git checkout --detach c0a447bbe7be8976fe9a53c2bcbf3b91dad0eeac
uv sync --frozen --group dev --python 3.12
uv run --frozen python -m pytest -q tests/agent_loop/test_runner.py::test_write_tool_pauses_before_execution
```

后两条命令都在 `offerpilot-source-reading` 根目录运行。安装会创建该目录的 `.venv`，使用锁文件中的依赖；这一步需要下载依赖，但测试不需要配置模型密钥或启动前后端。预期看到 `1 passed`：写建议已产生，而 executor 仍未调用。

旧产品快照不包含后来增加的教程实验。做小实验时回到含 `docs/learn/examples/` 的教程 checkout；读源码测试时使用上述固定快照，不把两个目录的命令混用。各章的 `python -m pytest ...` 可在已激活的对应环境执行，也可加上这里的 `uv run --frozen` 前缀。

## 2026-10-08 的源码与截图验证

完成了上述源码与相关 ADR 的静态核对，并在对应源码基线上运行 **14 个现有测试，全部通过**。测试环境为 Python 3.12.14、pytest 9.1.1；使用已有开发依赖，通过仓库 pytest 配置加载当前 worktree 的 `src`，不是运行旧仓库的产品代码。

| 测试文件 | 本轮执行的节点 |
| --- | --- |
| `tests/agent_loop/test_runner.py` | `test_write_tool_pauses_before_execution`；`test_executes_multiple_read_only_tool_calls_from_one_assistant_turn`；`test_failed_first_read_does_not_block_second_read_in_same_turn`；`test_always_confirm_write_pauses_even_when_auto_approve_is_enabled` |
| 同上 | `test_cancellation_after_provider_response_drops_buffered_deltas`；`test_provider_failure_after_approval_does_not_repeat_origin_executor`；`test_same_model_call_fallback_reuses_one_frozen_provider_surface` |
| `tests/test_context_projector.py` | `test_projection_mandatory_overflow_fails_before_provider` |
| `tests/test_write_operations.py` | `test_locked_modify_rejects_patch_prepared_mismatch_before_executor`；`test_ordinary_executor_exception_terminalizes_without_rerun` |
| `tests/test_pilot_control.py` | `test_fence_rejects_a_lease_that_expired_before_commit` |
| `tests/test_agent_run_journal.py` | `test_active_work_budget_ignores_gap_between_recorder_calls` |
| `tests/pilot_runtime/test_execution_budget.py` | `test_runtime_budget_counts_agent_and_title_model_calls_together`；`test_runtime_budget_deadline_is_absolute_and_queue_wait_is_consumed` |

命令格式为 `python -m pytest -q 文件路径::测试名`，各章给出相应组合。运行前需安装本项目开发依赖；测试使用模拟模型、受控时钟或隔离临时数据，不需要真实模型密钥。

上述测试输出有一条 Starlette/httpx 测试客户端的弃用提示，没有失败。10 月 8 日补充截图时，前端构建通过，并通过内置浏览器走查了查询、确认、拒绝、追问、待确认页面刷新和显式停止，辅以只读数据库及接口核对。当次补图仅修改文档与素材，未重跑前述 14 项测试或完整发布门禁；未进行系统化模型质量评估、断网、服务重启或并发竞态实验。

### 补图后的逐章校对

2026-10-08 对全部 22 篇入门、6 篇进阶及其索引再次校对：逐章核对示例、论述、图注和参考资料，重新解析并渲染 23 张流程图，逐张检查 14 张截图及其原始选取字段。修正了教学示意与实际观察的范围说明、模型步与实际 Provider 请求的区别、默认预算的适用范围，以及结果核对和停止流程中的分支；同步修正图源和 SVG。

链接、源码定位、素材校验值和文档结构检查通过。此次校对仅修改文档与流程图，没有重新采集模型运行或重跑产品测试；前述实测缺口仍然成立，图文校对不替代读者试读或产品验收。

## 2026-10-09 的阅读路径与实验验证

本次修订保留 22 篇入门、6 篇进阶，默认路线改为 01→02→06→07；补入独立小实验，没有修改产品源码、依赖或原始截图。

- 在未安装 pip 和第三方包的全新 Python 3.12.14 虚拟环境中，实验的 10 项 unittest 通过；确认前、确认后、重复确认的日程数分别为 0→1→1。Ruff 检查通过，独立子代理复审意见已处理。
- 在另一个固定于 `c0a447bb` 的干净源码目录中，按上面的 `uv sync` 命令重新安装开发依赖。Python 3.12.13、pytest 9.1.1 下，入口的写前暂停测试通过；另跑多读取配对与普通执行异常不重跑两项，共 3 项通过。仍有 Starlette/httpx 弃用提示。
- 对进阶一伪代码注入“查询 A + 写入 B”的模拟返回，核对只保存选中调用、调用结果配对和 `provider_blocks` 保留。解析并重新渲染 23 张流程图，更新其中 4 张；逐张查看 14 张 JPG 原图，并核对原始素材校验值。

本次没有重跑全部 14 项历史测试、完整发布门禁或真实模型采集，未执行并发、重启和停止竞争实验；这些不是教学小实验覆盖的能力。作者校对与程序测试均不替代读者试读，读者试读仍未开展。

## 后续怎样维护素材

继续使用虚构投递和隔离数据，记录源码提交、运行配置、接口协议与完整日期。涉及确认的素材必须核对请求、确认内容及保存结果是否一致；更新时保留观察到的问题，不能只挑成功截图。

界面截图只能证明可见状态。执行次数、原操作身份或超时先后关系，需要配合真实测试或脱敏运行记录。没有现成诊断界面的地方，注明使用终端或日志输出，不制作仿真界面。

这一部分可以帮助读者沿实际工程查明机制。它不保证看完就能独立实现全部系统，也不要求把 OfferPilot 的每一项复杂设计移植到自己的项目。

[开始阅读：Agent Loop](01-agent-loop.md) · [返回学习总入口](../README.md)
