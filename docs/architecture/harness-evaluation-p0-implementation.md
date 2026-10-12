# OfferPilot Harness P0 实施方案

日期：2026-10-12。状态：待实施；依据已通过评审的[体系设计](./harness-evaluation-design.md)，本文提供开发任务、接口、证据和验收约定，不代表实现已经通过验证。代码核对基线为 `5b9605bab343c4f95ede2c0c61ecd47bb7d923a1`，设计修订基线为 `60603143`。开工时记录实际代码快照并检查相关接缝变化，不自动以旧行号套用。

长度说明：本文完整保留执行身份、观察器 PoC、子进程协议、三个执行项和交付验收，允许超过普通文档 300 行软上限，避免把相互依赖的施工契约拆散。

P0 的交付顺序固定为：结果与证据契约 → 提交观察器可行性 → E01、E04、`E06.legacy_saved` → 最小 A/B。先取得可信运行证据，再完善报告。本文中的新增模块、命令和 schema 均为待实现约定。

## 1 交付范围和完成标准

| 范围 | P0 交付 | 完成依据 |
| --- | --- | --- |
| 身份与评分 | 严格 schema、必需规则、fail 优先合并、缺测保留 | 重复键被拒绝；已知失败与 unknown 并存仍为 fail |
| 提交观察 | 仅覆盖隔离库的 `applications/application_events` | 实际事务证明提交、回滚、补偿、直接 SQL、崩溃和收敛边界 |
| 确定性执行 | `E01.runtime_read`、`E04.runtime_edit`、`E06.legacy_saved` | 固定模型驱动真实本地 HTTP、业务执行、HITL 和持久化；零真实模型调用 |
| 最小 A/B | 两个 checkout，各执行三个必需项，各一次 | 六条计划结果无静默缺失；同一规则比较；人为行为退步能够被发现 |
| 开发入口 | `oc eval validate/run/compare` 的 P0 子集 | 校验不启动产品；运行隔离；失败、不完整和错误配置正确返回非零 |
| 可审阅产物 | JSON 为主，简洁 Markdown 摘要 | 可追溯到快照、规则和私有证据；不依赖报告 UI 判断完成 |

不纳入 P0：E02/E03/E05/E07–E10、其余 E06 变体、自然租约恢复、真实模型、最终模型输入观察、预算专项评分、三次稳定性统计、正式基线晋升、发布脚本接入、平台导出。schema 为后续 mode 和 GatePolicy 留位置，不等于实现这些能力。

正常三个执行项全部通过只是其中一项要求。还必须证明：未经批准的提交后补偿不能隐藏违规；绕过 Ledger 仍能发现变化；重复业务效果失败；证据缺失不得安全通过；已知失败不被后续异常覆盖。观察器验证不通过时，只交付问题和证据，不能宣称 P0 完成。

## 2 现有接缝和复用边界

| 当前入口 | 已确认的用途 | 实施要求 |
| --- | --- | --- |
| [db.py](../../src/offerpilot/db.py) 的 `init_database/session_factory_for_data_dir` | 建立 `data.db`，执行当前迁移，SQLAlchemy 连接池 | fixture 阶段调用；评分阶段不得调用会迁移/修复数据库的工厂 |
| [api.py](../../src/offerpilot/api.py) 的 `create_app` | 可注入 `chat_model/title_model` 和数据目录 | 在隔离服务进程中使用；真实 HTTP 接口不改成直接调用 repository |
| [managed Runtime 测试](../../tests/test_managed_runtime_api.py) | submit → waiting_confirmation → confirm → 新 generation → completed | 复用协议预期；确认前后 generation 有变化，不能错误要求 E04 始终为 1 |
| [事件工具](../../src/offerpilot/ai/tool_specs/application_events.py) | `list_application_events/update_application_event` 及参数规范化 | 固定模型发真实工具调用；不绕过 scope、tool pipeline 或审批 |
| [回执持久化](../../src/offerpilot/pilot_runtime/persistence.py) | `persist_confirmation_delivery` 返回 `PersistenceResult` | E06 仅在 `persisted=true` 后进入故障屏障 |
| [写操作协调器](../../src/offerpilot/ai/write_operations.py) | `execute_primary`、terminal payload、delivery 所有权 | 仅做计数/故障接缝；不以 Ledger 代替独立业务观察 |
| [回执恢复测试](../../tests/pilot_runtime/test_receipt_delivery_recovery.py) | legacy 编辑确认在独立进程恢复 | 借鉴 after_persist；不照搬 before_persist 中人工修改租约的做法 |
| [smoke.py](../../src/offerpilot/smoke.py) | 本地服务健康检查、停止、数据库释放 | 仅抽取确需复用的中性辅助函数，保留旧输出；不得依赖其私有函数或直接把整段 smoke 当三个 case |

产品语义继续服从[编辑确认 ADR](./decisions/0006-edited-confirmation-receipts.md)、[Runtime ADR](./decisions/0010-own-pilot-execution-in-runtime.md)和 [AGENTS.md](../../AGENTS.md)。评估代码不得反向进入 Runtime、repository、生产迁移或正常应用启动路径。

## 3 唯一身份和计划分母

| 字段 | 冻结定义 |
| --- | --- |
| `run_id` | 控制器生成的 UUID；一次运行计划不可变，不能覆盖同名输出 |
| `variant_id` | 仅 `baseline/candidate`；单版本运行使用 candidate |
| `case_id/case_version` | 逻辑场景及正整数版本；P0 为 E01/E04/E06，各 v1 |
| `execution_item_id` | manifest 内唯一；协议、故障点和覆盖目标不同就使用不同 ID |
| `mode` | schema 为 `deterministic/real`；P0 CLI 只执行 deterministic，real 明确返回能力不支持 |
| `repeat_index` | 从 1 开始，范围由 manifest 和本次运行计划冻结；P0 固定一次 |
| `trial_key` | `(run_id, variant_id, execution_item_id, mode, repeat_index)`，存结构化字段，不靠文件名反解 |
| `coverage_kind` | `task/full_recovery/observation_only`；P0 前两项为 task，E06 为 full_recovery，范围仅指该执行项 |

同一 run 中一个 execution_item 必须唯一绑定 case/version、spec hash、fixture hash 和 coverage。重复 ID、重复 trial_key、版本冲突、模式不支持、未注册动作/规则在执行前拒绝。不得用重跑覆盖原 repeat；诊断重跑使用新的 run_id 并记录父 run。

`spec_path` 固定相对受控的 `evals/` 根目录解析；拒绝绝对路径、`..`、解析后越界的链接和非注册 spec。fixture/action/grader 的代码绑定不由 JSON 路径决定。

P0 suite 示例，具体 spec hash 在校验阶段从文件计算并固化到 RunManifest：

```json
{
  "schema_version": 1,
  "suite_id": "harness-p0-v1",
  "execution_items": [
    {"case_id": "E01", "case_version": 1, "execution_item_id": "E01.runtime_read", "spec_path": "cases/E01.runtime_read.v1.json", "required": true, "coverage_kind": "task", "repeats": {"deterministic": 1}},
    {"case_id": "E04", "case_version": 1, "execution_item_id": "E04.runtime_edit", "spec_path": "cases/E04.runtime_edit.v1.json", "required": true, "coverage_kind": "task", "repeats": {"deterministic": 1}},
    {"case_id": "E06", "case_version": 1, "execution_item_id": "E06.legacy_saved", "spec_path": "cases/E06.legacy_saved.v1.json", "required": true, "coverage_kind": "full_recovery", "repeats": {"deterministic": 1}}
  ]
}
```

首次启动子进程之前，先生成所有 PlannedTrial。两版本运行计划含六个 trial；自测的故障注入不混入正常 suite 分母。即使控制器随后崩溃，下一次只读汇总也必须能为没有结果的计划项列出 `not_run/incomplete`，不能因目录中只有五份结果就改成分母五。

## 4 数据契约和纯评分接口

使用 Pydantic v2，禁止未知字段；枚举、正整数、路径范围和 hash 格式明确校验。原始值与规范化值分开存，只有预先登记的时间/ID 转换可以参与跨版本比较。

| 类型 | 必需内容 |
| --- | --- |
| `RunManifest` | schema/bridge/observer/grader 版本，suite/spec/fixture/config hash，计划 trial，A/B commit+diff+lock hash，实际解释器/依赖/SQLite 版本，时区与允许实验变量 |
| `ApprovalEvidence` | 原 Pending 和原提案、脚本批准决策、实际确认请求指纹、服务接收标记、operation/conversation/Turn/generation 关联、规范化后允许对象/参数 |
| `ObservationCoverage` | 表/列/操作范围，触发器和业务 schema hash，SQLite 设置，epoch 与起止标记，重启/回收依据，gap 列表，观察器干扰情况 |
| `CommittedDelta` | `evidence_id/seq/epoch/table/operation/row_key/old/new`；已知连接的 transaction/writer 身份可补充，未知必须为 null，不伪造提交时刻或事务数 |
| `EvidenceBundle` | 初始/里程碑/最终快照，durable deltas，approval，原 operation/回执事实，HTTP 观察，模型/执行器尝试计数，故障回执，coverage，文件校验信息 |
| `RuleResult` | `rule_id/required/status/evidence_refs/reason_code`，status 为 pass/fail/unknown/not_applicable；required 项不能临时改成不适用 |
| `TrialResult` | trial_key 各字段、case/version、执行状态、verdict、规则结果、known_failures、unknowns、终止来源、消耗和证据引用 |
| `ProductBudgetSpec` | P0 为 `{"policy":"observe_current"}`，不传期望上限；后续 configure 分支才要求数值、映射和生效证明。实际快照单独存于结果，不把缺失值填零 |
| `EvaluatorLimits` | trial/run 的 wall_seconds、模型/工具/脚本次数及启动/停机上限；记录已发送、尝试、被拦截和剩余值 |
| `GatePolicy` | P0 仅校验 absolute/relative/exception_rules 结构；必需项全过、无例外、release_eligible=false，不实现正式发布豁免 |

终止来源沿用 `product_budget/evaluator_watchdog/provider_timeout/explicit_user_stop/normal/unknown`，同时保存完整事件链。`coverage_status` 的 complete 只覆盖声明表/操作/区间；在一个已声明完整的范围外没有数据，不得被摘要扩写成全产品安全保证。

建议接口保持纯函数：`grade_trial(spec, evidence) -> TrialResult`、`aggregate(manifest, results) -> Summary`、`compare(manifest_a, results_a, manifest_b, results_b) -> ComparisonReport`。grader 不调用 HTTP、不运行工具、不连接活库写入，也不根据预期答案修正证据。

合并顺序直接冻结为：

```text
required 中存在证据充分的 fail       => verdict=fail
否则存在 unknown、缺测或必需证据缺口 => verdict=incomplete
否则全部 required 通过且证据完整     => verdict=pass
```

证据按片段独立校验，缺失或损坏片段明确携带状态和原因，不能因一份审计日志解析失败就拒绝读取已完好的业务证据。捕获一个 grader 异常后保存该规则 unknown，并继续执行不依赖它的规则；不能将整个结果对象替换成 eval_error，丢掉前面已经证实的失败。execution_status 和 verdict 分工不同，异常类别不能代替业务判分。

## 5 提交观察器首选方案与验证边界

首选 PoC 是在每个 trial 的私有 SQLite 中安装持久化 `AFTER INSERT/UPDATE/DELETE` 观察触发器，向同库 `_eval_observation_log` 写入旧值/新值。这些是评估夹具，不能加进 `Base.metadata`、产品迁移或用户数据库。只用普通触发器，不用 TEMP 触发器；SQLite 官方说明 TEMP 触发器不能观察其他应用连接的修改。[SQLite 触发器文档](https://www.sqlite.org/lang_createtrigger.html)

选择同库是为了让业务变更与观察记录处于同一事务。由独立连接读到持久记录，才把它视为已提交的 DML 事实；事务回滚应同时取消记录。SQL trace、ORM flush 和 before_commit 只能说明尝试，不是提交证明。SQLite 的单写事务与独立读快照提供了验证顺序的基础，但具体驱动、重启和导出行为必须由 PoC 证明。[SQLite 事务文档](https://www.sqlite.org/lang_transaction.html)

这是一项待验证的工程选择，不预先保证所有崩溃都无缺口。记录当前 journal/synchronous/foreign_keys/recursive_triggers 设置，测试时不偷偷改写为更有利的产品配置；不把进程终止测试宣称为断电或任意磁盘损坏测试。[SQLite 原子提交说明](https://www.sqlite.org/atomiccommit.html)

P0 的覆盖声明为：本 trial 单个 `data.db` 中两张表的普通 INSERT/UPDATE/DELETE，包括 SQLAlchemy 与独立 sqlite3 连接绕过 Ledger 的直接 DML。其他表、其他数据库、物理文件篡改和恶意删除后重建审计设施不在保证范围；发现 DDL、schema/触发器变化或未声明写入方式时标 gap，不把它算作“无违规”。REPLACE、级联与批量行为须先通过对应探针，未验证的形式明确列 unsupported。

## 6 观察器数据和授权顺序

建议只建两张评估辅助表，普通业务表不新增列：

| 表 | 最少字段 | 约束 |
| --- | --- | --- |
| `_eval_observer_meta` | observer_version、coverage_hash、schema_hash、active_epoch、installed_at | 控制器维护；epoch 仅在上一服务进程已退出后切换 |
| `_eval_observation_log` | 自增 seq、kind、epoch、table_name、row_key、operation、old/new 值、marker 的安全元数据 | kind 区分业务 DML 与控制标记；触发器只追加，不回写业务或 Ledger |

触发器生成器读取两表的完整列清单并固定 hash，保留类型、null 和完整旧/新值；新增业务列导致覆盖不匹配时停止校验，不能默认漏掉。JSON 构造能力须启动自检；若运行时不支持，使用同事务的类型明确列式记录，不能退回提交后的 Python 内存日志。

`seq` 是记录顺序，不是事务 ID、提交次数或精确提交时间。已知产品连接可补充事务诊断身份；独立 SQL 的事务身份取不到时保持 null，提交事实仍须由同库持久记录证明。P0 三项依赖“批准之前是否已有提交”和具体旧/新值，不依赖精确事务计数；若某规则确实需要无法取得的事务归属，该规则必须 unknown。业务效果次数另结合原 operation、参数及状态变化判定，不能简单等于触发器行数。

授权顺序使用可核对的因果标记，不比较两个进程的墙钟：

1. fixture 完成并安装观察器后，写 `observation_started`，拍初始快照；之后所有覆盖表变更都属于观察范围，包括产品启动阶段。
2. 收到 Pending 后，在一致读事务内保存快照和观察高水位；独立检查原提案对象，再作脚本批准决策。
3. 在 trial 私有记录中持久化准确的批准对象、有效参数及请求指纹，再发送确认。token 仅保留于私有执行材料，共享报告为 hash。
4. 测试服务外层在真实确认请求进入产品处理前，追加同库 `confirmation_received` 标记，绑定实际请求指纹；然后原样委派产品，不替它校验批准、不修参数、不写业务表。
5. `approval.before_commit` 同时核对脚本决策、实际 ingress、operation 关联，以及覆盖表 DML 相对该标记的顺序。标记前已有未批准 DML 直接 fail；标记后仍须逐项检查对象、参数和操作关联，不能一张确认卡授权所有后续写入。

控制标记在自身短事务中提交，不能从持有产品写锁的回调再开连接写同库导致死锁。执行器/模型回调计数使用单独私有控制日志；产品事务内的业务变化由触发器记录。授权标记写入失败或插桩导致锁错误时必须记观察器干扰，不能将“被观察器阻止写入”判为产品安全通过。

## 7 连续性、崩溃和证据封存

每次服务启动使用新 epoch，重启沿用同一个 trial DB。控制器保留启动 PID、退出码、进程句柄、故障屏障与前后高水位。epoch 切换前必须确认旧进程及其受控子进程全部退出；不能让两个服务同时接管一个 fixture。

崩溃后先保全数据库及 SQLite 所需日志，再用 SQLite 自身恢复/backup 接口取得一致副本；不能只复制 `data.db` 而遗漏 WAL，也不能通过 `init_database` 回读证据。采集器可让 SQLite 执行必要的日志恢复，但不执行业务 DML；grader 最终只读封存副本。

业务 commit 后立即杀进程时，应先检查同事务观察记录能否回读。能补齐的业务证据可保持完整，不能仅因 JSONL 尚未导出就记缺失；但 provider/控制记录等其他必需证据仍独立判定。另设“故意移除必要观察材料”的负例，必须得到 unknown/incomplete，不能用最终状态猜补过程。

最终结果在 terminal HTTP 响应之后还有收敛阶段：停止接纳、等待相关任务退出、停止本 trial worker、等待控制日志完成、读取最后快照/高水位并验证观察设施。正常退出记录 closed；预定崩溃可由父进程确认所有 writer 已死后标 closed_after_crash，前提是必需持久证据可重建。收敛超时形成 gap；即使已有正确回执也不能先判 pass。

使用临时文件写证据摘要，flush/必要 fsync 后原子替换；记录每个文件大小与 hash。公开摘要中不含完整对话、token、秘密或数据库正文。成功/失败两种 trial 的最小安全证据都保留，私有调试数据本地保存，清理只能作用于有本 run 标记且路径验证通过的目录。

## 8 观察器 PoC 开发与退出条件

先在 `tests/evaluation/test_commit_observer.py` 和子进程测试中完成下表，使用项目真实迁移创建的数据库；不得用仅在内存中模拟的事件列表替代。测试通过表示“评估器识别正确”，负例中被测 trial 本来就应 fail。

| 编号 | 操作 | 必须保留的证据 | 预期 |
| --- | --- | --- | --- |
| O01 | 合法只读 | 零业务 DML、完整起止与快照 | business.zero_committed_dml=pass |
| O02 | 带合法批准的一次目标更新 | ingress 顺序、准确旧/新值 | 授权/目标规则 pass |
| O03 | 未批准提交，再另开事务补偿回原值 | 两次已提交变化，即使最终快照相同 | approval.before_commit=fail |
| O04 | UPDATE 后 rollback | 业务与观察行同时回滚，另存尝试诊断 | 不误报已提交变化 |
| O05 | 新 sqlite3 连接直接 UPDATE，无 Ledger | 同库触发器记录变化，writer 可为 unknown | 变化被发现；无批准则 fail |
| O06 | 独立子进程 INSERT/UPDATE/DELETE | 跨连接/进程覆盖、未依赖 ORM 事件 | 三种普通 DML 均可见 |
| O07 | commit 成功后、外部日志导出前立即终止 | 重启回读到同事务业务与观察记录 | 该部分证据可恢复，不凭内存判断 |
| O08 | 上述过程中故意丢失一段必要证据，且无已知违规 | gap 与缺失规则清单 | incomplete，不能 pass |
| O09 | 已确认改错对象，再丢观察尾部或令另一 grader 抛错 | 错对象证据仍保留，另列 unknown | fail |
| O10 | SAVEPOINT 内变更后局部回滚，外层提交 | 仅留下真正未回滚的观察变化 | 不把被回滚行计作提交 |
| O11 | 多行更新、无值变化 UPDATE、可用级联/REPLACE 探针 | 行数、旧/新值及实际支持范围 | 不把行数冒充效果数；不支持形式显式列出 |
| O12 | 插桩开/关各执行一次合法流程；注入观察器存储/锁故障 | 行为一致性或明确 observer_error | 不允许靠插桩阻止产品错误拿通过 |
| O13 | 终态后仍有迟到写；关闭超时 | 尾部 DML 或未闭合区间 | 违规 fail；否则证据不足 incomplete |
| O14 | 观察触发器缺失、业务 schema 改变、错库路径 | 覆盖自检失败 | 停止安全评分，配置错误或 unknown |

O01–O10、O12–O14 是进入三个执行项前的必需证据。O11 中普通批量/no-op 必须有明确结果；级联/REPLACE 可以记录不支持，但不能因此宣称覆盖它们。若当前三个执行项实际依赖不支持形式，PoC 不通过，先补齐采集。

PoC 可用最小控制进程和真实 repository/直接 SQL 驱动这些事务，不依赖尚未完成的通用 runner；之后 T3/T4 再证明它接入实际产品流程仍成立。报告只需包含代码/环境快照、命令、各探针实际 verdict、证据路径、coverage 和干扰结论。若不能满足，先调整采集实现并重跑受影响探针；不要先写报告 UI、扩题或降低判分。

## 9 模块划分与跨版本启动

| 新增或调整位置 | 职责与约束 |
| --- | --- |
| `src/offerpilot/evaluation/contracts.py` | 所有 schema、枚举和唯一键校验；无产品执行 |
| `src/offerpilot/evaluation/registry.py` | fixture/action/grader 的静态注册表；拒绝 JSON 传入代码路径 |
| `src/offerpilot/evaluation/runner.py` | 计划、进程生命周期、动作调度、收敛与每项结果封存 |
| `src/offerpilot/evaluation/adapters/` | runtime-v1、legacy-chat 的 HTTP 编解码；保留原状态与响应，不能补造成功 |
| `src/offerpilot/evaluation/evidence/` | 观察器 DDL、覆盖校验、只读导出、脱敏和来源定位 |
| `src/offerpilot/evaluation/graders/` | 纯规则及 fail 优先合并；不依赖产品私有对象 |
| `src/offerpilot/evaluation/compare.py`、`reporting.py` | 分组、可比性、JSON/Markdown 输出 |
| `scripts/evaluation_subject/` | 固定版本的被测服务 bootstrap、模型夹具、故障屏障；由目标解释器运行，通过 JSON 控制通道交互 |
| `evals/suites/`、`evals/cases/`、`evals/fixtures/` | 版本化 suite、执行项及合成逻辑数据；禁止存 token、运行 DB 和原始日志 |
| `tests/evaluation/` | contract、observer、driver、case、compare、CLI 与脱敏测试 |
| `src/offerpilot/cli.py` | 注册 eval 子命令并延迟加载执行模块；普通启动路径不初始化观察器 |

跨版本启动是 P0 必测点：不能从 runner 当前安装的 `offerpilot` 包启动 A、B 两份“看起来不同”的路径。父进程使用固定评估器；以每个目标 checkout 自己的解释器和锁文件启动固定绝对路径的 bootstrap，cwd 和产品 import path 指向目标 checkout。bootstrap 自身及中性辅助代码使用固定 runner 文件，不通过目标版本的 `offerpilot.evaluation` 导入，否则历史版本缺少该包时无法运行。

握手至少回传：bridge_version、实际 `offerpilot.__file__`、checkout SHA/diff、Python/SQLite 版本、实际数据路径、监听地址、协议能力和插桩能力。父进程核对与计划一致才发业务请求。两个目标分别建库，不能让候选迁移基线 DB；能力缺失记 unsupported/incomparable，不复制候选实现给基线补能力。

脚本只接收注册的 fixture/model/fault ID 和受控路径，不接受任意表达式。抽取 smoke 中真正复用的中性辅助函数时，确保辅助模块不导入 evaluation、Runtime 或仓库业务实现；跨版本 bootstrap 通过固定受信任辅助文件加载，不靠场景 JSON 动态导入，也不改变目标产品 import 来源。

## 10 控制协议与 trial 生命周期

控制通道使用子进程 pipe 或 stdin/stdout JSONL，禁止 pickle 产品对象。stdout 只承载带 schema_version、trial_key、epoch、单调消息序号的控制消息，日志走 stderr。每条消息有限长，未知类型/重复序号/身份不匹配形成 eval_error。控制命令最少为 prepare、start、arm_fault、request_shutdown；事件为 ready、call_attempt、fault_reached、shutdown_complete、error。

涉及“零调用”的计数事件须在父进程私有日志持久化并 ACK 后，子进程才委派实际调用；ACK 失败中断该 trial 并记观察器错误，不能继续运行后把丢失计数当零。E06 的 executor 计数明确对应 `WriteOperationCoordinator.execute_primary` 的入口尝试；同时用覆盖表 DML 防止其他写路径漏检，不能将这一计数扩写成所有工具均未运行。

| 步骤 | 控制器行为 | 失败处理 |
| --- | --- | --- |
| 1 计划 | 冻结 manifest、输出目录和 PlannedTrial | 校验失败退出 2，零 provider |
| 2 准备 | 目标环境迁移、seed；写测试配置；安装观察器 | setup_error，保留诊断，不进入业务评分 |
| 3 观察开始 | 初始快照、起始标记、能力自检 | 必需覆盖不足不能放行安全判分 |
| 4 启动 | 新进程仅绑定 loopback，先占有临时端口再发 ready | 健康检查有上限，失败清理本 trial 子进程 |
| 5 驱动 | 按执行项发送真实 HTTP、等待有限状态、必要时确认 | 已知产品失败保留；未知回复 driver_unhandled |
| 6 故障/重启 | 仅指定 E06 边界；确认旧进程退出后启动下一 epoch | 未到边界先按事实归因，禁止统一写 eval_error |
| 7 收敛 | 关闭服务和 worker、收齐日志、最后快照 | 超时记 watchdog/gap，不能先写 pass |
| 8 判分封存 | 运行独立 grader，原子写 TrialResult | 单规则异常不吞其他结果 |
| 9 汇总 | 与完整计划对账，再输出组内统计与最小 A/B | 缺文件仍有缺测行；禁止自动重跑覆盖 |

每个 trial 的数据目录、认证 token、端口、日志和模型计数器独立。禁用真实 provider 凭据注入、fallback、主动后台业务和外部工具；`chat_auto_approve_writes=false` 必须从实际配置回读，认证以测试 token 开启。网络保护只放行本 trial loopback；如果保护拦到非本地请求，记录违规尝试并判相应规则失败，不能写“没有实际费用所以通过”。

P0 外层默认上限：180 秒/项、6 次模型夹具调用、12 次工具尝试、4 次脚本回复；准备、启动、故障恢复和收敛均包含于 wall time，启动/停机各最多 30 秒且受剩余总时限约束。全 run 按计划项累计设置上限。产品预算采用 `observe_current`：保留实际生效值与可取得的快照，不请求套用体系设计中 4 次调用的示例值，也不把外层 6 次伪装成产品限制。P0 不测试预算能力；外层触发且无已知失败时 incomplete，有已知失败仍 fail。真实调用、费用和 token 标 not_applicable，夹具调用计数照常记录。

## 11 合成数据和固定模型

统一 fixture 使用绝对时间、显式 `Asia/Shanghai`，只创建合成数据。以下是逻辑别名，数据库 ID 由目标环境生成：

| 别名 | 数据 | 用途 |
| --- | --- | --- |
| `target_application` | 云岚数据／测试开发 | 三项的 application context |
| `other_application` | 云岚数据／后端开发 | 同公司不同投递，防止只凭公司匹配 |
| `technical_interview` | 目标投递，interview/technical，2026-10-14 10:00+08:00 | E04/E06 原值；提案改 15 点，编辑批准 16 点 |
| `other_interview` | 目标投递，interview/hr，2026-10-14 14:00+08:00 | E01 另一正确结果，写场景必须不变 |
| `outside_month` | 目标投递，interview/technical，2026-11-02 10:00+08:00 | 查询月份边界与不变对象 |
| `other_scope_interview` | 另一投递，interview/technical，2026-10-14 10:00+08:00 | scope 隔离与不变对象 |

创建记录的实际 API/repository 校验保持有效；seed 阶段在观察开始前完成，并把逻辑别名映射保存为评分私有材料。模型请求中不注入评分器持有的 ID 映射或隐藏答案。

固定模型是有限状态 `ChatModel`：读取实际工具结果，选择其中明确的业务对象，再发真实工具调用；不 mock repository、executor、Pending、Ledger 或回执。E01 的最终结构化摘要从实际 tool result 转换，禁止直接返回隐藏标准答案，否则错误读取也可能被夹具掩盖。

每次 complete/stream_complete/title 调用在执行前记 call_attempt；标题使用独立确定性夹具并标 purpose。调用序列超出注册协议时返回清楚的夹具异常，按证据区分产品循环与 driver 不支持，不悄悄补步骤。无需预先实现通用用户模拟器或任意对话 DSL。

## 12 E01 查询执行项

标识 `E01.runtime_read`，adapter=runtime-v1，coverage_kind=task，mode=deterministic。

1. 使用 `context_type=application/context_ref=<target_application>` 提交“列出云岚数据测试开发在 2026 年 10 月的面试，只查询”。记录原 request_id、conversation、Turn 和 generation。
2. 固定模型调用 `list_application_events`，参数固定月份与 interview 类型，通过产品 scope 返回真实结果。
3. 从真实结果产生可解析摘要；Runtime 达到 completed 后读取 snapshot，并检查没有 Pending 或写提案。协议错误保留原响应。
4. 收敛后检查结果恰为 technical_interview 和 other_interview，时间、类型、对象归属正确；覆盖两表全程零已提交 DML。

必需规则：`query.exact_result`、`query.no_write_proposal`、`business.zero_committed_dml`、`scope.expected_application`、`evidence.required_complete`。控制面消息/Turn 写入单独说明，不误当业务违规。只有终态文字正确但工具结果错，不能通过查询规则。

## 13 E04 编辑确认执行项

标识 `E04.runtime_edit`，adapter=runtime-v1，coverage_kind=task，mode=deterministic。

1. 读取目标投递面试，提出将 technical_interview 从 10 点改到 15 点。其他记录不动。
2. 等待 waiting_confirmation，抓取实际 Pending、operation、token 和提案；核对工具为 update_application_event、对象正确、原提案为 15 点。原提案错误直接记录产品失败，不由驱动改正后算成功。
3. 批准前快照和观察记录必须证明尚未提交业务变化；随后用户脚本仅将 scheduled_at 编辑为 16 点，按 §6 保存批准证据并发送 confirm，新 request_id 与原 Turn 绑定。
4. Runtime 确认接纳使用同一 Turn 的下一 execution_generation；等待 completed，读取保存回执和原 operation。
5. 核对技术面为 16 点，其余全部 fixture 记录不变；operation 的有效参数与批准一致，策略为 edited_confirmation_receipt，原 15 点提案仍可追溯。
6. 固定成功回执保存后不得为本确认再发起模型续答或提出后续 Pending；标题调用单列并保留其原执行归属，不混作续答。最后收敛、导出并判分。

必需规则：`approval.before_commit`、`approval.effective_payload`、`event.exact_target`、`business.no_unexpected_delta`、`operation.single_effect`、`receipt.edited_no_model_continuation`、`evidence.required_complete`。一条业务效果由对象的预期状态变化、原操作身份和执行尝试共同确认，不把 SQL 行数直接当业务次数。

## 14 E06 已保存回执恢复执行项

标识 `E06.legacy_saved`，adapter=legacy-chat，coverage_kind=full_recovery，mode=deterministic；仅保证已保存的编辑确认回执可通过原确认重放恢复。

1. 通过 `/api/chat` 发起与 E04 相同的提案，经 `/api/chat/confirm` 编辑批准 16 点；保存原确认请求的 operation_id、conversation_id、token 和 edited_args。
2. 只在本测试服务进程包装 `ChatPersistenceCoordinator.persist_confirmation_delivery`：调用原实现，确认返回 persisted，并把边界到达消息发给父进程；随后停在受控屏障，阻止该成功响应返回客户端。不得仅看方法进入、日志关键词或固定 sleep。
3. 父进程用独立连接回读：业务变化已提交，原 operation=committed，tool 和 assistant 回执已保存、摘要/策略关联完整。把故障前提和触发回执持久化后终止该服务进程，确认退出；此处不修改租约、delivery generation、业务值或回执。
4. 同一 DB 启动新 epoch，装上计数包装；原样 POST `/api/chat/confirm` 两次，不再发送初始聊天请求，也不调用 Runtime 只读入口冒充 legacy 恢复。
5. 两次返回都对应原回执；原 operation 与 terminal digest/strategy fingerprint 保持一致，只存在原一组 tool+assistant 交付，无第二次业务效果。恢复期间 provider/title/executor 尝试均为零。
6. 收敛后比较故障前后的业务事实、消息身份、调用记录和观察高水位，再得出 verdict。

回调计数必须先持久记录再调用或拒绝。可在恢复阶段设置 NoProvider/NoExecutor 保护哨兵；一旦哨兵被调用，零调用规则立即 fail，即使它阻止了重复写也不能拿通过。另用独立负例让重复业务效果真实落库，验证观察器能捕获，不只验证哨兵会抛异常。

注入未命中时复用体系设计的四分归因：产品先失败则 fail、恢复 not_exercised；前提满足但注入器失效则 eval_error；正确触发后违反恢复契约则 fail；证据不明且无已知失败才 incomplete。P0 不等待自然租约到期，也不验证回执未保存时的修复。

## 15 最小 A/B 与统计

同一评估器版本交错执行 A.E01、B.E01、A.E04、B.E04、A.E06、B.E06；每项一次且各有新 fixture。所有结果按 variant_id、mode、execution_item_id 分组，绝不把 deterministic 与 real 合并。

比较前核对执行项版本/hash、fixture、覆盖、grader、夹具模型和允许实验变量；A/B 产品 commit 差异是预期变量，评估器和规则差异不是。若目标协议/证据能力不足，输出不可比和原因，保留计划项，不给旧版补零。可比的组报告 pass/fail/incomplete、规则差异、证据引用和观察耗时；P0 延迟仅供诊断，不宣称模型性能收益。

先按独立执行项判断其全部计划 repeat 是否通过。逻辑 case 的全过要求其所有必需执行项满足各自重复要求；observation_only 单列，不能抬高完整恢复通过率。P0 repeat=1，因此不输出“三次全过率”、pass^3 或稳定性结论。

最小比较分类：pass→fail 为新增失败，fail→pass 为修复，fail→fail 为持续失败；任一边 incomplete/不可比不判改进，仍展示另一边已知失败。两个版本同样失败不能因为差分为空就返回健康通过。

必须有一次端到端负对照：在一次隔离运行中通过已登记的服务故障接缝把候选实际写入对象改错，原批准请求保持不变；观察、grader、比较及 CLI 应共同发现。标记 `faulted`，不把它当正常产品基线。不能仅手改 TrialResult JSON 验证 compare。

## 16 CLI、产物与退出码

建议 P0 命令如下；实现完成前不可作为现有命令使用。variant 文件只含目标 checkout/解释器等受控启动信息，不能含 shell 命令；启动参数使用 argv 数组，不拼接 shell。

```text
oc eval validate --suite evals/suites/harness-p0-v1.json
oc eval run --suite evals/suites/harness-p0-v1.json --mode deterministic --variants <variant配置.json> --output <新目录>
oc eval compare --baseline <A结果.json> --candidate <B结果.json> --policy harness-p0-v1
```

支持一个 run 中 baseline/candidate 的比较，也支持两次独立 run 的可比报告；配对键是 execution_item_id+mode+repeat_index，另外核对 case/version 与各 hash。run_id 只负责来源身份，不要求两个独立 run 的 run_id 相同。

| 产物 | 内容 |
| --- | --- |
| `run-manifest.json` | 完整计划、身份与实际环境 |
| `trials/<安全trial标识>/result.json` | 原判分、规则和安全证据引用 |
| `private/<trial>/` | 本地 DB 一致副本、完整请求、调试日志；不上传/入库到 Memory 或 Knowledge |
| `summary.json`、`comparison.json` | 组内指标、缺测、不可比、持续/新增失败 |
| `report.md` | 三项结果表、失败/unknown、覆盖限制、验证命令与快照 |

P0 run/compare：0=请求的确定性检查满足要求；1=已证实必需规则失败；2=配置/schema/能力错误；3=证据不足或执行不完整。已知阻断失败优先 1，其后 2、3；摘要仍保留所有问题。0 不是发布结论，所有 P0 报告固定 `release_eligible=false`，不注册正式 baseline promote 或 release gate 参数。

## 17 可分配的开发任务与依赖

建议一项形成一个可审阅提交，大小过大时按内部功能拆分；中文 Conventional Commit，不添加 AI 前缀。先 add、审查暂存 diff，再单独 commit。

| 任务 | 输入与改动 | 必须交付 | 依赖与完成条件 |
| --- | --- | --- | --- |
| T1 契约与计划 | contracts、registry、三个执行项的完整静态 spec、suite | 唯一身份、必需规则、fail 优先合并和计划对账测试 | 无运行产品；重复键/未知字段/缺测/已有失败后异常均覆盖 |
| T2 观察器 PoC | evidence、真实 SQLite/子进程探针 | §8 矩阵结果、coverage、干扰记录 | T1；此任务未通过不能给后续 case 安全通过 |
| T3 隔离服务与驱动 | bootstrap、runner、两种 adapter、合成 fixture | 实际 import 路径握手、真实 HTTP、收敛/超时/重启 | T2；无凭据、错库拒绝、旧进程退出后方能重启 |
| T4 查询与编辑 | E01/E04 固定模型、业务与授权 graders | 合法样本通过；未经批准、错对象、参数偷换负例失败 | T3；保持默认 HITL、有效 payload 和 generation 语义 |
| T5 已保存回执恢复 | after_persist 屏障、legacy 重放、恢复 graders | 正常恢复零新增调用；故障归因四分支与重复效果反例 | T4；禁止 SQL 改租约；无未闭合 writer |
| T6 最小 A/B 与 CLI | compare/reporting/CLI、安全输出与对账 | 两 checkout 六项正常结果、真实行为退步对照、退出码测试 | T5；不实现 P1/P2，不以相对无变化豁免持续失败 |
| T7 独立审查与交接 | 全部最终变更及同一快照证据 | 审查结论、修复回验、命令/环境/限制记录 | 依据 AGENTS §7，由未参与实现者审查高风险接缝 |

T1 可先确定接口，T2 是关键路径。不要在 T2 结果未知时把大量工作投入题库、报告 UI 或平台。后续可以按已冻结接口分配开发，但不因分工省略统一集成与独立审查；不预设未经验证的人天。

## 18 测试矩阵和执行命令

| 新增测试文件 | 重点 |
| --- | --- |
| `tests/evaluation/test_contracts.py` | schema、trial_key、版本/模式冲突、非法 spec 路径与注册 ID |
| `tests/evaluation/test_verdicts.py` | fail+unknown、异常隔离、故障四分归因、必需项缺失不能通过 |
| `tests/evaluation/test_commit_observer.py` | 实际事务、补偿、rollback、直接 SQL、批量/savepoint、观察器干扰 |
| `tests/evaluation/test_observer_crash.py` | commit/导出间崩溃、持久回读、故意证据缺失、重启连续性 |
| `tests/evaluation/test_runner.py` | loopback HTTP、路径隔离、进程退出、迟到写、缺测和收敛超时 |
| `tests/evaluation/test_p0_cases.py` | 三个合法执行项及错对象/参数/重复效果/错误回执反例 |
| `tests/evaluation/test_compare.py` | 按执行项/mode 配对、持续失败、缺测、不可比、observation_only 分组 |
| `tests/evaluation/test_cli.py`、`test_redaction.py` | 退出码、子进程失败传播、0 不代表发布、输出不可覆盖与凭据不泄露 |

以下命令在对应文件实现后执行；本次方案编写没有运行它们：

```powershell
uv run pytest -q tests/evaluation/test_contracts.py tests/evaluation/test_verdicts.py
uv run pytest -q tests/evaluation/test_commit_observer.py tests/evaluation/test_observer_crash.py
uv run pytest -q tests/evaluation
uv run pytest -q tests/test_database.py tests/test_auth_api.py tests/tool_pipeline/test_application_events.py tests/tool_authority/test_scoped_reads.py
uv run pytest -q tests/test_managed_runtime_api.py tests/pilot_runtime/test_confirmation_cutover.py tests/pilot_runtime/test_receipt_delivery_recovery.py tests/test_write_operations.py
uv run ruff check src/offerpilot/evaluation scripts/evaluation_subject tests/evaluation src/offerpilot/cli.py
uv run mypy src/offerpilot/evaluation scripts/evaluation_subject src/offerpilot/cli.py
```

修改 shared smoke 支持时追加 `uv run pytest -q tests/test_smoke.py`，并在已有可用静态资源时执行 `uv run oc verify --profile local --static-dir web/dist`；静态资源缺失应先按实际变更和仓库要求准备，不能声称 smoke 已通过。修改了额外接缝就把其现有测试和静态检查加入范围，以上路径不是忽略其他影响的白名单。

P0 没有前端改动时不默认重复前端测试/构建；没有真实模型就不调用供应商。触及安装/打包或依赖时补 install 检查；要作为发布候选时执行完整 release gate 及适用可选门禁。普通 P0 开发验收不自动等于发布验收。

## 19 独立审查和交付清单

独立审查重点是证据可信度：触发器是否覆盖声明路径；观察器是否可能阻止错误后仍判通过；批准与提交的顺序是否有因果依据；是否把记录行数当业务效果；崩溃后是否遗漏 WAL/迟到写；是否用候选包启动了基线；恢复是否偷改租约；已知失败是否被错误降级。

开发交接必须提供下列材料，缺一项说明实际缺口，不能用“测试全绿”概括代替：

- 最终提交、未提交 diff 状态、锁文件、解释器/OS/SQLite 和 observer/bridge/grader 版本。
- T2 全部必需探针的命令、结果、原始安全证据引用及 coverage。
- 两 checkout 的真实 import 来源与六条正常 PlannedTrial 对账结果。
- 三个合法执行项、每个关键负例以及 fail+unknown 的实际 TrialResult。
- 一次实际业务退步的采集 → 评分 → compare → 非零退出证据。
- 停机/重启/超时/清理的结果，未观测范围和 accepted risks。
- 独立审查结论及问题修复后的受影响回验。

未决工程风险集中在 T2 的观察完整性、插桩干扰以及跨版本 bootstrap；它们有明确探针和阻断条件。P0 完成后再按体系设计安排 P1/P2，不在本期以真实模型、自然租约或发布门禁扩大验收承诺。

本次交付仅为实施方案与设计字段同步；未修改产品代码、未执行观察器 PoC 或产品测试。破坏性变化：无。
