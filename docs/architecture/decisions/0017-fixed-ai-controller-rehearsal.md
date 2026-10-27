# ADR 0017 固定六例模型控制器的生产路径与上线边界

- Status: 非 master 无密钥实现与生产适配代码；生产登记、部署保护和收费验收未启用
- Date: 2026-10-27
- Decider: 用户批准非 master 实现与无密钥演练；未批准 master 合并、安全设置修改或真实临时 token 启用
- 依据: 已审固定审批入口迁移方案、原 companion 四批历史证据及指定提交的真实公开 GET 报告

## Context

原 companion 付费入口用逐批 H/R 变量绑定精确代码，用户手工复制负担较重。
不能通过删除 SHA 检查，或让请求提交自己声称可信来减轻负担。
环境分支规则不是 workflow allowlist，能使用密钥的可信代码必须独立于候选数据。

无密钥第一阶段已取得真实 [CI 40450655710](https://github.com/offercontext/offerPilot/actions/runs/40450655710)
证据：该提交 2452 项完整 gate 通过，匿名公开 GET 核 H、2267 节点 aggregate、旧入口全部 16 个 run
和四批 48 次／0.411934 元账本。此事实只适用于该精确提交，不证明后来生产代码、真实保护或语义效果。
连接工具不支持旧 runs 路径是该连接的白名单限制，不是 GitHub 禁止读取公开历史；不通过换路径绕过限制。

## Decision

### 生产路径存在但默认未配置

保留独立 fake 演练，新增生产 `before`、`paid`、`send` 桥接和惰性 workflow 模板。
另有惰性真实环境 Review 模板，独立 `control-probe` 仅匿名核平台元数据与本人批准，
不读取元数据/provider token、不调用 provider、不产生 paid-started 或生产准入。
生产登记默认缺失，在元数据访问和 provider key 读取之前拒绝。
开发 push CI 仍无 environment、provider secret 或真实 paid job；模板未安装即不触发生产执行。
候选只作固定 JSON 字节，不能作为代码、依赖、缓存、命令、URL 或响应指令执行。
历史回归用固定 hash 的非激活 `.yml.txt` 保留原断言；完整 gate 另独立核实际 installed profile，
开发定义核 dev/legacy 精确字节，control 必须恰有两文件且匹配已审模板，不把 fixture 当部署证明。

### 外部固定控制器与登记

上线需另行批准 master 的无密钥入口登记，实际 dispatch 指向整支锁定的 control 分支。
本人一次性设置外部完整 controller SHA 和严格 Registration JSON；只有升级或显式迁移维护时更新，
不恢复逐批 H/R。JSON 禁止自带 controller_sha，运行、workflow、ref、checkout 都必须匹配外部 SHA。
准确字段及接口维护于[控制器说明](../../../scripts/companion_ai_controller/README.md)，不在本 ADR 复制 schema。

最终 control tree 必须仅含生产与独立 dry-run 两个 workflow，构建后对该精确 commit 完整 CI、独审并固定 SHA。
机器核 tree 与已登记入口历史；其他 workflow catalog ID 只用于检测入口变化，不构成免费调用豁免，
其路径不得出现在 control tree。未知入口、未登记演练、分页缺口、未知 paid-started 都阻断。
DryRunAnchor 显式区分旧开发 push 与 fixed controller SHA 的 control dispatch；后者须唯一 platform-only job，
实核 workflow blob、完成状态及本人环境批准。生产准入必须有至少一条真实 control 演练锚点，不能只用合成或开发 CI。
所有运行历史保证限于本人迁移 checkpoint 确认的 registered campaign，不能宣称机器发现了任意未登记调用。

### 批准前后保持同一授权

`before` 绑定完整代码、请求、登记和历史观察，以严格 schema handoff 传给 paid job。
批准后重读环境、本人批准及全部已登记历史；除明确的当前 job 生命周期变化外，binding 必须相同。
`send` 再完整核验、核 checkout、建立独占 claim，并在读取 provider key 前检查 paid 记录新鲜度。
每轮元数据读取少于 120 秒，paid 准入从该轮起最多有效 120 秒；用户 Review 等待不被偷换为 120 秒窗口。
发送时仍需剩余完整 20 分钟，原截止不延长。

四批历史累计 48 次和 411934 micro-CNY 不重置，第四批缺 requests 的事实不修造。
本版仅六个固定 review，预算 cap 20 元、每次保守预留 3 元，无第七次、重试或任意新数据授权。
任何真实 paid-started 消耗批次；未知成本保留整批最坏 18 元负债并冻结后续。
独立 fake 演练没有真实 paid job，不给真实付费入口增加可由输入控制的 dry-run 豁免。
固定 concurrency 降低重叠风险，不是事务账本；回滚也不回滚已消耗状态或支出。

### 读取权限不因代码准备而扩大

当前 CI 使用匿名公开 GET，只有 contents:read，不向元数据适配器注入或读取真实 token。
匿名每 IP 每小时 60 次。16 条旧 run、尚未计新 control 演练锚点的测算基线约 before 44、paid 45 次；
paid 与 send 两轮约 90 次，再加平台锚点、分页和历史增长，不能保证匿名模式可靠完成生产核验。
允许准备默认禁用、显式传入平台临时 GITHUB_TOKEN 的只读适配器及 fake 测试；
`reader_mode` 默认 anonymous，只有显式 github_actions_read 才由桥读取专用元数据 token，
适配器不自动发现环境凭据，匿名模式不接受 token，认证失败无 fallback。
默认匿名与 actions-read 两个生产 profile 都只存为惰性模板；后者仅准备只读权限和三个核验步骤的专用临时 token 映射。
启用真实 actions:read 和 token 注入仍须本人单独批准，不新增 PAT、长期管理员凭据或写 API。
失败不能自动换到未授权凭据或省略历史以适配限流。

完整 branch protection 需要 Administration:read，公开 rulesets 可能隐藏 bypass。
本人在上线和维护时核完整锁定/no bypass，最终 Review 确认其未变；
owner setup attestation 与 campaign checkpoint 是人类信任根，不是自动证明。
若要求每批自动证明所有隐藏设置，在不增权约束下仍无法上线。

### 部署顺序与验收责任

代码/fake 独审与完整 gate → 明确批准 master 入口及构建干净 control commit →
对最终 control commit 完整 CI 固定 SHA → 授权停旧入口并排空运行 →
本人锁定 control、环境精确分支和唯一本人 Review/no bypass → 登记实际身份、旧开发锚点和迁移 checkpoint 初稿 →
单独批准生产临时 actions:read → 独立无 provider secret 平台 Review/control-probe 及负例验收 →
同一迁移窗口将成功的精确 control dispatch 锚点追加到最终外部 Registration，不改分支/SHA →
旧路径断权后本人配置环境密钥 → 本批另行授权与最终 Review。

每步仍需其对应授权与实际证据；不能用变量或密钥配置代替缺失的代码/平台验收。
独立 control-probe 允许先用只有旧开发锚点的登记初稿运行；生产在缺真实 control 演练锚点时仍阻断。
演练成功后追加外部登记属于一次迁移收尾，不是逐批 H/R；全部新历史必须纳入最终登记。
当前实际 `.github/workflows` 未启用 control dispatch 或环境。旧 R 仍未激活，不因本阶段发布而自动替代或启用。

## Consequences

一次性固定执行器减少每批复制 H/R 的负担，但没有降低精确代码、历史预算和本人批准门槛。
合成正负例可检验注入、身份错配、坏 artifact、重复批次、未知成本与时间边界，
不能代替真实平台保护或付费语义验收。单 owner 仍可修改配置或遭账号失陷，方案不声称防御这一信任根。

凭据仅在固定发送 step 显式映射；GitHub 环境审批保护的是 job，并非逐 step ACL。
疑似凭据回显阻断公开工件，保留未知负债供本人安全处理；mask 不保证不泄露。
当前连接没有 dispatch 写工具，未验证现有已登录执行面前，用户仍需 Run workflow 加 Review 两步。
默认回滚为冻结，不自动复活旧入口。

## Alternatives Considered

1. 复用旧 H/R 或在候选里自推导：缺乏独立固定信任基准，拒绝。
2. 新建独立验证仓库：隔离更清楚，但增加凭据迁移、跨仓库历史和权限成本，非默认。
3. control 分支逐批 push 请求：破坏长期锁定，非最终推荐；开发 push 只用于无密钥测试。
4. 为匿名限流省略第二轮历史或注入长期高权 token：削弱 TOCTOU 或扩大长期风险，拒绝；
   准备严格作用域临时只读适配器，启用另取本人授权。

## Related

- [控制器说明与上线清单](../../../scripts/companion_ai_controller/README.md)
- [原 helper](../../../scripts/companion_ai_validation/README.md)
- [GitHub 手动工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [环境与部署保护](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [运行与审批 API](https://docs.github.com/en/rest/actions/workflow-runs)
- [环境 API](https://docs.github.com/en/rest/deployments/environments)
- [REST API 限流](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
- [分支保护读取权限](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection)
- [规则集读取和隐藏 bypass](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset)
