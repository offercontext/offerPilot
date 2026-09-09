# Harness P3B–P5 连续实施与交付计划

> 使用 writing-plans、subagent-driven-development、test-driven-development 和独立 CR 按阶段实施。
> 用户已授权推进至 P5 完成；真实模型与人工产品验收由用户另行安排。实施、可控测试与本地验证不等待阶段间确认。

**Goal:** 完成执行与订阅分离、四类有范围的上下文来源，以及用户显式开启的有界主动任务。

**Architecture:** 继续使用现有 SQLite、Pilot Runtime、P0 确定性回执、P1 展示、P2 Timeline 和 P3A 围栏。Runtime 管理执行；前端订阅事实；Contributor 通过受控来源加载参与统一预算；主动队列复用已有执行和写入授权。

**Tech Stack:** Python/FastAPI/SQLAlchemy/SQLite、React/TypeScript、pytest/Vitest。

## 1. 基线与顺序

- 已完成 P3A：`e0ce3383`，工作区干净。P3B 从此创建 `feat/20260909-runtime-reattach`，保留尚未合并的 P0–P3A 依赖。
- 原批准路线图：`2026-09-08-harness-delivery-roadmap.md`（原文档分支提交 `5b3bbb2`），本轮范围为 §9–11。
- 飞书主 PRD 回读 revision 772；当前 Knowledge 事实仍以 [knowledge-system.md](../../architecture/knowledge-system.md) 为准。
- 各阶段保存独立实施记录；P5 已分步提交，P3B/P4 与最终整合在本工作树收口提交。默认不推送、合并或发布，不修改远端文档。

## 2. 阶段清单

### P3B：执行与订阅分离

- [x] 增加显式版本化提交/读取/订阅协议；原接口保留现有断连行为。
- [x] Runtime 拥有初始执行及确认续跑；零订阅者、断线、重开均不重启任务。
- [x] 有限并发、等待队列、总 deadline、模型/工具调用预算；重连不重置。
- [x] 有界临时进度与可靠 Timeline 快照/增量；慢客户端不阻塞提交。
- [x] Pilot/Haru 使用新协议、恢复原任务、明确关闭窗口与停止区别。
- [x] 覆盖停止、权限/来源失效、服务退出、启动失败、旧客户端兼容，完成 CR 与本地整合提交。

### P4A：Readiness Signal

- [x] 仅注入当前投递/明确目标面试的用户确认 Signal 与有效 Evidence。
- [x] 按来源版本和撤回状态重新校验；禁止把准备重点升级为长期能力判断。
- [x] Contributor 独立开关、版本、预算、诊断和开关对照评估。

### P4B：Confirmed Memory

- [x] 核验并补齐明确的存储、确认、查看、编辑、撤回前置能力；不在 Contributor 内创建另一套领域真值。
- [x] 仅消费用户确认偏好；保留来源/版本、当前请求优先、删除和撤回即时失效。
- [x] 独立开关/预算及对照验证；Memory 不作为外部知识依据。

### P4C：Knowledge Context

- [x] Note 与 Evidence 两路独立召回，包括未进入 Note 的 Evidence；合并、去重、排序、引用回读。
- [x] 仅当前有效版本；检查归档、删除、来源更新和作用域，不引入 Brief 或自动 Wiki。
- [x] 接入受控 Loader 和统一 Projector 预算，验证开关对照及来源失效。

### P4D：Older Conversation Summary

- [x] 摘要保存明确消息覆盖范围、revision/digest、生成版本、来源和失效条件。
- [x] 区分用户陈述、可信业务事实和模型推断；原消息保留，摘要不能变成授权。
- [x] 有界生成、缓存、失败回退、原历史去重、费用预算，禁止投影中无界调用模型。

### P5：Haru 主动任务

- [x] 用户显式启用范围；确定性提醒与模型准备草稿分别配置，默认关闭。
- [x] 持久有界队列、原子认领、lease/attempt/重试期限与 Runtime Turn/generation 对齐。
- [x] 覆盖投递截止、面试准备、久未更新投递、复盘提醒及面试准备草稿。
- [x] 执行与发布前检查来源版本/撤回、去重和频率；未知先对账，不自动重跑。
- [x] 可见来源/触发原因/时间/状态；安静时段、时区、有界启动补查、来源级关闭和一键关闭。
- [x] 仅本地通知/草稿，不自动投递、外部发送或修改关键业务状态；业务写入仍走原 HITL。

## 3. 验证与收口

- 每阶段先运行能证明缺口的用例，再实现并运行受影响的契约、集成、故障和并发测试。
- 非平凡实现独立审查规格覆盖与代码质量；修复后复测，不用测试数量替代验收矩阵。
- 前端构建及可控浏览器走查由本任务负责；真实 Provider、人工质量评估及真实产品验收留交接清单。
- 最终本地自动 gate 包含后端、ruff、mypy、前端、构建和本地 HTTP 验证；既有失败需明确定位，不能冒称通过。
- 记录每阶段 commit、环境、命令、结果和未执行项。增量迁移，不删除用户数据，不绕过 HITL。

## 4. 当前验收记录（2026-09-09，实施与本地交付完成）

- 自动化环境：Windows 隔离 Python 3.12.13，前端依赖沿用已安装工作区；最终全量 pytest 使用临时安装的 xdist 8 workers，未将其写入产品依赖。整轮前后 499 个源码/配置 SHA256 一致。
- `ruff check .` 通过；`mypy src`：202 个源文件通过。`npm run build`（tsc + Vite）通过，保留既有大 chunk 提示。
- 全量前端首次运行：2,347 passed / 15 failed。13 项与本次 UI 接入有关的 fixture 已修正，定向 5 文件 30 项通过；停止和恢复标记相关另有 40 项通过。
- Runtime 提取边界门禁 67 项通过；保留未受控来源、跨源读取、注册与 ready 状态的负向 fixture，补充明确确认、独立 policy 与预算证明。
- 源码审计 133 项通过，单次生产源码审计 22.9 秒，保留原 30 秒阈值。优化仅在一次审计内复用同路径、同文本的只读 AST；不同 fixture 不共享缓存。
- Offer 对比 Drawer 静态契约失败在 `e0ce3383` 原基线独立复现，源文件和该测试相对基线无差异，本期不修改该业务模块。
- 可选来源临时不可用/真实 SQLite 独占锁/完整性错误/连接池恢复：4 项定向测试通过；缺表等未知错误不降级。Knowledge 当前版本完整性与可编辑性 10 项通过，Readiness 明确绑定/撤回/版本漂移 8 项通过。
- 既有 Runtime 主流程复核：`test_start_turn.py` 81 项通过（388.77 秒）、`test_confirmation_cutover.py` 38 项通过（927.56 秒）、`test_interview_preparation_api.py` 57 项通过（738.06 秒）。
- 旧 Chat 测试按 P3A 接纳契约修正：路由 spy 使用真实会话并保留接纳校验，4 项通过；Journal 故障对照只规范化随机 Turn/Request UUID，保留代次与业务字段比较，28 项通过（1319.88 秒）。新 POST 遇到待确认任务返回 409，GET 回读保留原 Pending、Journal 与消息，1 项通过（24.78 秒）。
- 确认超时交付修复：固定回执使用终态恢复 scope，旧 SSE 不再丢弃可信终态；传输模块 49 项通过（11.34 秒），同步/流式 CAS 与回执 6 项通过，停止/新执行围栏 2 项通过。旧 Journal clock 静态契约以当前文件重新加载验证通过。
- 旧 Chat fixture 独立 CR 后补强接纳调用次数、同步身份字段必填、Pending 参数与授权 token 不变断言，11 项通过（253.43 秒）。
- 旧确认流程在新 Python 3.12 环境追加复核：确定性批准、修改后批准、拒绝、失效与同键重试共 12 项通过（263.27 秒），没有修改生产确认行为。
- 新隔离 Python 3.12.13 环境：持久会话上下文与两类 Provider 错误响应 4 项通过（90.36 秒）。旧 Windows Store Python 3.11 全量 worker 发生一次 access violation，运行至约 55% 后停止，不记为通过。随后切换到 Python 3.12；最终整轮使用 8 workers，结果见 §5。未变更产品 Python 下限或锁文件以迁就验收环境。
- 最新完整 Projector 模块 93 项通过（107.42 秒），包含受控来源门禁与 v3 Manifest 持久化；最新 manager 类型检查通过。
- 懒启动固定工作池后的 manager + 真实 Runtime API + 接纳围栏整合回归：19 项通过（88.82 秒），包括部分线程启动失败无排队残留。
- 最新 Runtime 回归：`test_managed_runtime_api.py`、`test_harness_optional_api.py`、`test_pilot_turn_api.py`、`test_managed_execution.py`、`test_execution_budget.py` 共 29 项通过（338.94 秒）。
- 受控浏览器：已有投递对话提交后离开并重载，再打开能看到持久回复；新协议 start 与模型调用各 1 次。发现并修复了原会话 ID 遗漏与 Readiness 路由未注册。
- 受控浏览器：偏好创建与版本查看、较早对话整理、准备重点绑定/撤回、知识 Note 编辑新版本与归档均可操作；准备重点默认收起，展开后目标未选时不能确认。
- 受控浏览器：显式开启指定投递的主动提醒与准备草稿，分别生成本地结果；受控草稿调用 1 次，然后一键关闭。
- `bash scripts/local-smoke.sh 18765` 与 `oc verify --profile local --static-dir web/dist` 已通过，包含健康检查、SPA fallback、业务 CRUD、写入确认与重放；均使用隔离数据。
- 独立 CR 追加了来源删除后的读取/订阅围栏、数据库终态优先、确认操作所属 Turn 校验、启动时旧 epoch 对账，以及接纳前容量预留。新增接纳围栏 3 项通过（43.59 秒），覆盖启动失败收敛、容量占满时同请求重放、旧 epoch 围栏。独立复核后的接纳与协议故障 9 项通过（163.01 秒），包括同会话 A/B 驱逐恢复、错误 Turn 拒绝/终态重放无写入、已打开 SSE 来源失效后停止暴露内容。完整后端结果及后续复验见 §5；不以定向证据声称完整发布 gate 已通过。
- 真实 Provider、人工质量、Docker 和安装验收本轮未执行；真实验收按用户安排交由其他人。仅使用临时验收数据库，无用户数据删除、远端发布或自动业务写入。


## 5. 完整门禁与复验记录

- 最终冻结源码整轮：`pytest -n 8 --dist load -q --tb=short`，6,191 passed / 31 failed / 4 skipped，6,731.86 秒。4 项跳过均因 Windows 无创建符号链接权限，涉及 Knowledge ingest / reset 的符号链接保护，未算通过。
- 完整前端：`npm test -- --run`，2,373 passed / 3 failed。Git 子进程启动失败单独复验 6 项通过；源码审计单独复验 133 项通过，生产审计 18.755 秒，保留原 30 秒阈值。剩余 Drawer 静态门禁见下表。
- 标题、JSON 深度、旧接纳响应 fixture、Journal / Readiness 受控来源门禁及黄金样本 LF 收口：临时源码副本 226 项通过（108.96 秒），ruff 通过、mypy 202 个源文件通过，独立 CR 无阻塞问题。整轮结束后同步 8 个已审文件并核对逐文件字节一致，未在运行期间混入新实现。
- Windows PowerShell 诊断捕获修复：整组 9 项通过（15.13 秒），不再由读取线程吞掉非 UTF-8 错误字节。
- 旧接口待确认终态修复：同步、流式新用例先复现 Turn 被 completed 覆盖；改为控制器单一持久化后，接纳围栏完整模块 5 项通过（61.06 秒），旧 Pilot Turn API 完整模块 10 项通过（162.69 秒），保留终态首写失败后按原结果恢复的断言。
- 最新收口源码 `ruff check .` 通过；`mypy src` 202 个源文件通过，SQLite 错误码访问兼容项目 Python 下限。
- 最终聚焦独立 CR：无阻塞问题，复核了控制器单一终态、连接归池前恢复 busy_timeout、真实线程 Context 与旧 worker 写入围栏、新 generation 显式重试。慢 handler 四项串行通过；响应上限低于 handler 持锁等待时限，未放开迟到写入。
- 浏览器启动超时 / 迟到 CDP 响应两项在整轮中超时；串行复验 2 项通过（140.13 秒），保留原 180 秒时限。
- 黄金样本只补 `tool_metadata` / `tool_pipeline` 的 LF 属性并恢复换行；未更新样本字段、版本、哈希或历史权限清单。
- Readiness 门禁仅允许 Preparation 与正式 confirmed-readiness 两个已审消费者，保留第三方消费者和 Preparation 内部逃逸的负向 fixture。第二个消费者的当前调用位置已人工审查，尚未增加逐调用点静态锁定；其同快照、撤回、CAS 与漂移行为有 8 项运行时回归。

- 最后确认回归：Python 3.12 显式运行 `pytest -q tests/test_chat_api.py -k 'test_chat_confirm_fallback_timeout_before_handler_keeps_retry_claim or test_chat_confirm_timeout_after_write_returns_completed_fallback or test_chat_confirm_rejection_timeout_returns_recorded_fallback or test_chat_confirm_timeout_before_result_sink_keeps_pending'`，8 passed / 380 deselected（270.56 秒）。覆盖写入前超时保留重试、已提交写入及拒绝的固定回执、迟到结果不清空 Pending。慢 handler 四项在工作树 uv 环境串行通过（58.55 / 38.14 / 42.60 / 30.64 秒），连接归池三项通过（5.19 秒）；均使用同一收口代码，未冒称再次全量运行。

### 本期保留的既有门禁失败

| 检查 | 已核对原因与范围 |
| --- | --- |
| `test_application_jd_implementation_scope_is_machine_checked` | 缺少 release orchestrator 的 baseline / allowlist 两个外部 artifact；没有伪造新基线或扩大 allowlist。沿用 [既有交接记录](../../reports/2026-08-20-context-projector-release-verification.md) 的外部前置条件。 |
| `test_readme_references_five_wide_product_screenshots` | README 已使用产品手册新截图，测试仍要求旧路径；文件与 `e0ce3383` 一致，原基线整模块 1 failed / 1 passed。本期不改公开 README。 |
| `test_dependency_policy_v1_matches_read_only_canonical_golden` | 当前 26 工具清单误与冻结的 25 工具 v1 样本比较；现有 current 样本与实现一致。相关文件均未相对基线变化，不刷新历史安全 golden。 |
| `test_scoped_authority_source_gates_hold_across_production` | 基线 `list_application_index_scoped` 在 SQL 约束后过滤 outer-join NULL sentinel，被保守 post-filter 门禁拒绝；方法及门禁与基线一致，保留安全检查。 |
| `test_task12_has_no_name_based_or_reflective_classification` | 基线 `presentation_sources.py` 的 undo metadata 反射访问及 `application_preparation_access.py` 的固定工具名资格查询被门禁拒绝；均已存在于 `e0ce3383`，本期 API 无新增违规，不放宽该门禁。 |
| 前端 `workspaceDrilldown.test.ts` | Offer 对比组件含 Drawer，与该静态门禁冲突；源文件、测试与基线一致，独立复现，未修改该业务模块。 |

这些项意味着完整发布门禁尚未全绿；没有将基线失败视为通过。真实 Provider、人工质量、Docker / 安装验收未执行，按用户安排交由后续验收。数据库变化为增量新增；破坏性变化：无。未推送、合并、发布或修改远端文档。
