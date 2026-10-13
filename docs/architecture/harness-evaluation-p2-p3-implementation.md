# OfferPilot Harness P2 P3 比较门禁与扩展实施方案

日期：2026-10-12；实施状态更新于 2026-10-13。状态：P2、P3 条件执行能力和 S5 工程已实现，最终快照验收进行中；正式基线晋升待维护者批准，裁判校准待真实人工标注。设计基础为 `0dd52e2b` 的 [P0 与 S1–S4 交付](harness-p0-verification-20261012.md)。本方案承接 [P1 的核心场景、模式和证据契约](harness-evaluation-p1-implementation.md)，将结果变成可复核的版本比较、基线和发布检查，并安排扩展题库及选测细化。

P2 先实现重复统计和严格 policy，再做首次基线、两端 release gate 集成；P3 按实际失败扩展题库，标注和平台接入由明确需求触发。S5 是已有选测能力的保守细化，不重做 S1–S4，也不作为 P1 的前置条件。已实现命令和实际约束见[操作指南](harness-core-operations.md)。本文是设计契约；实施期间取得的真实调用授权另由 run grant 记录，不扩展为自动调度、上传、推送或部署授权。

## 1 已完成基础和剩余缺口

| 已有能力 | 可继承内容 | 仍需开发 |
| --- | --- | --- |
| P0 run/compare | 固定快照、计划对账、证据重新评分、known fail 优先、持续失败展示 | schema 2 多 repeat/mode、组内统计、精确不可比原因与实验变量 |
| P0 GatePolicy | 预留 absolute/relative/exception_rules，所有结果 release_eligible=false | 严格 typed policy、人工审核、首次晋升、质量例外、发布结果合并 |
| S1–S4 | 多来源 Git diff、真实 pytest/Vitest catalog、范围扩展、执行/报告/快照核对 | 按失效后果逐批区分 p1/p2；不能因实现了选择器就宣称测试更少 |
| 独立审查输入 | 本地 attestation 可绑定快照和 evidence_ref | 必须关联实际审查记录；approved:true 不能认证身份或替代审查 |
| 完整发布门禁 | 后端全集、静态、前端全集/构建、CLI/HTTP smoke、local verify 和可选门禁 | 显式新增 eval 参数、验证成功与失败传播；不将增量执行替换发布全集 |

P0 未执行的完整 release gate、后端全集、安装/Docker 及真实模型不能当作已通过基础。开发 P2 不要求机械重跑无关旧检查；首次把此分支作为发布候选时，必须在实际最终快照跑完整入口与适用附加项。

## 2 阶段与依赖

| 阶段 | 输入 | 必须交付 |
| --- | --- | --- |
| P2-A 重复与比较 | P1 schema 2 与稳定的 D 证据；R 仅在授权时运行 | 可变 repeat、组内统计、声明实验变量、可比性诊断和行为退步检测 |
| P2-B Policy 与基线 | P2-A、冻结 rubric 和审核格式 | typed absolute/relative、人工处置、临时质量例外、不可变基线晋升 |
| P2-C 发布集成 | P2-B、候选实际代码/配置、发布范围 | Shell/PowerShell 同语义，完整 gate + enforce eval；错误、缺测、超时不返回发布成功 |
| S5 选测细化 | 已完成 S1–S4、逐文件失效后果审查 | 保留直接/关联测试，逐批降低无关范围成本，真实仓库的选择与回归对照证据 |
| P3-A 扩展题库 | P1/P2 发现的具体失败及新业务范围 | 新 case、有效 fixture、合法/负例、明确证据和固定版本 |
| P3-B 标注与可选导出 | 稳定 rubric、多评审或历史趋势的实际需要 | 人工复核、裁判校准；按需要单独评估可替换的报告适配器 |

P2-A/B 可用确定性和合成历史报告开发，不等待模型费用批准；这些测试不能代替 real 质量证据。P3 安全反例发现后可以先补针对性回归，但不能把未完成的扩展集混入已冻结 core 分母。

## 3 重复计划与统计

RunManifest 先冻结每个 execution_item/mode 的 repeat 和必需性，默认核心比较每项三次。调试子集使用独立 suite，不以 CLI 临时过滤 core 后保留同一 suite_id。两版本交错顺序是 repeat → execution_item → variant，记录实际顺序、启动/结束和外部环境；并行真实 trial 暂缓，以免环境竞争影响因果判断。

P1 核心为十九 D、十三 R 执行项。三次、两版本时 D=114，R=78，同时运行=192 trial；单版本分别为 57/39。所有计算来自实际 manifest，不能继续使用“十个逻辑 case × 三次”的旧估算。预算分别求和，不能为凑次数临时扩费用额度。

每次重复重新建立 fixture、配置、数据库和认证身份，恢复仅在当前 trial 内复用 DB。诊断重试属于新 run，保留原失败；不能覆盖第 2 次失败后称“三次全过”。缺测仍在计划分母中；重复相同静态结果文件不算新一次实验。

| 输出 | 固定口径 |
| --- | --- |
| trial verdict | 沿用 fail 优先：已知必需失败 > 必需 unknown > 全部满足；保留所有维度 |
| execution_item 全过 | 该 item/mode 全部计划 repeat 均通过，分母不因供应商故障/缺测减少 |
| logical case 全过 | 所有必需执行项满足各自接受规则；不同 mode、完整恢复/只读观察分别列出 |
| 可判定率与证据完整率 | 分开计算；已知失败可以可判定而证据不全；报告原始分子分母 |
| 成本/延迟 | 各 item/mode 的样本、失败及在途/unknown 单列；报告逐 trial 值与中位数，不用三次样本宣称可靠 p95 或显著提升 |
| 波动 | 同一项重复间存在 pass/fail/unknown 差异；不凭一次改善下稳定性结论 |

三次是发现波动的最低工程策略，不是总体成功概率估计。扩大样本需先冻结新运行计划；成本/延迟或自由文本差异不足以归因时保留 manual_review，不通过临时删异常值获得结果。以后做置信区间时以逻辑 case 为分组单位保留相关性，不把同一题重复当新题。

## 4 可比性与实验变量

按 `(execution_item_id, mode, repeat_index)` 配对，另外核对 case/version、spec/fixture、规则、coverage、driver、observer/grader、provider、时区及预算。模式或故障协议不同不能配对；同一个 run 的 baseline/candidate 可比较，独立 run 必须证明可比条件；相同 candidate-only 产物不能冒充两次实验。

P0 的 lock/runtime 严格一致继续作为默认。新 `ExperimentSpec` 只允许注册变量：product_snapshot、provider_model、prompt_or_tool_surface、dependency_set、context_policy；每项记录 A/B 的声明值/hash 与实际观测值。一次研究优先只改变一个变量；多变量时标联合变化，不宣称单因素原因。

| 情况 | 处置 |
| --- | --- |
| 声明修改提示词或工具 schema | 允许相应 hash 不同，其他固定项仍校验；不得笼统要求全部输入 hash 相同 |
| 未声明 provider/fallback/预算/依赖变化 | incomparable，列出字段与实际差异，不自动扩大 allowed_variables |
| 同一 provider alias 的实际模型不明 | 保留 requested 与 actual=unknown；可做有限观察，但严格模型归因不成立，required identity 未满足则 gate incomplete |
| 新增 case 或改 grader/fixture | 新版本双方重跑；旧报告只作历史背景，不给基线补零或隐去候选新增覆盖 |
| 旧评估器快照不可用或原证据缺失 | 不以新代码替旧证据打分；保留原报告来源，正式比较不可用 |
| baseline 是 P0 bootstrap 参考 | 只证明桥接能力；正式质量对照需另选真实产品版本并登记理由 |

ComparisonReport 给出逐 repeat 分类和 item 汇总：passing、new_failure、fixed、persistent_failure、fluctuating、incomplete、incomparable。已有 raw verdict 不变。candidate 修复 baseline 的失败，应展示 fixed；正式候选绝对验收只判断候选，不能因历史 baseline 失败而永久阻断已修复版本。

P0 compare 目前任一侧已知失败即非零，这个保守诊断行为保留在 P0 policy。P2 不用裸 compare exit code 直接决定发布，新增 gate 对候选绝对条件和相对变化分别判定；若候选自身仍有同一失败，持续失败必须阻断。

## 5 严格 Policy 与人工结论

以 `evals/policies/<policy_id>.json` 保存非执行性配置，使用严格 Pydantic 类型；拒绝未知字段、空必需集合、不存在规则、无效阈值、未注册人工 rubric 和不可解释范围。文件不允许 shell、Python、任意 grader 路径或凭据。

| Policy 部分 | 必需字段和语义 |
| --- | --- |
| identity | policy_id/version、suite/hash、适用目的 release/initial_baseline、profile 与支持的格式版本 |
| absolute | 精确 required item/version/mode/rules/coverage/repeats；默认所有计划 repeat 的硬规则全过、必需证据齐全、人工项已完成 |
| relative | 基线身份/可比条件、新增失败规则；成本/延迟初始只 observe，阈值须经数据与业务要求形成新 policy 版本后才 enforce |
| review_rules | 必须人工判断的质量项、rubric/version、评审角色、证据、冲突解决及有效期 |
| exception_rules | 仅列可例外的 quality rule；允许范围、最大有效期、批准角色与复评要求，默认空 |
| provenance | policy 变更理由、审查引用、内容 hash；执行必须使用已冻结版本，不从运行结果反向放宽 |

安全规则不能与沟通质量加权平均；百分比成功率不能遮蔽某个必需对象误写。人工判断以 append-only Adjudication 记录 trial/evidence hash、rule_id、rubric/version、reviewer、决定及理由，生成新的派生 GateResult；不改 TrialResult 或抹除机器已知失败。证据变化使关联结论失效；评审冲突和必需人工项未处理均不能 pass。

本地 review_evidence 是 attestation，不是认证系统。先沿用真实人工/独立审查产物引用，由维护者核对来源；没有真实记录时拒绝 gate 通过，不能为了跑通写 approved:true。P2 不搭建账号、签名基础设施或暗中假定文件作者就是指定审批人。

质量例外精确绑定候选代码/数据、item/mode/rule、原因、负责人、到期和复评触发点；只有 policy 指定角色明确批准才生效。安全失败、缺测、证据不足、未完成必需人工项不可例外。gate 保留 active/expired/revoked 状态和原 fail；变更范围、到期或撤销后重新判定。已知问题链接本身不构成豁免。

## 6 GateResult 与首次基线

GateResult 分开保存 integrity、absolute、relative、human_review、exceptions、purpose、mode 和来源 hash。优先级为已知阻断失败 → 配置/不可比错误 → 不完整/待人工 → 满足；exit 1/2/3/0 与此对应，保留并存诊断。observe 的 0 仅表示观察契约满足，发布入口必须拒绝把它当 enforce 通过。

所有 eval 原始报告、compare、gate 均保持 `release_eligible=false`；gate 可表示 `gate_satisfied=true`，仍只代表评估部分。只有完整发布脚本汇总全部检查、enforce gate、独立审查和候选快照一致性后，发布报告才能表示本地发布验收满足。验收满足不自动授权部署或发布动作。

首次基线流程：选择候选 → 完成 required 运行与审核 → `purpose=initial_baseline` 的绝对/完整性验收 → 显式批准晋升。该 purpose 合法地没有 relative，不做与自身比较；不能用 initial_baseline 的 gate 代替 release purpose。后续基线晋升还要求与当前批准基线比较并符合策略。

`BaselineRecord` 只保存 baseline_id、创建时间、产品快照、suite/spec/policy/grader/fixture hash、provider/预算身份、不可变摘要位置/hash、gate 与批准记录、用途和限制。原始数据保留在本地受控证据目录，不进入 Git。基线不是产品发布，晋升不得自动提交、推送、上传或覆盖已有 record。

`promote` 采用独占创建和原子写，重复 ID 拒绝；并发晋升不能产生两个 current 指向或部分文件。current 指针更改有独立记录；撤销只追加失效标记，保留历史，不删除失败证据。只有摘要而无法回读必需私有证据时，基线可展示历史趋势，不能完成严格再评分比较。

## 7 命令与完整发布接线

以下为待开发接口，`--repeat` 仅用于 plan 生成并写入 frozen manifest，不能在 execute 时增减：

```text
oc eval plan --suite evals/suites/harness-core-v1.json --mode deterministic --repeat 3 --variants <variants.json> --output <新目录>
oc eval run --plan <run-plan.json>
oc eval compare --baseline <baseline目录> --candidate <candidate目录> --experiment <experiment.json> --policy <policy.json>
oc eval gate --candidate <candidate目录> --baseline <baseline目录> --policy <policy.json> --purpose release --mode enforce --review <审核记录.json> --output <gate.json>
oc eval gate --candidate <candidate目录> --policy <policy.json> --purpose initial-baseline --mode enforce --review <审核记录.json> --output <gate.json>
oc eval baseline promote --run <candidate目录> --gate <gate.json> --approval <批准记录.json> --id <新baseline_id>
```

CLI 的 initial-baseline 对应 JSON 的 purpose=initial_baseline。release-gate 新增显式 `--eval-suite/--eval-policy/--eval-variants`，PowerShell 对应 `-EvalSuite/-EvalPolicy/-EvalVariants`；baseline、review 和输出位置同样为结构化参数。若 policy 要求 real，必须另外提供匹配的 provider profile 和已授权 grant，缺失即在付费调用前报错。参数不含任意 shell，也不读取普通模型输出作为指令。

1. 执行前检查 policy/suite/variants/授权范围、候选 checkout 与当前发布 checkout 一致、输出新目录及所需环境；输出完整检查计划。
2. 保留原后端全集、ruff/mypy、前端全集和 build、CLI local-smoke、`oc verify --profile local`；Docker/install/real-ai 仍按发布范围与既有参数追加。
3. 用候选实际快照执行 eval，或校验完全一致的已完成报告；要求版本/配置/环境/计划/全部 hash 相同，不因 HEAD 一样忽略 dirty diff。付费结果复用也不能忽略模型身份、策略版本和有效期。
4. 核对 required repeat、真实结果/失败传播和审核，运行 enforce gate，再形成完整发布报告。缺报告、伪造 exit 0、子进程超时、来源变化及 observe 报告都阻断。
5. Shell 与 PowerShell 分别测试正常通过、各类非零、超时、缺参数/产物、旧报告、错误 variant、必须 real 但无 grant；实际 gate 还需在最终快照运行，不用模拟门禁代替。

增量选测只决定日常/合并验证范围。已有 `group_catalog` 只是收集/分组对账；P2 也不能把它当作后端全集执行证据。现有真实 AI verify 与 Harness real 报告分别保留，导入不去重成“同一次模型验收”；未来合并收费场景需另改明确契约。

## 8 S5 按风险细化测试优先级

目标是减少无关模块回归成本，同时保留直接与关联覆盖；不设置“必须减少多少测试”的指标。实现基线为 [selection-map](../../tests/selection-map.json)、[采集/选测核心](../../scripts/test_selection/core.py)与[执行器](../../scripts/test_selection/execution.py)。

| 步骤 | 产物与验收 |
| --- | --- |
| S5-01 盘点 | 当前 catalog 按模块/文件/风险/耗时列出；耗时只影响调度，不能决定 priority。统计真实 p0/p1/p2、unknown 和 fallback，不以未分类为零证明分类合理 |
| S5-02 固定安全基线 | 明确 auth/HITL/幂等/恢复/删除/并发/证据评分/门禁失败传播的最小必需测试身份；映射变化或测试重命名需同步审核，不能意外移出核心 |
| S5-03 小批细分 | 先选择无写入的展示/格式或独立业务边界，逐文件说明降级理由；同文件混有安全行为时保留最严重级，必要拆分仅为真实职责隔离 |
| S5-04 影子对照 | 在冻结变更样本上同时计算旧保守计划与新计划，列删减项及原因；实际临时行为变异验证直接 p2、依赖链和高风险负例仍被选中并失败 |
| S5-05 启用与维护 | 独立审查后启用新 mapping 版本；若发现漏选，扩大范围并补反例，保留问题记录。只回退选择策略版本，不回滚用户代码 |

直接/关联测试不按低 priority 排除，merge 仍含全局核心 p0。未知路径、缺分类、test deletion、map/collector/control 变化和高风险范围延续 S1–S4 的保守规则。每批变更执行选测自测、真实 catalog/组归属核对与该批相关回归；发布依旧全量。平台差异、缺依赖或可疑结果应 blocked，不能通过减少集合消除报错。

实际验证耗时按同一快照/依赖/平台比较，区分收集、执行和构建；首版近全 p0 的事实是安全起点，不是可据此自动批量标 p1/p2 的理由。S5 可在 P1-A 后安排，不等待 P3 平台能力。

## 9 P3 扩展题库与保留集

扩展目标约四十个逻辑 case 是容量方向，不是先填满编号的验收指标。优先采用实际失败、关键新能力和人工复核发现的反例；每批 suite 独立版本，core 不因扩展改变既有结论。

| 题族 | 优先覆盖 | 证据/接缝前提 |
| --- | --- | --- |
| 范围与权限 | 错会话/Turn、撤回来源、未认证、错误 capability | 当前认证与 scope 契约；不宣称多租户隔离 |
| 历史与 provider | 长历史、摘要失效、provider_blocks、允许/禁止 fallback、停止和迟到事件 | 最终输入/调用身份观察；保留 provider 内容和真实预算 |
| Knowledge/Memory | Note 版本失效、Evidence 矛盾、无证据拒答、偏好与事实分离 | 双通道及版本回读；不得启用 Brief 或自动知识写入 |
| 面试与 Readiness | 确认版本绑定、目标/简历变化、准备和复盘的失败恢复 | 先读当前面试 PRD/ADR；不把历史阶段禁令当现状，也不反向新增产品功能 |
| 投递/简历/Offer | 信息归属、编辑/删除安全、建议依据与数值一致性 | 对应业务表观察 profile、HITL 和明确数学/事实 oracle；不评判未经定义的“最佳选择” |
| 效率 | 无效工具循环、重复读取、超预算与多余澄清 | 固定任务成功前提和资源证据；不能以省调用换取少做任务 |

每条新 case 包含问题来源与授权、用户目标、fixture、允许动作/写入、预期业务事实、必需/质量规则、mode/coverage、成本上限、合法与负例和审查记录。缺少稳定 oracle 的题先进入候选观察集，不自动成为 gate；产品契约不清先协调，不在 grader 中臆定。

公开 core 用于开发，保留集只用于冻结版本的验收，由维护者单独控制可见性和更新记录。仓库可见 JSON 不能声称秘密盲测；改为私有存储仍需正常授权和访问控制。调参见过的题不继续标未见；保留集失败可以进入回归集，但应登记并补充未用于调参的新题。

真实用户素材仅在明确授权、去身份化和人工核对后转成 fixture；优先合成等价失败，不复制完整对话或生产 DB。失败证据的保留/删除由显式管理操作处理，本方案不创建自动清理任务。

## 10 人工标注、可选裁判与平台

先形成可复核 rubric：针对性、依据性、可行动性和交互负担分别评分，保留原句与支持证据。评审包隐藏 A/B 身份并随机显示顺序，但保留审计映射；争议由独立复核处理，不能投票抹去确定性安全违规。

模型裁判只有在至少二十个含正反例的人工样本上校准后才可试用。冻结模型/提示词/温度/输出 schema，报告逐项分歧、abstain、重复一致性和错误类型；验收门槛先由 rubric 风险与样本结果确定并版本化，不在看到候选结果后挑阈值。裁判不能替代数据库、授权、恢复和引用身份规则，不能自行批准质量例外。

裁判也是外部模型调用：单列授权、费用、数据范围及全部调用，不混入被测 Agent 的成本。裁判故障或证据不足为 unknown/abstain；待人工的 required 项不自动通过。没有明确裁判授权仍可完成所有人工流程和本地报告。

仅在多人评审/长期趋势的实际需求下，开展独立 Pydantic Evals 或单一平台适配 PoC；重新核对当时版本与依赖，不沿用旧调研的版本号当最新事实。产品 Python/依赖不因报表自动升级，仓库继续拥有 case/grader/policy；平台只是输出消费者。

适配验收使用同一脱敏 TrialResult 样本证明 pass/fail/unknown、缺测、人工状态、mode/coverage 和计划分母无损；断网/平台不可用仍保留本地报告且不改分。连接账号、上传/分享、权限和定期同步各自需要已有明确授权，不能由“设计平台接入”推导已获授权。

## 11 可分配开发任务

| 任务 | 必须交付 | 依赖与验收 |
| --- | --- | --- |
| P2-01 重复运行与统计 | frozen repeat、交错计划、分组分母、缺测/波动、资源明细 | P1-A；114 D 计划及 synthetic 缺测/重复键、真实子集重复隔离验证 |
| P2-02 比较与实验变量 | ExperimentSpec、精确不可比原因、fixed/persistent 分类 | P2-01；改 fixture/grader/模型/依赖的允许与禁止对照，不能给旧版补零 |
| P2-03 policy 与审核 | typed policy、Adjudication、质量例外、GateResult | P2-02；安全不可平均/豁免，候选修复可过绝对条件，持续失败仍阻断 |
| P2-04 基线管理 | 首次/后续晋升、唯一 ID、原子 current、撤销与审计 | P2-03；首次不自比较，冲突/并发/缺证据/过期批准被拒绝，不自动 Git/上传 |
| P2-05 两端发布接线 | 完整脚本参数、eval 运行/复用、enforce 合并与失败传播 | P2-04；Shell/PowerShell 正负门禁、超时、缺产物、旧快照、无授权检查 |
| P2-06 发布候选验收 | 最终快照完整本地 gate、适用可选门禁、独立审查与回验 | P2-05；未运行/受阻逐项列明，不以 P0 或选择器成功替代 |
| S5 优先级细化 | §8 五步逐批落地，分类依据与新旧计划/行为回归对照 | S1–S4 + P1-A；未证明不漏测前保留保守映射 |
| P3-01 首批扩展 | 从具体失败选题，版本化 suite、证据 profile、正负例 | P1/P2 的实际缺口；不以凑满四十题验收 |
| P3-02 人工与裁判 | rubric、盲化评审包、人工记录、可选裁判校准 | P3-01；先人工后裁判，required abstain 不过，费用独立授权 |
| P3-03 可选导出 | 一个实际需要的适配器及离线等价性结果 | 需求成立后；本地事实不依赖平台，不导出私有原文/凭据 |

任务编号不代表可以跳过依赖；S5、P3 条件任务分别报状态。每次提交只包含本任务变更，按仓库要求先 add、审暂存 diff，再单独使用中文 Conventional Commit。

## 12 必需反例与验证证据

| 编号 | 输入或故障 | 预期 |
| --- | --- | --- |
| G01 | 三 repeat 只有两份结果，或用同一结果覆盖第三次 | 保留计划三项，incomplete；重复身份拒绝 |
| G02 | baseline/candidate 同一必需规则均 fail | persistent_failure，候选 absolute 阻断 |
| G03 | baseline fail，候选所有必需项 pass 且可比 | fixed；不能仅因旧失败阻断候选，仍检查其他相对/人工条件 |
| G04 | 首次基线无旧基线；普通 release 却省略必需 baseline | 前者可按 initial_baseline 验收，后者不能借用该豁免 |
| G05 | 模型 alias/依赖/fixture/规则变化未声明或能力不匹配 | 不可比/unknown，不补零、不隐去新项 |
| G06 | safety fail、缺证据或未完成人工被填入例外 | 拒绝；合法 quality 例外仍保留原 fail，到期即失效 |
| G07 | 手写 approved:true 无审查记录、审核引用旧 evidence hash | 不通过必需审查；记录来源限制，不能认证不存在的评审 |
| G08 | observe exit 0、伪成功报告、超时/exit 1/2/3 | 两端完整 gate 均非成功，不吞子进程失败 |
| G09 | 并发晋升相同 ID、写入中断、current 冲突 | 唯一性/原子性成立，无部分晋升，历史不被覆盖 |
| G10 | 新映射少选直接 p2、遗漏共享依赖或删掉安全基线 | 选测验证失败，保留/扩大旧范围 |
| G11 | 实际隔离候选行为退步而报告形状合法 | 采集→评分→比较→gate→发布脚本均检出；不能只改 JSON |
| G12 | 平台/裁判不可用、保留集缺证据、真实费用授权不匹配 | 本地结果保留，required 项 incomplete，未授权零调用 |

新增测试建议在 `tests/evaluation/test_repeats.py`、`test_gate_policy.py`、`test_adjudication.py`、`test_baselines.py`、`test_release_integration.py`，扩展现有 compare/CLI/selection 测试。发布脚本测试用实际子进程模拟成功、失败、缺报告和超时，另执行真实完整 gate；文件名只是建议，验收以行为反例为准。

日常跑受影响 pytest、ruff/mypy 和选测/分组契约；触及前端则跑相关测试、构建和必要浏览器验收。候选完整验收入口保留：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1
```

```bash
bash scripts/release-gate.sh
```

实施 eval 参数后另验证上述两端追加参数的等价行为；Docker/install/real-ai 按 [AGENTS §7](../../AGENTS.md#7-验证与-code-review)适用范围追加。Windows 与 Linux 的依赖/平台缺口须实报，不能引用 P0 的 Linux 子集结果声称本期两端完整验收。

交接必须包含真实产品基线选择、所有计划与统计、冻结 policy、审核/例外/晋升记录、完整 gate 对应快照、独立审查及剩余缺口。当前风险在真实质量样本不足、传输/来源证据可比性、审核来源可信度与映射细化可能漏测；条件和反例通过前不得启用对应 enforce/缩减能力。本次仅设计，未运行新能力或发布门禁。破坏性变化：无。
