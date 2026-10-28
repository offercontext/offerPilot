# 固定六例模型验证控制器

此目录包含固定六例的数据与历史核验、独立 fake 演练，以及待部署验收的生产
`before` / `paid` / `send` 路径。**生产登记默认未配置，live 必须拒绝；不是已上线的收费入口。**
`templates/` 中的生产 workflow 是惰性部署模板，不在 `.github/workflows/` 下执行。
当前非 master 开发 CI 仍只运行无密钥测试和匿名公开 GET，不配置受保护环境或 provider key，
不向元数据适配器注入 token。
生产代码存在、合成测试通过、公开元数据读取成功，均不等于真实部署保护或模型语义效果已验收。

## 2026-10-28 迁移边界

原六次付费窗口已于 2026-10-28 00:00 UTC 过期，`before`、`paid`、`send`
仍按原截止拒绝；迁移批准不续期、不重置批次或预算。独立 `control-probe`
仅核平台元数据和本人审批，采用有限正数、前后不倒退的时钟及 120 秒读取窗口；
它不读取 provider 密钥、不产生付费准入、不消耗付费尝试。新付费批次须另行批准。

## 模块与信任边界

- `policy.py`：固定 H、六条原始请求 hash、manifest、batch key、预算与截止。
- `read_only.py`：固定仓库的受限 GET、H 证据及旧入口历史读取；不访问 provider。
- `admission.py`：外部生产 `Registration`、控制分支 tree、已登记入口历史、环境、本人批准及 TOCTOU 核验。
- `production.py`：固定 workflow 的运行环境桥接、handoff、checkout 与新鲜度校验、发送和安全证据归档。
- `rehearsal.py` / `execute.py`：独立 fake 路径；合成身份和批准不是生产登记。
- `templates/companion-ai-controller.yml`：默认匿名的惰性生产定义；开发 push workflow 不是生产定义。
- `templates/companion-ai-controller-actions-read.yml`：待单独批准的惰性临时 token profile，
  仅增加 actions:read 和三个元数据核验步骤的专用 token 映射，当前未部署。
- `templates/companion-ai-controller-review-dry-run.yml`：惰性的真实环境 Review 演练定义，未来安装到
  `.github/workflows/companion-ai-controller-dry-run.yml`；只有 platform-only job，不引用 provider secret 或元数据 token。

候选文件只作为受限 JSON 字节读取，不得导入候选代码、安装候选依赖、执行候选输出或共享其缓存。
付费路径仅使用已审控制器中的固定标准库 broker 和六条固定请求，不接受任意模型、URL、header、命令或路径输入。
单 owner 的保护不防 owner 恶意或账号失陷；环境分支规则不是 workflow allowlist，
允许分支上的所有 workflow 都属于受信代码。

## 离线验证与已取得的真实证据

```sh
uv sync --frozen
uv run python -m scripts.companion_ai_controller.test_gate
uv run ruff check scripts/companion_ai_controller scripts/companion_ai_validation
uv run mypy scripts/companion_ai_controller/*.py --follow-imports=silent --explicit-package-bases
```

完整 gate 包含原 helper suite、新控制器 suite 和平台契约测试，保存逐节点
setup/call/teardown aggregate 到 `artifacts/companion-controller-offline.json`。
skip、deselect、重复或遗漏节点均不算通过。fake 执行命令及产物以 `rehearsal --help` 为准。
新增生产代码必须在最终精确快照上重新跑 gate、静态检查和独立审查，不能继承旧提交的通过结论。
历史 workflow 回归读取固定 hash、非激活的 `.yml.txt` 资产，原断言保留；完整 gate 另必跑
`test_installed_profile.py`，独立核实际开发 dev/legacy 字节，或未来 control 目录恰为两文件且字节匹配已审模板。
历史 fixture 不是实际部署证明，installed profile 通过也不代替真实平台保护验收。

`read_only.py` 收集固定 H 的公开 GitHub 元数据，核 commit/tree、CI 身份、三项 job、
固定 artifact 和 2267 节点完整 aggregate。ZIP 需从正常支持的下载渠道取得并匹配固定 SHA；
无 ZIP 时明确不完整且退出 2。

```sh
uv run python -m scripts.companion_ai_controller.read_only \
  --offline-artifact scripts/companion_ai_controller/evidence/h-offline-12707161298.zip \
  --scan-legacy-history
```

旧链扫描检查全部分页、九个固定 run 锚点、所有 job 与四份历史 artifact 元数据；
缺失、过期、未知付费或分页不全均拒绝。此报告始终 `production_admission=false`、
`live_allowed=false`，不替代生产入口、环境及本人审批的完整准入。

本地工具演练：无凭据 CLI 直连失败并保留退出 2；连接工具支持固定 H 的
repo/commit/run/jobs 和 artifact 元数据，但其白名单不支持旧 workflow 全量 runs 路径。
不在该连接中换路径绕过限制或增添凭据。
独立 GitHub CI 在正常运行环境中另行执行固定公开 GET，不使用 token、不增加现有
contents:read 权限、不引用 environment 或 provider secret；401/403、限流或其他失败立即拒绝。

2026-10-27 的真实 [CI run 40450655710](https://github.com/offercontext/offerPilot/actions/runs/40450655710)
已核验固定 H ZIP、2267 节点 aggregate、旧入口全部 16 个 run 和四批账本（48 次／0.411934 元）。
该提交的完整无密钥 gate 为 **2452 项通过**；这不是后来新增生产代码的测试结果。
真实采集报告为该提交的 `companion-controller-public-read.json`。
这是当前连接工具不支持该路径，不是 GitHub 禁止读取公开运行历史。

## 一次性外部登记

上线时由本人登记两项非敏感仓库变量，当前没有替用户配置：

| 变量 | 含义 |
|---|---|
| `OFFERPILOT_COMPANION_AI_CONTROLLER_SHA` | 最终已审 control commit 完整 SHA；固定 workflow SHA、运行 SHA、control ref 和 checkout 必须一致。 |
| `OFFERPILOT_COMPANION_AI_REGISTRATION` | 实际平台身份、已登记历史锚点及本人迁移确认的严格 JSON。禁止包含 `controller_sha`，该值只能来自前一外部变量。 |

两项只在控制器升级或显式迁移维护时更新，不是逐批 H/R，也不预授权新数据。
v1 仅支持当前六例和 ordinal 4；不同数据或协议超出固定白名单就属于需重新审查的升级，
不是任意未来模型任务的通用入口，也不承诺未来永远无需修改登记变量。
`admission.Registration.from_mapping` 是生产 JSON 的权威 schema；`policy.fixture_registration()`
仅供测试，不得拿其合成 ID 部署。当前 JSON 必须精确包含以下字段，不接受未知字段或缺项：

- `workflow_id`、`environment_id`、`environment_name`、`branch_policy_id`：部署后读回的实际身份。
- `repository_id`、`control_ref`、`owner_id`、`actor_id`：当前仅允许仓库 `241031113`、
  `refs/heads/ai-validation-control`、owner/actor `112178718`。
- `dry_run_anchors`：已审演练记录列表；每项必需 `run_id`、`workflow_id`、`head_sha`、`workflow_blob_sha`，
  另按真实记录填写 `job_name`、`event`、`head_branch`。后三项有开发 push 兼容默认值，不得据此猜历史 job 名；
  `KNOWN_DRY_ANCHORS` 固定的两条旧记录必须原样保留。workflow 固定为 `397812462`，至少含
  `40447671170`、`40450655710` 两条开发 push。另支持 fixed controller SHA 上的 control 分支 dispatch，
  job 必须是 `Controller platform-only review`，必须实核已完成成功记录、workflow blob 和本人的环境批准。
  生产准入要求至少一条这种真实 control 演练；缺失时以 `PLATFORM_REHEARSAL_MISSING` 拒绝，不因名为 dry-run 就免费豁免。
- `other_workflow_ids`：本人迁移时登记的仓库 workflow catalog 身份，不是免费付费入口清单。
- `reader_mode`：显式填写 `anonymous` 或 `github_actions_read`。类的默认值为匿名；严格 JSON 仍要求该字段，
  后一种值只选择代码能力，不代表真实临时 token 权限已获批准。
- `owner_setup_attestation`：固定为 `owner-verified-lock-and-no-bypass`，表示本人实际核验完整保护。
- `owner_campaign_checkpoint`：固定为
  `owner-confirmed-registered-entrypoints-only-and-environment-only-key`，表示本人确认 campaign
  只经已登记入口使用、凭据仅在受保护环境；这是明确的人类信任根，不是扫描所有可能调用的机器证明。

未来 control commit 必须先清理为仅含生产与独立 dry-run 两个 workflow 的 tree，再完整 CI、独审并固定 SHA。
准入会机器核验该 tree，不可把现开发分支直接当 control 分支。
`other_workflow_ids` 只维持 catalog 完整性；其路径不得出现在 control tree。
新增/变化的入口或未知运行导致阻断，不能把 catalog 登记变成任意 workflow 的免计费豁免。
所有“完整历史”保证均限于本人 checkpoint 覆盖的 registered campaign；单 owner 仍负责该范围成立。

## 生产接口与批准前后复核

生产 workflow 调用 `python -m scripts.companion_ai_controller.production before|paid|send`；
`--evidence-dir` 仅用于 `send`，其值由固定 workflow 提供，没有 reader、transport、URL、key 或 fake-mode CLI 参数。
三项 dispatch 输入只能是当前候选 SHA、CI run ID 和 manifest digest，不能选择其他六例或任意程序。
独立 Review 演练另调用同模块的 `control-probe` phase：只用匿名公开 GET 核固定 control 身份、
两入口 tree、当前唯一 platform-only job、环境及本人批准，不产生 production admission，不读元数据/provider token、
不建立付费 claim 或调用 provider。报告明确 `mode=platform_only`、`live_allowed=false`、
`paid_started=false`、`campaign_attempt_consumed=false`；不是生产收费入口的可控 dry-run 开关。

- `before`：先检查登记、runtime 与干净 checkout，再读取完整已登记历史和环境；
  产生严格 schema 的 base64 handoff，经 `GITHUB_OUTPUT.before` 传给 paid job 的 `CONTROLLER_BEFORE`，不插入 shell。
- `paid`：批准后在新 runner 重读元数据，核本人批准、相同注册/代码/请求/历史 binding；
  只允许已知当前 job 生命周期变化，不允许审批期间出现新支出或身份漂移。
- `send`：再次完整执行 paid 核验、checkout 与新鲜度校验，并在 `RUNNER_TEMP` 独占持久 claim 后才读取唯一 provider key。
  claim 不可用于重启续跑；跨 run 的一次性仍由历史核验承担。

每次核验需在 120 秒内完成；paid 准入从本轮读取开始最多有效 120 秒，发送前再次检查。
不能把 120 秒解释为用户必须在 120 秒内 Review：before handoff 可等待至固定截止，但 paid 会完整重读。
进入发送仍必须剩完整 20 分钟；重新读取、重新 dispatch 或运行重试都不会延长截止。

只归档请求/响应 body、数字账本及 digest，不输出 HTTP 授权头、token 或环境值。
生产桥检查疑似凭据回显时停止公开 ZIP 发布，保留未知负债并交本人安全处理；
mask 不是安全边界，保留原始失败不意味着公开泄露敏感字节。
GitHub 环境获批后 secret 对 job 可用；仅发送 step 显式引用密钥是固定可信 YAML 的纪律，不是平台逐 step ACL。

## 元数据权限与匿名限流

当前开发 CI 保持匿名公开 GET 和 `contents: read`，未启用 `actions: read`，
不向元数据适配器注入或读取真实 token。
公开资源可无认证读取；GitHub 的匿名主限额为同一来源 IP 每小时 60 次。
按 16 条旧 run 测算、尚未计新增 control 演练锚点的基线约 before 44 次、paid 45 次 GET；
paid 和 send 两轮就约 90 次，加入平台演练锚点、历史或分页后还会增加，不能把匿名路径包装成可靠生产方案。

默认禁用的临时 `GITHUB_TOKEN` 只读适配器仅为代码与 fake 测试准备；
真实启用 `actions: read` 及显式注入临时 token 需要本人单独批准。
不自动查找环境 token、不新增 PAT 或长期凭据、不调用 secret/dispatch/写 API，
401/403、限流或请求范围不符都停止，不切换未授权通道。
具体 profile 为 `Registration.reader_mode=github_actions_read`：生产桥仅在该模式读取专用
`OFFERPILOT_COMPANION_METADATA_TOKEN`，显式传给 `verify_before` / `verify_paid(metadata_token=...)`；
`GitHubActionsReadReader` 本身不读环境，调用方只能给固定仓库受限 GET 注入 token。匿名模式拒绝显式 token，
不自动发现 `GITHUB_TOKEN`，不在认证失败后退回匿名；桥在核验结束后移除专用变量，避免传到 provider。
默认匿名模板不注入该变量、未加 actions:read；另有惰性的 actions-read 模板准备三个元数据核验步骤
从平台临时 `github.token` 显式映射，实际选用和部署仍须单独批准。开发 CI 不受该模板影响，
不能仅配 Registration 就宣称已获权。

完整 branch protection 仍需 `Administration: read`，公开 rulesets 也可能隐藏 bypass 配置。
本人需在上线/维护时核完整锁定和 no bypass，最终 Review 确认未变。
若要求每批机器证明全部隐藏设置，在不扩大权限的约束下仍是上线阻塞。
参见 [运行与审批 API](https://docs.github.com/en/rest/actions/workflow-runs)、
[环境 API](https://docs.github.com/en/rest/deployments/environments)、
[REST API 限流](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
和 [分支保护读取权限](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection)。

## 费用与时间没有重置

campaign 为 `one-shot-20261021`，ordinal 为 4：四批历史共 48 次、411934 micro-CNY，
总上限 20000000 micro-CNY，每次预留 3000000。第四批没有 `requests.jsonl`，保持真实缺失，不重建。
当前只允许六条固定请求，最多六次，不重试、不补第七次；通过结构检查不等于语义效果已证明。
截止仍为 **2026-10-28 00:00 UTC／北京时间 08:00**，开始前需剩完整 20 分钟。
这是保守 usage 账本，不是 provider 账单的独立硬限额。

batch key 绑定 campaign、ordinal 和六条 payload digest，不因 nonce、换 SHA 别名或 workflow 而改变。
任一真实 paid-started 消耗本批尝试；取消、缺失工件或未知费用阻断后续准入，
无法证明发送数时保留整批最坏 18 元负债。独立 fake 演练没有真实 paid job，
不提供输入可控的 dry-run 计费豁免。固定 concurrency 不拼批次，且不是事务账本。

## 仍待批准和验收的上线清单

1. **最终代码验证**：完成新增生产快照的 gate、静态检查、完整 metadata fake 端到端及独审。
   构建仅含生产/独立 dry-run 两入口的干净 control commit，对该精确 commit 完整 CI 后固定 SHA。
2. **master 登记**：另取明确 merge 授权；master 仅登记无密钥手动入口，实际 dispatch 选 control ref。
   惰性模板不能直接等同已部署入口，未知 ref/定义必须拒绝。
3. **旧入口停止**：经授权禁用旧付费 workflow，排空 waiting/queued/running，保留全部历史，
   由本人确认没有 registered campaign 之外的调用或未知负债。
4. **本人配置保护**：锁定 control，禁止强推/删除/绕过；环境只允许精确 control branch，
   唯一 reviewer 为本人且 no bypass。单 owner 不用 required PR review=1 制造无法维护的假双人审批。
5. **迁移登记初稿**：分支已锁定后，读回实际 workflow/environment/policy ID，审查 catalog 和 owner checkpoint，
   由本人先以保留旧开发锚点的 Registration 及固定 SHA 配置两个外部变量，供独立 control-probe 使用；
   此时生产准入因缺 control 演练记录仍拒绝。不猜 ID，不把 fixture 当注册，不扩大为 repository secret。
6. **元数据权限单独批准**：明确临时 token 仅固定 GitHub GET 与 `contents/actions: read`，
   启用前完成 fake 哨兵/泄露和权限负例。当前只批准适配器代码准备，不代表已批准或启用真实 token。
7. **先无密钥平台验收并完成登记**：部署独立 Review dry-run 模板，在已锁定 control SHA 上 dispatch，
   经本人真实环境 Review 后运行匿名 control-probe；配合拒绝 ref/tag 和锁定负例验收，不引用 provider secret、
   不运行真实 paid job。成功完成后，本人在同一迁移窗口把该 run 的精确 SHA/blob/job/event/ref 加入最终 Registration。
   追加外部登记不修改已锁定 control branch/SHA，不是逐批 H/R；生产随后实核该锚点和审批，演练不消耗本批。
8. **后本人配置密钥**：先断开旧路径的密钥权限；无密钥验收全过后才由本人配置/迁移环境密钥，助手不读取值。
9. **最后授权与 Review**：另行确认本批执行、费用和截止；旧 R 仍未激活，不因代码发布而自动替代或启动。
   当前连接没有 dispatch 写工具；未验证可用的已登录执行面时，仍是用户 Run workflow 加 Review 两步。

默认回滚是冻结，不自动复活旧 H/R；历史支出、未知负债和已消耗批次不回滚。
配置变量或迁移密钥不能代替以上代码与真实平台验收。
详见 [迁移架构记录](../../docs/architecture/decisions/0017-fixed-ai-controller-rehearsal.md)。
