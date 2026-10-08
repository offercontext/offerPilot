# 固定 Windows EXE：一次性真实 AI 验证

## 状态与边界

本工具是待人工启用的独立验收器，不能据此声称真实 AI、Windows UI 或完整发布门禁已通过。产品源码、安装包和 EXE 不改动、不重建；普通 helper 发布不会注入 AI secret。当前只完成本地离线/假凭据测试；固定 EXE 的 Windows MOCK job 尚须发布后实跑确认。真实 AI 仍未获执行就绪证明。

固定对象见 `desktop/real-ai-validation/product.json`：

- 产品/构建提交：`16f31e477fd9882392ea8f754b6e2ef5ebdcf5c4`
- 构建 run：`37754883783`；artifact：`11540740222`
- artifact digest：`sha256:220bdcf1929e0a32c123664d2d268b02f1a2cc0f75de152bc9fed2820a90d3bc`
- installer SHA-256：`2e7b144ef657dcfa6e9408b532b59617c00f5a442081ff47ec18b75753713439`

最后一项是安装器哈希，不是安装后的主程序哈希。安装后逐文件比对固定安装器内 payload，另记录主程序哈希供启动前复查。完整回归是独立门禁；本任务不认证、不重跑、不取消它。Playwright 使用现有 inspect fuse 和临时 loopback CDP；不修改 fuse。GitHub hosted Windows 是管理员/UAC受平台管理环境，不证明普通用户 UAC/SmartScreen、无开发工具的正常启动体验。

## 真实流量与费用边界

真实安装版 UI → 本机 loopback 预算代理 → 固定 `https://api.deepseek.com/chat/completions`，模型只允许 `deepseek-flash`。这是经代理的真实 provider 验证，不是直连。正常 HTTPS/TLS 校验保持开启；不修改防火墙、证书、OS 安全或产品代码。

整轮 session `offerpilot-fixed-exe-real-ai-20261008` 总上限为 **10 CNY、8 次 HTTP 出站请求、600 秒、0 自动重试**。并非每个 workflow run 各有 10 元。七个场景分别最多一请求；取消场景最后执行。broker 先以保守峰值费用为每请求预留 3 元，再发送。可信 usage 才安全结算；缺失/矛盾 usage、协议或账本异常封闭 session，未知费用保留预留额。余额不足后续场景 BLOCKED。

代理固定 endpoint/model、限制请求体和输出 token、拒绝未知参数/重定向/重试/fallback、在任何 HTTP 出站前同步持久化 reservation。每场景独立临时令牌，旧场景 SDK 迟到重试不能占用后续预算。运行时业务 repair/fallback 即使仍存在，也没有额外获准请求。报价依据和整数计价见 `broker-core.cjs`，日期固定为 2026-10-08；代码只允许该 UTC 日期且剩余有效期覆盖整个请求超时窗口，超期自动 BLOCKED，须先复核价格并审查更新代码才能另行批准。人工批准前须复核 [DeepSeek 官方价格](https://api-docs.deepseek.com/quick_start/pricing)，涨价或模型契约变化先停，不直接运行旧账本上限。

工作流全局串行且不取消已开始工作。运行历史必须完整、run_number 连续。任何既往“Approved bounded real AI”job 非明确 skipped，即永久关闭本轮入口；换 request SHA、重新运行、前次结果未知、删除历史、读历史失败都不能重获 10 元。当前实现不支持续跑或自动恢复余额。确需修复后再次运行，必须先由负责人核验已花费及未知预留，计算本轮剩余额度，再单独取得新批准并审查新实现；不能简单换变量、marker、workflow 名或 session ID 重新给满额预算。

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

所有 AI 动作由安装版 UI 点击发起：保存 AI 配置并测试连接；Pilot 增量输出；提出写操作后的 HITL 拒绝；面试准备；简历分类预览并取消；Offer 谈薪草稿；Pilot 运行中停止。主窗与 Haru 用真实状态、conversation ID 和可见 UI 同步核对，不合成发布状态。

每次 PASS 同时需要恰好一笔对应场景的真实代理出站和 UI 结果。普通场景须可信结算。停止场景必须在 UI 点击前已有真实活动请求，并在 harness 强制取消之前观察产品断开；若模型太快完成、取消未传到 backend 或证据不全，不算取消通过。

只用同一个虚构公司/岗位/候选人。backend API 仅布置白名单合成记录和只读辅助证据，不直接调用 chat、面试、简历或 Offer AI API。简历通过合成 raw_text fixture 标为 upload 准备分类输入；不据此声称 PDF 上传/提取通过。HITL 永不自动批准；谈薪最终保存不提交。模型若没有给出合适建议或产生 repair/fallback，可能按真实结果 FAIL/BLOCKED，不改包绕过。

## 为什么没有 Run workflow 按钮

2026-10-08 只读核验：该仓库默认分支为 `master`，默认树不存在 `.github/workflows`。GitHub 的 [workflow_dispatch 默认分支约束](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch) 使新 feature-only workflow 不能假定直接显示 Run workflow。此方案不修改 master，不创建 PR/merge，不用 dispatch 绕过限制。

可用路线是原 feature 分支的一次显式 push 请求，经 secret-free preflight 和受保护环境人工批准。实际环境 API 可读性、GitHub skipped-job 历史表示、安装/UI 兼容性仍须真实预检验证；任一不符合就 BLOCKED，不能保证“设好 secret 即一定能跑”。

## 用户亲自设置

1. 先等独立安全审查及无 key 单测完成、受审 helper 发布至 `feat/20261005-windows-desktop-validation`。记下完整 helper SHA `H`，审阅该 SHA 下的 workflow、harness、broker 和 lockfile；不要只审阅可变 branch 名。必须先等待该完整 H 的 “Pinned EXE offline UI” Windows job 成功及 MOCK 报告七场景全部通过，再继续配置凭据；fake Page 单测不替代这一环。
2. 在仓库 Settings → Environments 创建且仅配置 `offerpilot-real-ai-validation`。
3. Required reviewers 只选择已核验用户 `XiaoZheBrother`（ID `112178718`）。关闭管理员绕过保护的选项。Deployment branches and tags 选 Selected branches and tags，唯一规则为 branch `feat/20261005-windows-desktop-validation`，没有 wildcard/tag/其他分支。
4. 若请求 push 由本人发起且本人也是唯一 reviewer，Prevent self-review 必须关闭才能本人批准；这不免除逐次人工批准。若组织强制禁止 self-review，先安排另一已批准的触发身份或调整经审核方案，不能绕过。
5. 仅在该 Environment 的 Secrets 新建 `OFFERPILOT_REAL_AI_DEEPSEEK_20261008`，粘贴新的专用 key。建议 provider 端另加专用余额/费用限制作为纵深防护，不改已有 key 或账户安全配置。
6. Repository Settings → Secrets and variables → Actions → Variables 创建 `OFFERPILOT_AI_APPROVED_HELPER_SHA=H`。它是非secret变量，不含 key。
7. 准备尚未推送的一个空请求提交 `R`：唯一 parent 为 `H`，tree 与 `H` 完全一致，message 见下方。可由用户自己准备，也可在用户明确说配置就绪并授权后由负责人准备并回报 SHA；本工具准备阶段不创建 marker、不推送激活。
8. 设置非secret变量 `OFFERPILOT_AI_REQUEST_SHA=R`，再次检查 GitHub 上的 H 和待推送 R。仅在明确请求执行这一轮后，将 R 正常 push 到原 feature 分支，不 force push。
9. 在 Actions 中查看本次 R 的 “Pinned Windows real AI (manual approval only)” run。secret-free preflight 成功后，人工打开等待的环境批准，逐项核对 run SHA=R、helper SHA=H、固定 EXE、模型、整轮 10 CNY 上限和新专用 key，才批准。
10. preflight 会读 [environment protection](https://docs.github.com/en/rest/deployments/environments#get-an-environment)、[branch policies](https://docs.github.com/en/rest/deployments/branch-policies#list-deployment-branch-policies) 和 [审批记录](https://docs.github.com/en/rest/actions/workflow-runs#get-the-review-history-for-a-workflow-run)。仅 `contents: read`/`actions: read`；无 admin/PAT。403/404/字段缺失都停止，不代改保护。
11. 完成后查看白名单报告（MOCK 另有已通过安全检查的图片）。不要点 Re-run jobs。由用户按自己的密钥管理流程撤销这枚专用 key，并清空两项激活变量；不把本工具成功当成发布验收。

请求 message 必须精确为（用完整 H 替换占位，此文本不是激活提交）：

```text
test: request OfferPilot real AI validation

OfferPilot-AI-Request: H
OfferPilot-AI-Budget-CNY: 10
```

普通代码 push 没有匹配的 R、marker 或保护环境，只会跳过 AI job，不能用 commit message 单独获得凭据。环境 approval 允许执行的是受审 H 的整个 helper；mutable branch 上恶意 workflow 本身可尝试改变规则，因此最终必须人工核对 exact commit 和 tree，代码内检查不能替代这一步。

## Windows 无 secret MOCK 前置验收

独立 `offline.mjs` 入口使用同一个固定安装器、真实 Electron/后端、同一套 UI 场景和清理代码；区别是合成 HTTPS transport、fake key、合成响应和模拟计价。`run.mjs` 固定导入生产 broker，没有环境变量或命令行切换到 MOCK，也没有可配置 upstream。

MOCK 工具在自身进程中封锁真实 HTTPS/TLS 和非 loopback TCP，生产 broker 的字节未改，单独 VM 时钟只让 broker 处于已审计费窗口；UI/EXE/合成事件日期仍使用当前系统时间。此处是工具的进程级网络拦截，不修改 Windows 防火墙/证书或 EXE。七场景合成响应仍经过真实应用处理、JSON 校验、真实 HITL 和 UI 点击。mock 不是 provider 行为、质量、费用或网络兼容性的证明。

普通 push 首先只做无secret changed-path 路由。仅 `desktop/real-ai-validation/**`、本独立 workflow 或本文变化才启动 Windows MOCK；其他 helper 改动和空 activation 不重复启动它。合并提交、缺历史、非祖先 push、文件列表截断均 fail closed。顶层没有 paths 过滤，以免把空 activation 丢弃。

paid preflight 必须从真实 GitHub Actions 记录查到同一 H 的完整 successful push run、唯一 completed/success 的 “Pinned EXE offline UI” job，且 job/run/head SHA 全一致；不能用变量 `true`、别的 helper SHA、局部单测或旧截图代替。产物只在 `mock-evidence/`，artifact 名 `fixed-exe-mock-evidence-*`，result/ledger 都显式 `mode: MOCK`、零真实 provider 证据和 `simulated-counters-only`；不会覆盖 live ledger。

任何修复改变 H 都需要重新通过该 H 的 Windows MOCK。此模式也不允许自动变为 live，仍要后续专用 secret、精确 R/H、同轮预算闩锁和用户本人环境批准。

## 离线验证与发布隔离

```sh
npm ci --prefix desktop/real-ai-validation --ignore-scripts --no-audit --no-fund
npm test --prefix desktop/real-ai-validation
```

测试只用假的 key、假的 HTTPS provider 和 fake Page，不能使用真实 secret。Node 22 是 CI 目标；本地也检查 Node 24。禁止为了测试调用实际 `run.mjs`。

原 `desktop-windows.yml` 现仅为本工具新增三个精确 `paths-ignore`：`desktop/real-ai-validation/**`、`.github/workflows/desktop-real-ai.yml`、本文。产品、锁文件、原 workflow 自身和 release gate 路径仍触发默认完整门禁，dispatch 与原有 package-only 判定不变。

首次修改原 workflow 的迁移提交本身仍触发旧流程。本次候选发布显式使用已存在的 `build: AI [windows-package-only] ` 前缀（末尾空格属于契约），允许额外 focused/build/packaging 和独立 Windows MOCK；不使用全 CI 跳过标记。该前缀不触发本工具 paid 入口：没有精确 R/H 和人工环境批准仍然跳过真实 AI。原 `validation-status` 仍按设计拒绝把 skipped full-regression 当成功，故 package-only run 不应被承诺整体绿色或发布就绪。后续完整验证须使用无此前缀的明确 full-gate 提交。

空激活提交是否被其他 path-filtered workflow 忽略按真实 GitHub run 验证，不预先声称不会触发。

## 首轮 MOCK 的已知诊断边界

[Windows MOCK run 37780561724](https://github.com/offercontext/offerPilot/actions/runs/37780561724) 已完成：同固定安装器下载/校验/安装成功，连接场景 `UI_TIMEOUT`，其余场景 BLOCKED，broker 发送/拒绝均为 0，清理通过，paid jobs 跳过。旧报告未记录 UI 子阶段，不能据此把问题归因于 provider。

真实 React/Ant 组件与 Playwright 原版 selector engine 的局部回归证实：带 tooltip 的“原生 JSON Schema”开关，其 accessible name 不等于纯 label，原 role-exact locator 匹配 0；改为 exact Form label 与 switch role 交集匹配唯一元素，且能实际切换状态。其他 input exact label 保留。该证据来自 jsdom，系统 Chromium sandbox 启动受环境 namespace 限制，未绕过；不是 Windows 重跑成功证明，也不证明该次超时只有一个原因。新增固定子阶段及安全 MOCK 图片用于下一轮定位。

[第二轮 Windows MOCK run 37784479723](https://github.com/offercontext/offerPilot/actions/runs/37784479723) 中，连接配置/测试已真实 PASS；Pilot stream 在 `PILOT_RUNNING/HARU_SYNC_FAILED` 失败。两笔合成 provider 请求正常 SETTLED、无拒绝，主窗和 Haru 的失败截图均显示完整同一 MOCK 回复及终态 idle，清理通过。图片证明最终可见同步，不证明此前 running 已被观察。

helper 原实现有确定的漏采窗口：发送点击返回后才轮询 running，又依次等待 Haru 标签和主窗 Stop；约六秒的合成 provider 帧序列不能替代实际 UI 状态采样。实际 React/controller/Haru hook 局部验证存在 `idle/null → running/null → running/正ID → idle/同ID`，故不放宽正 ID 要求。修复在发送前以 Haru 公共 `onState` 和两窗 DOM MutationObserver 被动锁存真正的 running/正 ID/可见运行控件，owner 用只读 getState 做关联核对；两次 getState 都来自同一 main-process snapshot，不能单独声称两份独立 UI 证据。只看见 idle、只有 bridge 或只有 DOM、ID改变/超时/失联均不得通过。

正常 stream 可在结束后读取已真实锁存的运行中证据，再独立要求 idle、最终身份一致和 Haru 实际正文；cancel 仍必须当下 Stop 可点、broker 请求仍 active，真实点击后由产品断连。没有修改 MOCK 速度、模型、预算、重试或产品。每场景 finally 注销订阅/观察器，诊断只保留固定布尔，不记录正文或会话 ID；此改动仍须新的 Windows MOCK 实跑确认。
