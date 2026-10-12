# Harness 核心执行、比较与审核

日期：2026-10-13。实现说明；具体运行是否通过以冻结报告及独立审查为准。承接 [P1 方案](harness-evaluation-p1-implementation.md)和 [P2/P3/S5 方案](harness-evaluation-p2-p3-implementation.md)。P0 schema 1 入口保留。

## 1 核心与扩展

核心 suite 的分母为 19 个 deterministic、13 个 real 执行项。两版本三次分别为 114 和 78 trial；每次重新创建合成数据库、认证身份、配置和来源。排列固定为 repeat → item → variant。缺测计入完整分母，诊断重试创建新 run。

当前核心 spec 为 E01/E04/E06/E07/E08 v3，其余 v2。历史 spec、评估器与失败记录保留在对应冻结 worktree；旧证据必须使用原评估器回读。新版启动期限 90 秒涵盖 prepared 和 ready，普通 wall 360 秒、缺失恢复 600 秒；这些是评估器保护，产品实际预算独立回读，不能互相替代。

[扩展 suite](../../evals/suites/harness-extension-v1.json)独立包含 E11.memory_withdraw_scope v1。来自 fixture-control 授权范围曾过宽的实际缺口：准备两条 active Memory，撤回其中一条；其新来源单位必须消失，另一条保持原版本并继续进入最终序列化请求。扩展暂为观察集，不能混入核心 policy 或声称私有保留集。

`unapproved_compensation` 是实际负例：在合成 trial 首次 POST 前提交未授权业务写入，再独立提交补偿。最终数据库相同也必须 fail；观察器记录两次 committed DML。它只在显式 diagnostic variant 启用，不能用于正式基线或 release。

## 2 计划与运行

variants 为结构化 JSON 数组，每项提供 `variant_id`（candidate 或 baseline）、独立 checkout、interpreter 和已注册 fault（正式为 none）。两版本必须使用不同 checkout，实际导入文件、Git/diff、锁文件和运行时会再次校验。

```text
oc eval validate --suite evals/suites/harness-core-v1.json
oc eval plan --suite evals/suites/harness-core-v1.json --variants <variants.json> --mode deterministic --repeat 3 --output <新目录>
oc eval run --plan <目录/run-plan.json>
oc eval plan --suite evals/suites/harness-core-v1.json --variants <variants.json> --mode real --repeat 1 --provider-profile <profile.json> --output <新目录>
oc eval run --plan <目录/run-plan.json> --grant <grant.json>
```

plan 不启动产品或发模型请求。目录不可覆盖，建议使用 ignored `artifacts/harness/`；私有合成请求、回答、数据库和证据留在本地，不进 Git。

real profile 冻结 endpoint/model、temperature=0、thinking=disabled、输入字节/输出 token/尝试数/超时、禁用 fallback、CNY 峰值价格及出处时间。grant 精确绑定 profile/suite hash、variants、repeat、期限、最大请求和金额及已有人工授权引用。每次 HTTP send 前按实际请求字节和最大输出预留费用并 fsync；请求额度、费用或期限用尽会在 send ACK 前阻断。usage 缺失保留全部预留，不退款。grant 在 OS 当前账号的 `.codex/evaluation/grant-claims` 独占认领：Windows 从 [Known Folder Profile](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid)读取，POSIX 从 UID 账号库读取；改变 CODEX_HOME/HOME/USERPROFILE 不产生另一份额度。复制文件不能重用。这是同一 OS 用户的本地防重复消费，不是对该用户主动修改代码或删除授权收据的身份认证系统。

凭据仅从本地引用在子进程内解析。请求不得隐式重试、重定向或读取代理配置；stream 必须要求 include_usage。记录 requested alias 和实际返回 model、usage、调用身份与最终请求 hash；HTTP SDK 的 stream 标志不能替代业务 wire stream。缺身份/usage 是 unknown，不推测服务模型版本。

中性 bridge 对可信产品代码实施 Python 网络、进程和 SQLite 路径隔离；这不是针对恶意原生代码的 OS 沙箱。HITL、checkpoint、pending/confirm、provider_blocks 保持原产品契约。

E07 在模型屏障释放前从实际 cursor 建立重连，随后释放模型并接收同一 turn/generation 的实际数据帧；不能等回答完成再重连，因为产品有界缓冲可合法淘汰早期 cursor。重连使用 API 的 `after` 参数，并核对原 cursor 和新事件序号；订阅控制帧不能证明恢复。有界 subscriber 若发送 resync，保留该信号并 GET 原生 snapshot，核对同一 Turn/generation、有效 cursor 及覆盖已接收事件的高水位；不能重新 POST。确定性 E07 将正常回复均匀分为 300 段输出（短于 300 字符时逐字），在原产品 512 事件预算内覆盖 256 事件缓冲淘汰风险；[续流预检 suite](../../evals/suites/harness-core-stream-preflight-v1.json)单独运行此项，不替代完整分母。

外部检查用 `oc eval import-external --receipt <receipt.json> --output <新结果.json>` 零网络导入，类型为独立 ExternalCheckResult，不能合入 trial 分母或直接成为 release 证明。receipt schema 2 的字段为 kind=external-check-receipt、check_id UUID、check（oc-verify/knowledge-acceptance）、command 原 argv、exit_code、execution_complete、带时区 started_at/finished_at、mode、report {path,sha256}、report_capture（stdout/file）、code {checkout,commit,diff_hash,lock_hash,files_hash}、data_definition/data_files/data_manifest、model 和 metric_definitions。代码、报告及数据引用实际回读校验；Knowledge 的 --report 必须与报告引用一致，data_files 必须覆盖实际 --fixtures-dir（未指定时为该 checkout 的默认 fixture）完整目录及 manifest/queries。oc verify 缺原生数据标识时，额外 data_manifest 须是 schema 1、kind=external-data-snapshot，绑定相同 check_id/check/definition/files；无法捕获则数据快照 incomplete。真实模式的 model 须绑定 profile {path,sha256}/profile_hash、actual_models 与 evidence（含 id/model、非负 prompt_tokens/completion_tokens usage 的原供应商 JSON 响应）。不支持此证据形状的旧真实报告保持缺模型快照，不能推测 served version。

现有 oc verify stdout 文本及 Knowledge 安全 JSON 可回读；没有 --report 时须明确声明 stdout 捕获，file 捕获须绑定实际 --report。Knowledge metric 原值与定义分别保留。缺运行/数据/模型/定义证据为 incomplete，原非零退出或明确失败优先 fail；没有原文的安全报告，质量始终 unknown。旧工具没有统一的运行 receipt 时由维护者附加实际捕获的元数据，不能为缺失信息补“通过”。receipt 为本地 attestation，不能认证外部进程或作者；此 importer 不执行 command、不调用模型、不复制报告原文，不修改外部工具既有行为。快照读取清除 Git 环境路由，并禁用 external diff、textconv、fsmonitor 与所有注册的 clean/process/smudge filter；支持的信任范围仍是本地可信操作者，不承诺对并发恶意文件修改的 OS 隔离。

## 3 比较与 Gate

```text
oc eval compare --baseline <run目录> --candidate <run目录> --policy core-observe-v1 --output <compare.json>
oc eval gate --candidate <D-run目录> --policy evals/policies/core-initial-baseline-v3.json --purpose initial-baseline --mode enforce --review <审核.json> --output <gate.json>
oc eval gate --candidate <run目录> --baseline <批准基线run目录> --policy <release-policy.json> --purpose release --mode enforce --experiment <实验.json> --review <审核.json> --baseline-store <store目录> --baseline-id <id> --output <gate.json>
```

正式比较必须声明 ExperimentSpec。五类已注册变量为 product_snapshot、provider_model、prompt_or_tool_surface、dependency_set、context_policy；声明不豁免评估器、fixture、预算、模式和环境核对。展示 fixed、persistent/new failure、incomplete、incomparable 和波动，三次样本不宣称总体稳定性、可靠 p95 或单因素因果。

[initial 模板](../../evals/policies/core-initial-baseline-v3.json)和 [release 模板](../../evals/policies/core-release-v3.json)默认要求 19 项 D 每项三次并有真实独立审查引用。若发布范围要求 R，需另冻结覆盖完整 13 项 R 的 policy、人工 rubric、profile 和费用 grant；不能把 D 模板冒充真实质量验收。

exit 优先级为已知阻断失败 1、配置或不可比 2、不完整或待人工 3、满足 0。所有 eval/compare/gate 报告仍为 release_eligible=false。质量例外只对已完成的人工质量 fail、精确对象和有效期生效；安全失败、缺测、缺证据和待人工不得例外。

首次基线不需要 relative，但须绝对检查、证据和实际审查通过后由维护者明确批准。`oc eval baseline promote` 要求 run、gate、approval、id、store；本任务不自动生成批准或晋升。记录不可覆盖，promote/revoke 使用同一 OS 锁；有序 journal 为提交点，current 可恢复，撤销仅追加失效记录并保留历史。approval/review 是本地 attestation，不能认证操作者身份。

## 4 完整发布接线

两端 release 脚本新增显式 EvalSuite/Policy/Variants/Output/BaselineId/BaselineStore/Review/Experiment，Shell 为 `--eval-*`，PowerShell 为 `-Eval*`。要求 real 时另提供 provider profile/grant。EvalReuse 可复用完全匹配的已完成目录；EvalTimeoutSeconds 只能缩短冻结最大期限。

预检绑定候选实际 checkout、dirty diff、运行时、suite/policy、所有输入 hash、批准基线与独立审查，在付费前拒绝缺参数和漂移。保留原 pytest、ruff、mypy、前端测试/build、CLI/HTTP smoke 和 local verify；评估不能替代这些检查。

执行子进程由 ProcessTree 管理。超时关闭后代且非零，不退款。回执要求 enforce/release、完整保存报告 hash、两个 release_eligible=false，并回读原私有证据重评分核对完整语义；observe、initial、缺报告、伪 exit 0、统计篡改都不能通过。只在所有原检查及评估验收满足后，完整脚本可报告本地 gate passed；这不授权部署。

## 5 人工评审与条件裁判

```text
oc eval review-pack --run <real-run目录> --output <新评审目录> --audit-output <独立维护者目录/audit.json>
oc eval judge-preflight --run <real-run目录> --review <真实人工记录.json>
```

review-pack 使用原冻结评估器重新评分，随机展示样本并隐藏 A/B 标签，audit 必须保存到评审目录之外；交给评审人的仅是评审目录，维护者负责 audit 的本地访问权限。包含原请求、最终回答、实际选中来源和四维 0–3 rubric：针对性、依据性、可行动性、交互负担。audit 保存 trial/evidence hash 和原 safety fail；人工结论追加保存，不能修改 raw TrialResult。仓库可见题目不声称秘密盲测。

judge-preflight 为零网络条件检查：至少 20 个不同、有效、证据 hash 绑定的人工样本，且包含正反例；重复、冲突、abstain、过期或引用损坏不能凑数。每项引用必须是 `kind=human-rubric-record` 的结构化 JSON，包含与 Adjudication 完全相同的 adjudication_id/run_id/trial_key/evidence_hash/rubric/reviewer/decision/reason/dimensions/created_at/expires_at，schema_version=1；通用文本文件不能充当标注。run_id 必须与 trial key 一致，ID 不得重复。本地 attestation 仍不能认证实际作者，维护者须核对人工来源。即使满足，仍须单独授权实际校准并记录误报/漏报，judge_release_enabled=false。当前没有真实人工校准集，不执行模型裁判。平台适配器仅在实际导出需求及授权成立后开发，本任务没有上传动作。

## 6 S5 选测

mapping schema 2 绑定 [固定安全身份](../../tests/selection-safety-v1.json)。真实 pytest node 与 Vitest 文件 catalog 回读时，安全 ID 缺失、重命名、无模块或降为 p1/p2 均失败封闭；修改 mapping、安全表或控制代码仍选全集。小型选择器测试夹具保留 schema 1；真实 OfferPilot 仓库必须 schema 2。

首批候选仅 CalendarView.theme p2、TopBar p1、application_status p1。直接/关联测试仍选中，merge 仍包含全局 p0，未知路径/缺分类延续原保守规则。冻结影子样本和实际临时行为变异经独立审查后才认定细化验收；此处不承诺减少固定百分比。完整发布仍全量。
