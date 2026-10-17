# 固定 Windows EXE：一次性真实 AI 验证

## 状态与边界

本轮固定产品提交 `4844b591b18c13acb1633b63e883f2ba6d861956` 保留已完成 run boundary 的投影修复，并以确定性历史耗时均衡完整测试分片。新打包 run `38768464387` 已成功，完整门禁仍在运行；本次新包须独立完成非 AI、七场景免费 MOCK 与单 Pilot/Haru 恢复探针。旧 b0 的完整门禁因第 5 片总时限取消而失败；该包的非 AI、七场景 MOCK 和恢复探针通过只保留为历史证据，不替代本轮新包验收。

旧基线记录：产品 `9c245314e570f1d6de034e4d52deb46979874416`、installer `96c7517bc2a7674d897f324971edb2cbb56a9a9b23846c65af65293eb25c58e6`、run `38500952016` 完整门禁通过（6760 passed、13 允许的 skipped，前端 2657 passed）；旧同包非 AI run `38503131958` 为 183 PASS、7 BLOCKED、1 NOT RUN、4 N/A，旧 helper `f3122f4` 的免费 MOCK run `38503131977` 七场景通过。后续更严格的真实异步终态观察暴露 completed boundary 多余气泡，不能以旧 MOCK 覆盖新契约。旧 paid run `38694575612` 已结算 188 微元（CNY 0.000188），历史账本保留；下述一次性恢复只为这一个固定来源提供经审例外，其他非 skipped paid 历史仍触发 SESSION_ALREADY_CONSUMED。免费通过不授权重跑 live。

固定对象见 `desktop/real-ai-validation/product.json`：

- 产品/构建提交：`4844b591b18c13acb1633b63e883f2ba6d861956`
- 构建与完整门禁 run：`38768464387`；artifact：`11990663885`
- artifact digest：`sha256:555827695395f87011d1d98e746c102aa97b142fd8f5bec6db8a37f887406b60`
- installer SHA-256：`592ce2aa8baa4e8388fd1d7074b441a81e89a295d50b6da893753346e70b52f0`

最后一项是安装器哈希，不是安装后的主程序哈希。安装后逐文件比对固定安装器内 payload，另记录主程序哈希供启动前复查。来源比较统一使用共享 manifest 的原 6 个桌面模块和新增 6 个 updater 模块；broker snapshot 的产品提交、build run、artifact 与安装器哈希必须全部与 PIN 一致，任何旧/新混配拒绝写成有效证据。完整回归是独立门禁；PIN 绑定完整回归来源；LIVE preflight 额外核对该 run 及唯一“Full release regression (required for release)”和“Validation results (both jobs must pass)”job 均真实 completed/success，不重跑、不取消既有门禁，也不把 helper 报告当作独立发布认证。Playwright 使用现有 inspect fuse 和临时 loopback CDP；不修改 fuse。GitHub hosted Windows 是管理员/UAC受平台管理环境，不证明普通用户 UAC/SmartScreen、无开发工具的正常启动体验。

## 真实流量与费用边界

真实安装版 UI → 本机 loopback 预算代理 → 固定 `https://api.deepseek.com/chat/completions`，模型只允许 `deepseek-flash`。这是经代理的真实 provider 验证，不是直连。正常 HTTPS/TLS 校验保持开启；不修改防火墙、证书、OS 安全或产品代码。

换固定产品包不会重置 session、历史闩锁、累计预算或价格期限。2026-10-15 的 paid run `38694575612` 已终态 FAIL：连接成功，账本仅一笔连接请求 SETTLED、188 microCNY（0.000188 CNY），Pilot 无请求条目或拒绝计数，失败于 `LIVE_TERMINAL_OBSERVE / LIVE_COMPLETION_UNPROVEN`；清理通过。这不能证明 Pilot 已调用 provider，也不能据此归因于模型速度或余额。该账本范围不包括较早未加离线 guard 的 Python 测试尝试，其实际出站与费用仍无法确认，不能被后续通过记录消除。用户已确认本次账户现有 10 元并授权在该范围内推进；这里承接可核验的 188 微元，不虚称全历史零消耗，也不新增无限历史对账门槛。空 full-gate 来源仍会被 live 拒绝；当前安装器还须独立完整门禁成功、同一审阅 helper SHA 的新 Windows MOCK 成功，再满足既有人工配置与审批条件。

同一 session `offerpilot-fixed-exe-real-ai-20261008` 的累计上限仍为 **10 CNY、8 次 HTTP 出站请求、0 自动重试**，不是每个 workflow run 各有 10 元。旧 600 秒是单次执行保护，不是累计时长预算。本次恢复只允许 **新增一次 Pilot/Haru 请求、120 秒新执行窗口**；原连接请求 188 微元/1 次原样承接，开始时已知可用金额为 9,999,812 微元，新增请求上限为 1（比累计剩余 7 次更窄）。120 秒从 runner 的本次执行起计，包含启动、合成 seed、reload 与配置；arm 前必须仍有完整 75 秒观察余量，不足直接 BLOCKED，不自动扩时。上游请求仍最多 60 秒、每次观察读取仍最多 5 秒。清理必须完成后才能 PASS，清理时间不构成继续出站的许可。broker 先同步持久化每请求 3 元保守预留再发送。可信 usage 才结算；缺失/矛盾 usage、协议或账本异常封闭执行，未知费用保留 3,000,000 微元加旧 188 微元。

代理固定 endpoint/model、限制请求体和输出 token、拒绝未知参数/重定向/重试/fallback、在任何 HTTP 出站前同步持久化 reservation。每场景独立临时令牌，旧场景 SDK 迟到重试不能占用后续预算。运行时业务 repair/fallback 即使仍存在，也没有额外获准请求。报价依据和整数计价见 `broker-core.cjs`：2026-10-16 00:01 UTC 重新读取 [DeepSeek 中文官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)及[英文价格](https://api-docs.deepseek.com/quick_start/pricing/)（本次直接读取官方页面），`deepseek-flash` 对应 DeepSeek-V4.1-Flash，峰值 CNY/百万 tokens 仍为缓存命中输入 0.04、未命中输入 2、输出 8；闲时半价，峰时为 UTC 周一至周五 01:00–04:00、06:00–10:00（中国法定节假日除外），本代理始终按中文 CNY 峰价计费，英文 USD 价格不用于换汇核算。定价页仍列出 1M 上下文、384K 输出；00:03 UTC 直接读取的官方英文 [模型元数据](https://api-docs.deepseek.com/api/list-models/) 再次确认 1048576 / 393216 tokens；[API 契约](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/) 本次直接读取超时，检索快照（标注三周前抓取）仍列输出上限 393216。本地输出 cap 仍最多 8192，宽松费用上界 2.162688 CNY 小于每请求 3 CNY 预留。`PRICE_REVIEW` 的本地审阅窗口仅为 2026-10-16 UTC、截止 `2026-10-16T23:59:59.999Z`；供应商保留改价权，这不是供应商承诺的价格有效期。代码要求剩余窗口覆盖整个请求超时，跨日自动 BLOCKED；正式运行前仍须再次核验官方来源，涨价、模型契约变化、来源无法确认或窗口已过期时保持阻断，不能只延长日期。

broker、MOCK 的独立 VM 时钟和数值证据导出共用该审阅快照。导出必须逐项匹配 snapshot 的价格日期、截止、峰值依据、原 session 和 broker mode；旧窗口、混合窗口或缺失出处均拒绝，不能给旧账本贴新日期。价格复核不更新产品 pin、不刷新 10 元累计预算、不释放任何未知费用预留，也不更改 `20261008` 的 request/session/concurrency/secret 身份。新 helper 仍须独立审查和同 helper 的 Windows MOCK；本地伪 transport 测试不等于同包 live 验收。

工作流全局串行且不取消已开始工作。运行历史必须完整、run_number 连续；超过 100 条或分页/字段不全仍 fail closed。本次唯一例外固定为旧 run `38694575612`、artifact `11953258616`、ZIP SHA-256 `8a31c0a40981a543b85103217b7d3c2a5cd9ebeb080b43664a4c81bd9ad3e917`。只读 GitHub 元数据必须把 artifact 绑定到原 run/head、唯一失败 paid job 及成功上传步骤；只下载这个 ID，有限大小校验 ZIP 的两个 JSON、原账本和原 FAIL 结果，再赋予进程私有有效对象品牌。来源失效、过期、删除或不一致均阻断，仓库测试 fixture 不可代替正式下载。准备后再读取 owned ZIP 并核对当前 H/R/PIN 绑定；broker 在监听、发 token 和出站之前 fsync 原前缀。旧产品/价格出处保留在 carryForward，当前产品出处独立，不能重标旧记录。任何其他非明确 skipped 的 paid job 都永久阻断，包括这次恢复成功、失败或取消之后；不提供通用余额恢复、改 source ID 或换 session 的入口。新 H 必须经过同 H 免费验证，再由新精确空子提交 R 与用户本人环境审批启用。

## 凭据与证据

用户亲自在唯一受保护 GitHub Environment 中创建新的专用 `OFFERPILOT_REAL_AI_DEEPSEEK_20261008`。不要把 key 发到聊天、仓库、普通 repository/organization secret、日志或配置截图；工具不读取旧 key，也不代建 secret。工具不读取 Secret 管理接口，不能自动证明 `secrets.NAME` 的来源作用域；GitHub 可能同名回退到 repository/organization secret。用户必须亲自确认指定 Environment 已保存该 secret，且 repository/organization 不存在同名 secret。这是 paid 手动启用的必要条件，不能因环境 job 已有保护就省略。

GitHub hosted runner 和实际 AI step 会接触真 key，不能承诺 runner 绝对无法读取它。broker 在自己的进程内读取专用环境变量，之后从进程环境删除；EXE/backend 仅继承必要 Windows 环境白名单，不继承 provider/GitHub key、代理或 Node 调试注入。EXE 的 API 设置只保存一次性 loopback token。产品可能将该临时凭据写入本轮 `config.json`；不能宣称“绝不写 config”。不在真 key 所在 step 安装依赖。

只使用 hosted Windows 新建的真实 APPDATA profile；现有 profile 一律拒绝并保留。普通 finally 和 workflow always cleanup 只移除本轮持有的进程、安装目录和临时 profile；无法验证归属时不删。异常关机/强制取消可能来不及清理，依赖 hosted runner 销毁作为最后隔离；因此仍不得使用真实个人资料。

Live 禁用全部截图；MOCK 仅允许下述固定非配置页面截图。两种模式都禁用 HAR、trace、环境/header/config/prompt/response/原始日志采集，凭据页绝不截图。不上传 DB、profile、export、crash、安装路径或原始账本。Live 只上传两个明确结构的文件；MOCK 另有严格白名单 PNG：

- `result.json`：固定场景结果、固定 UI 子阶段与目标存在/唯一/可见布尔值、验收项、首要失败和独立 cleanup 状态
- `ledger.json`：固定出处、请求计数、整数费用/token、每场景固定状态；没有 prompt/response/key

MOCK 截图仅包含 Pilot 生成结果/停止/拒绝及对应 Haru、面试建议、简历预览、Offer 草稿，另可保存受控失败图。连接/配置场景禁止截图。截图前后都验证页面身份、凭据控件缺席、全部本轮临时 token 未出现；检查只返布尔，图片先留内存，通过后才独占写入固定文件名。拒绝或失败只记固定 skip code，不能用图掩盖业务失败。保留自然动画，不隐藏控件、不强行结束动画。Live 入口不能注入截图工厂，且其 artifact 白名单没有 PNG。

进程和 profile 的完整清理不证明外部 provider 停止计费。取消请求保留未知 usage 的保守费用。

## 实际 UI 范围

本次 LIVE 仅由安装版 UI 保存配置并发送一次 Pilot，核验主窗/Haru 严格自然完成；不再点击测试连接，不调用其他六场景。原七场景（连接、Pilot、HITL 拒绝、面试、简历、Offer、停止）只在完整免费 MOCK 中保留。主窗与 Haru 用真实状态、conversation ID 和可见 UI 同步核对，不合成发布状态。

本次 LIVE PASS 必须恰好新增一笔 Pilot 真实代理请求并通过 UI 结果；旧连接 PASS 仅为历史来源，不贴到新产品验收。报告使用专用 `PILOT_HARU_RECOVERY_PASSED` 或 `PILOT_HARU_RECOVERY_COMPLETION_ONLY`，后者明确未证明持续流式；其他六场景列为 NOT_RUN/OUTSIDE_RECOVERY_SCOPE。累计 ledger 保留原连接行和当前增量，`realProviderCalled` 仅由本次新增请求判定，未出站时不能因旧请求而变 true。以下完整免费场景的原约束继续保留：每次 PASS 同时需要恰好一笔对应场景的代理出站和 UI 结果。普通场景须可信结算。停止场景必须在 UI 点击前已有真实活动请求，并在 harness 强制取消之前观察产品断开；若模型太快完成、取消未传到 backend 或证据不全，不算取消通过。

只用同一个虚构公司/岗位/候选人。backend API 仅布置白名单合成记录和只读辅助证据，不直接调用 chat、面试、简历或 Offer AI API。简历通过合成 raw_text fixture 标为 upload 准备分类输入；不据此声称 PDF 上传/提取通过。HITL 永不自动批准；谈薪最终保存不提交。模型若没有给出合适建议或产生 repair/fallback，可能按真实结果 FAIL/BLOCKED，不改包绕过。

## 为什么没有 Run workflow 按钮

2026-10-08 只读核验：该仓库默认分支为 `master`，默认树不存在 `.github/workflows`。GitHub 的 [workflow_dispatch 默认分支约束](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch) 使新 feature-only workflow 不能假定直接显示 Run workflow。此方案不修改 master，不创建 PR/merge，不用 dispatch 绕过限制。

可用路线是原 feature 分支的一次显式 push 请求，经 secret-free preflight 和受保护环境人工批准。实际环境 API 可读性、GitHub skipped-job 历史表示、安装/UI 兼容性仍须真实预检验证；任一不符合就 BLOCKED，不能保证“设好 secret 即一定能跑”。

## 用户亲自设置

1. 先等独立安全审查及无 key 单测完成、受审 helper 发布至 `feat/20261005-windows-desktop-validation`。记下完整 helper SHA `H`，审阅该 SHA 下的 workflow、harness、broker 和 lockfile；不要只审阅可变 branch 名。必须先等待该完整 H 的 “Pinned EXE offline UI” Windows job 成功、原七场景及独立单 Pilot 恢复探针均通过，再继续配置凭据；fake Page 单测不替代这一环。
2. 在仓库 Settings → Environments 创建且仅配置 `offerpilot-real-ai-validation`。
3. Required reviewers 只选择已核验用户 `XiaoZheBrother`（ID `112178718`）。关闭管理员绕过保护的选项。Deployment branches and tags 选 Selected branches and tags，唯一规则为 branch `feat/20261005-windows-desktop-validation`，没有 wildcard/tag/其他分支。
4. 若请求 push 由本人发起且本人也是唯一 reviewer，Prevent self-review 必须关闭才能本人批准；这不免除逐次人工批准。若组织强制禁止 self-review，先安排另一已批准的触发身份或调整经审核方案，不能绕过。
5. 仅在该 Environment 的 Secrets 新建 `OFFERPILOT_REAL_AI_DEEPSEEK_20261008`，粘贴新的专用 key。建议 provider 端另加专用余额/费用限制作为纵深防护，不改已有 key 或账户安全配置。
6. Repository Settings → Secrets and variables → Actions → Variables 创建 `OFFERPILOT_AI_APPROVED_HELPER_SHA=H`。它是非secret变量，不含 key。
7. 准备尚未推送的一个空请求提交 `R`：唯一 parent 为 `H`，tree 与 `H` 完全一致，message 见下方。可由用户自己准备，也可在用户明确说配置就绪并授权后由负责人准备并回报 SHA；本工具准备阶段不创建 marker、不推送激活。
8. 设置非secret变量 `OFFERPILOT_AI_REQUEST_SHA=R`，再次检查 GitHub 上的 H 和待推送 R。仅在明确请求执行这一轮后，将 R 正常 push 到原 feature 分支，不 force push。
9. 在 Actions 中查看本次 R 的 “Pinned Windows real AI (manual approval only)” run。secret-free preflight 成功后，人工打开等待的环境批准，逐项核对 run SHA=R、helper SHA=H、固定 EXE、模型、累计 10 CNY/8 请求、继承 188 微元/1 请求、只新增一次 Pilot 的 120 秒窗口和新专用 key，才批准。
10. preflight 会读 [environment protection](https://docs.github.com/en/rest/deployments/environments#get-an-environment)、[branch policies](https://docs.github.com/en/rest/deployments/branch-policies#list-deployment-branch-policies) 和 [审批记录](https://docs.github.com/en/rest/actions/workflow-runs#get-the-review-history-for-a-workflow-run)。仅 `contents: read`/`actions: read`；无 admin/PAT。403/404/字段缺失都停止，不代改保护。
11. 完成后查看白名单报告（MOCK 另有已通过安全检查的图片）。不要点 Re-run jobs。由用户按自己的密钥管理流程撤销这枚专用 key，并清空两项激活变量；不把本工具成功当成发布验收。

请求 message 必须精确为（用完整 H 替换占位，此文本不是激活提交）：

```text
test: request OfferPilot real AI validation

OfferPilot-AI-Request: H
OfferPilot-AI-Budget-CNY: 10
OfferPilot-AI-Scope: pilot-haru-carry-forward-only
OfferPilot-AI-Carry-Run: 38694575612
OfferPilot-AI-Carry-Artifact: 11953258616
OfferPilot-AI-Carry-SHA256: 8a31c0a40981a543b85103217b7d3c2a5cd9ebeb080b43664a4c81bd9ad3e917
OfferPilot-AI-Additional-Requests: 1
OfferPilot-AI-Execution-Seconds: 120
```

普通代码 push 没有匹配的 R、marker 或保护环境，只会跳过 AI job，不能用 commit message 单独获得凭据。环境 approval 允许执行的是受审 H 的整个 helper；mutable branch 上恶意 workflow 本身可尝试改变规则，因此最终必须人工核对 exact commit 和 tree，代码内检查不能替代这一步。

## Windows 无 secret MOCK 前置验收

独立 `offline.mjs` 入口使用同一个固定安装器、真实 Electron/后端、同一套 UI 场景和清理代码；区别是合成 HTTPS transport、fake key、合成响应和模拟计价。`run.mjs` 固定导入生产 broker，没有环境变量或命令行切换到 MOCK，也没有可配置 upstream。

MOCK 工具在自身进程中封锁真实 HTTPS/TLS 和非 loopback TCP，生产 broker 的字节未改，单独 VM 时钟只让 broker 处于已审计费窗口；UI/EXE/合成事件日期仍使用当前系统时间。此处是工具的进程级网络拦截，不修改 Windows 防火墙/证书或 EXE。七场景合成响应仍经过真实应用处理、JSON 校验、真实 HITL 和 UI 点击。mock 不是 provider 行为、质量、费用或网络兼容性的证明。

本次免费入口的 Pilot 复用 live 的严格观察内核：同一真实 EXE 的 UI 提交、实际异步流与完整产品上下文，验证新会话、单次 admission 身份、自然 worker 结束、两窗终态正文及账本稳定；保留每次读取 5 秒和场景 75 秒的原期限。只允许 `offline.mjs` 使用独立工厂；工厂通过私有对象品牌核验仍有效、已封锁外网的真实 MOCK broker，复制 mode 字段无效，live 入口拒绝注入。MOCK 仍须原有至少两次可见流增量及同会话 running 同步才能 PASS；仅自然完成保持 BLOCKED，不能将 `COMPLETION_ONLY` 当作旧流式验收通过。失败停止后续场景并清理，成功后继续原七场景及 Haru 会话检查；免费产物始终标为 MOCK、`realProviderCalled: false`，检查名使用 MOCK 专用前缀。

失败诊断只增加固定原因/读阶段枚举、受限耗时和轮询计数、nullable admission/worker 状态及已存在 status GET 的 `budget.model_calls` 预留次数；后者不是 HTTP 出站计数。未观察保持 null，首要失败不被清理异常覆盖；没有增加请求、正文、标识符、URL、凭据或原始错误采集。这是 harness 可观测性与免费复现路径，不是已确定的产品根因修复；本地合成测试仍不替代 Windows EXE 实跑。原 paid 消耗、未知费用及除固定来源外的历史闩锁均不因新 helper 或免费结果而重置。

普通 push 首先只做无secret changed-path 路由。仅 `desktop/real-ai-validation/**`、本独立 workflow 或本文变化才启动 Windows MOCK；其他 helper 改动和空 activation 不重复启动它。合并提交、缺历史、非祖先 push、文件列表截断均 fail closed。顶层没有 paths 过滤，以免把空 activation 丢弃。

paid preflight 必须从真实 GitHub Actions 记录查到同一 H 的完整 successful push run、唯一 completed/success 的 “Pinned EXE offline UI” job，且 job/run/head SHA 全一致；不能用变量 `true`、别的 helper SHA、局部单测或旧截图代替。原七场景产物在 `mock-evidence/`，artifact 名 `fixed-exe-mock-evidence-*`，result/ledger 都显式 `mode: MOCK`、零真实 provider 证据和 `simulated-counters-only`；不会覆盖 live ledger。

原七场景及其清理成功后，Windows job 再次准备同一安装器和全新 profile，通过明确的 `offline.mjs prepare-recovery` / `run-recovery` 执行仅一次 Pilot 的严格观察。该入口只接受已封锁真实网络、私有品牌核验的 MOCK broker，继承的是明确 synthetic 的 188 微元/1 请求 fixture，不冒称官方 LIVE 来源；实际 fake HTTP 从 0 增至 1，不执行连接预热。120 秒包含启动与 seed，原 75 秒观察不变；JSON 只增加固定 120000ms window、UI 前耗时/剩余时间整数，方便确认余量，不足则 BLOCKED。该独立探针没有截图/会话扩展，产物在 `mock-recovery-evidence/`，artifact 为 `fixed-exe-mock-evidence-recovery-*`，始终 MOCK、realProviderCalled=false，不能覆盖原七场景或 live 的证据。当前仅本地免费测试完成，Windows 余量仍须这次 H 的实际探针确认。

本轮仅在 MOCK 中增加 Haru 会话保留检查：原 pilot-stream 自然完成并结算后，经真实 UI 切换亮暗、折叠及窗口恢复，核对两窗可见正文、同会话 ID 和各自草稿；这些动作前后代理账本必须不变。随后继续完成原七场景的剩余步骤。全部成功后，通过真实托盘退出、独立进程/端口消失检查，再启动同一 EXE 和 profile，从后端实际历史重新选择原会话，核对正文与主题。未发送草稿跨重启保存不在证明范围。新增截图只有固定的亮色、暗色和重启后 Pilot 三张，沿用凭据缺席、临时令牌排除和自然稳定检查；live 入口拒绝该扩展。

任何修复改变 H 都需要重新通过该 H 的 Windows MOCK。此模式也不允许自动变为 live，仍要后续专用 secret、精确 R/H、同轮预算闩锁和用户本人环境批准。

## 离线验证与发布隔离

```sh
npm ci --prefix desktop/real-ai-validation --ignore-scripts --no-audit --no-fund
npm test --prefix desktop/real-ai-validation
```

测试只用假的 key、假的 HTTPS provider 和 fake Page，不能使用真实 secret。Node 22 是 CI 目标；本地也检查 Node 24。禁止为了测试调用实际 `run.mjs`。

原 `desktop-windows.yml` 现仅为本工具新增三个精确 `paths-ignore`：`desktop/real-ai-validation/**`、`.github/workflows/desktop-real-ai.yml`、本文。产品、锁文件、原 workflow 自身和 release gate 路径仍触发默认完整门禁，dispatch 与原有 package-only 判定不变。

首次修改原 workflow 的迁移提交本身仍触发旧流程。早期迁移候选曾显式使用已存在的 `build: AI [windows-package-only] ` 前缀（末尾空格属于契约），允许额外 focused/build/packaging 和独立 Windows MOCK；不使用全 CI 跳过标记。该前缀不触发本工具 paid 入口：没有精确 R/H 和人工环境批准仍然跳过真实 AI。原 `validation-status` 仍按设计拒绝把 skipped full-regression 当成功，故 package-only run 不应被承诺整体绿色或发布就绪。旧 9c245314 已使用无此前缀的普通完整门禁且通过。本次分片均衡源码 4844b591 同样使用普通完整门禁与新打包 run 38768464387；打包已通过，完整门禁仍待终态，不能复用旧包验收或称发布就绪。

空激活提交是否被其他 path-filtered workflow 忽略按真实 GitHub run 验证，不预先声称不会触发。

## 首轮 MOCK 的已知诊断边界

[Windows MOCK run 37780561724](https://github.com/offercontext/offerPilot/actions/runs/37780561724) 已完成：同固定安装器下载/校验/安装成功，连接场景 `UI_TIMEOUT`，其余场景 BLOCKED，broker 发送/拒绝均为 0，清理通过，paid jobs 跳过。旧报告未记录 UI 子阶段，不能据此把问题归因于 provider。

真实 React/Ant 组件与 Playwright 原版 selector engine 的局部回归证实：带 tooltip 的“原生 JSON Schema”开关，其 accessible name 不等于纯 label，原 role-exact locator 匹配 0；改为 exact Form label 与 switch role 交集匹配唯一元素，且能实际切换状态。其他 input exact label 保留。该证据来自 jsdom，系统 Chromium sandbox 启动受环境 namespace 限制，未绕过；不是 Windows 重跑成功证明，也不证明该次超时只有一个原因。新增固定子阶段及安全 MOCK 图片用于下一轮定位。

[第二轮 Windows MOCK run 37784479723](https://github.com/offercontext/offerPilot/actions/runs/37784479723) 中，连接配置/测试已真实 PASS；Pilot stream 在 `PILOT_RUNNING/HARU_SYNC_FAILED` 失败。两笔合成 provider 请求正常 SETTLED、无拒绝，主窗和 Haru 的失败截图均显示完整同一 MOCK 回复及终态 idle，清理通过。图片证明最终可见同步，不证明此前 running 已被观察。

helper 原实现有确定的漏采窗口：发送点击返回后才轮询 running，又依次等待 Haru 标签和主窗 Stop；约六秒的合成 provider 帧序列不能替代实际 UI 状态采样。实际 React/controller/Haru hook 局部验证存在 `idle/null → running/null → running/正ID → idle/同ID`，故不放宽正 ID 要求。修复在发送前以 Haru 公共 `onState` 和两窗 DOM MutationObserver 被动锁存真正的 running/正 ID/可见运行控件，owner 用只读 getState 做关联核对；两次 getState 都来自同一 main-process snapshot，不能单独声称两份独立 UI 证据。只看见 idle、只有 bridge 或只有 DOM、ID改变/超时/失联均不得通过。

正常 stream 可在结束后读取已真实锁存的运行中证据，再独立要求 idle、最终身份一致和 Haru 实际正文；cancel 仍必须当下 Stop 可点、broker 请求仍 active，真实点击后由产品断连。没有修改 MOCK 速度、模型、预算、重试或产品。每场景 finally 注销订阅/观察器，诊断只保留固定布尔，不记录正文或会话 ID；此改动仍须新的 Windows MOCK 实跑确认。


[第三轮 Windows MOCK run 37789729958](https://github.com/offercontext/offerPilot/actions/runs/37789729958) 已实跑：connection、Pilot stream、HITL 拒绝均 PASS。stream 真正记录了正 ID running、两窗可见运行控件、终态同 ID idle 与 Haru 正文同步；HITL 经真实 UI 拒绝且无合成写入。三笔 MOCK 请求已结算、无拒绝，清理成功，paid jobs 均跳过。面试场景在 `INTERVIEW_OPEN` 找不到目标而超时，其余仍 BLOCKED，不能称七场景通过。成功截图仍受 guard 限制；唯一图片为拒绝后 Haru 的受控失败图，不能当作 stream 运行态图片。

本地真实 React/Ant + 锁定 Playwright selector engine 回归复现了辅助 seed 的缓存缺口：AppShell 已缓存空 sources，面试索引却含新事件，与 `events=[]` 对账后不渲染 primary 按钮。helper 改为 seed 后、任何配置/arm 前正常 reload 一次，核验该次导航后四个产品 GET（投递/事件/简历/Offer）各仅含本轮 fixture；不调用 query cache、重放业务任务或写入 UI 状态。此诊断是复现的缺陷与本轮证据一致，不宣称已从失败截图直接看到空缓存。

后续完整入口也经审查：面试卡先进入准备检查，必须通过真实简历选择与“开始准备”才显示提案；只读 JD 用 exact textbox accessible name（包装 label 的 text 包含 JD 正文，会使 getByLabel exact 匹配 0）。新增面试子阶段并等待实际 JD 载入。简历分类/取消、Offer 输入审核/生成与 Haru Stop 的真实组件 DOM 契约也已回归。每次供应商保存由产品 setQueryData/invalidateQueries 更新设置缓存，helper 仍逐次重填一次性 token 并核对 PUT 回执；没有为此重复发送测试请求。

MOCK 截图新增严格枚举拒绝原因，不记录 DOM/值/URL；受控面试失败图只额外允许已知面试列表或准备检查页，提案则允许实际路由所在的“投递”页。配置/credential/token 检查仍在所有 surface 判定之前，截图仍需内存捕获前后双检查。拒绝成功文案接受固定产品实际显示的“已取消这次操作”。仍须新 helper SHA 的 Windows MOCK 实跑，不自动激活 live。

真实组件 selector 局部测试（独立于假 Page 单测；不会启动 EXE 或 provider）：

```sh
npm ci --prefix web --ignore-scripts --no-audit --no-fund
node web/node_modules/vitest/vitest.mjs run --config desktop/real-ai-validation/test-ui/vitest.config.mjs
```

该测试使用既有 web 锁定依赖、jsdom 和 helper 的锁定 Playwright selector engine。仅为 jsdom 补足 CSS ID 转义，并使用 rc-util 正常的唯一 React ID 分支；不改变产品文件。它仍不能替代固定安装版 Windows 交互。


## 新包与旧 MOCK 证据分界

[第四轮旧包 MOCK run 37794298028](https://github.com/offercontext/offerPilot/actions/runs/37794298028) 保留为失败证据：连接 PASS；stream 的观察器健康，但只记录到 running/null 和最终 idle/正 ID，未记录正 ID running 组合；两笔合成请求正常结束、清理通过，paid jobs 均跳过。其两张终态图片不能补作运行中证据。

真实 controller/execution/Haru hooks 复现了持续 running 的恢复分支误报 idle，产品修复已进入 `590291ce`：将本地订阅状态与当前 durable execution 活动分开，精确终态结清并拒绝同 generation 旧 running 回退；保留 GET 恢复、停止、HITL 和跨会话隔离。短请求 accepted/terminal 同批次也可能合法省略中间 render，两种情况不混为一谈。此次仅 repin 到实际新包，没有放宽 UI 断言、伪造运行状态、拉长 MOCK 速度或增加模型请求。

新包原 Haru 桥和窗口安全属性保持不变，新增更新桥仅在 owner 暴露。当前发布策略为 `null`，未配置正式签名更新源；验收不会调用 check/download/install，启动不会自动加载 updater 的外网 adapter。CJS 源检查覆盖完整 12 模块，其他文件仍由安装 payload 整树校验。repo/module 字节审查、fake tests 和完整 gate 身份核验都不代替新包的 Windows MOCK，也不证明完整 gate 已成功。
