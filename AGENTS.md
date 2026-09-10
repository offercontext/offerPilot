# OfferPilot Agent 工作指南

## 0. 指令与授权边界

- 遵守系统与开发者指令；用户当前任务和已有明确授权优先于本文件及技能中的流程建议。技能不能扩大任务范围或覆盖业务、安全约束。
- 在已授权范围内完成工作；常规、可逆的实现选择自行判断。仅当缺失信息实质影响结果且无法从上下文推断，或下一步超出现有授权时询问；等待期间继续独立工作。
- 发布、部署、向他人发送消息、修改远端文档及破坏性操作需在授权范围内执行。需要额外批准时，先完成可安全进行的准备，给出可审阅的结果；已有授权不重复询问。
- 若技能导致暂停、额外确认或偏离任务，指出实际读取的 `SKILL.md` 路径、相关原文和适用原因，区分硬性约束与流程建议。不要从建议推导新的审批门槛。
- 默认简洁中文，不发可选进度；用户请求或更高优先级指令要求时提供必要更新。回答先给结果，再给验证证据与限制。

## 1. 文档职责

- 本文件是跨 Agent 的仓库施工协议；产品事实见 §4，领域红线见 §6。
- 写改 Markdown 前读取 [文档规范](docs/architecture/documentation-rules.md)。按任务读取相关参考，不全量加载历史 spec、plan 或技能。
- `README.md` 面向用户。仅在用户要求，或公开安装、启动、许可证、命令行为确实变化时更新；它是公开承诺，不是内部验收表。

## 2. 开工与工具

- 改文件前运行 `git status --short --branch`。不覆盖、回滚、stash 或整理用户未提交改动；提交只包含本任务文件。
- 保证任务隔离：工作区干净、当前分支专用于本任务且无并行施工时，可以复用；存在用户未提交改动、多个任务或 Agent 并行施工时，使用独立 worktree。新建时默认基于最新相关上游分支，命名见 §3；不以隔离为由覆盖或整理用户改动。
- 涉及产品口径时优先用 `lark-cli` 读取相关飞书 PRD / ADR / Check 表；涉及代码行为时先读对应本地文档、当前代码和附近测试。
- 对可能变化的状态检查真实仓库、文档或运行时。浏览器验收优先内置 Codex browser，仅在用户明确要求时使用 Chrome。
- Git 操作（含远程传输）直接用 Git CLI，不因 Git 加载 `web-access`。该技能仅用于实际网页搜索、访问或浏览器交互。

## 3. Git 规范

- 新分支：`<type>/<yyyymmdd>-<name>`；type 为 `feat/fix/docs/chore/refactor/test`，日期为本地日期，name 为小写短横线短语，建议 4–6 个词以内，不写 Agent 名。
- 本次任务有改动时，完成验证后做一次小步提交；纯问答或无变更不创建空提交。已有用户提交安排优先。
- 提交标题：`<type>: AI <中文描述>`。type 使用 conventional commits 的 `build/chore/ci/docs/feat/fix/perf/refactor/revert/style/test`。
- `git add` 与 `git commit` 分开执行，先检查暂存 diff；不得夹带用户改动。

## 4. 事实源

OfferPilot 的产品和架构事实源是飞书 wiki：

- 主 wiki：https://ycn8095q3nc7.feishu.cn/wiki/K6BQw1X5Piksm2kDex3cMQMenvf
- Root docx token：`Q353d2stRowjrFx8fmkc6uPmnQb`
- Wiki node token：`K6BQw1X5Piksm2kDex3cMQMenvf`

改相关行为前需要检查的本地文档：

- `docs/architecture/knowledge-system.md`（Knowledge、Memory、Pilot retrieval 或练习消费相关改动）
- `docs/python-rewrite-contract.md`
- `docs/p0-release-checklist.md`
- `docs/superpowers/specs/*`

## 5. 代码改动规则

- 领域模型变化必须同步后端 models、schemas、repositories、API routes、AI tool schemas、前端 types、services、components、tests 和 mock data。
- 不要为已经被当前设计废弃的名称或字段保留长期兼容。如果最新 PRD/ADR 说旧契约已经移除，就干净移除。
- 设计要求的破坏性迁移或 reset 仅限本地开发数据；执行前确认目标与现有授权，未获授权的数据删除先询问。最终汇报说明破坏性变化。
- API 命名、前端 service 命名、Agent tool schema 应暴露当前产品语言，不要继续暴露旧内部语义。
- 优先沿用现有 repository/module 边界。实现一个聚焦改动时，不做无关重构。
- 同一能力同时有 CLI/API 时，尽量保持行为一致。
- 产品运行时的写工具必须保留 HITL 确认，除非配置明确开启 `chat_auto_approve_writes=true`（默认 false）。开发任务的授权不等于允许关闭产品 HITL。
- Agent checkpoint 与 pending/confirm 恢复链路必须保持可用；保留 `provider_blocks` 中的 provider 特定内容。provider fallback 行为以当前配置、实现与测试为准。

## 6. 领域红线

- 事件表和 API 语义是 `application_events`。不要把旧 `events` 表/API 作为长期兼容层重新引入。
- 后端模型名应继续与 `ApplicationEvent` 对齐。
- 事件语义是 `event_type + subtype + tags`。
- `event_type` 至少覆盖 `written_test`、`interview`、`offer_step`、`deadline`、`custom`。
- `assessment` 不是一级 `event_type`；应表示为 `event_type=written_test` 且 `subtype=assessment`。
- Conversation、Chat API、前端 Chat 上下文和 Agent runtime context 使用 `context_type/context_ref`。不要扩展旧 `offer_id` 上下文字段。
- 投递场景使用 `context_type=application` 和 `context_ref=<application_id>`。workspace/global 对话应默认到合理的 workspace context。
- 面试功能按 §4 的当前 PRD / ADR 及相关实施契约核对；[早期版本阶段范围](docs/archive/early-interview-version-scope.md)仅作历史记录，不作为当前功能禁令，也不据此推断功能已完成。
- Knowledge 的长期产品职责、领域模型和数据流以 `docs/architecture/knowledge-system.md` 为唯一事实源；旧 Source -> Wiki 方向及其 Spec、Plan、ADR 已删除。

## 7. 验证与 Code Review

- 按行为风险而非文件名或行数选择最小充分验证；多类影响取并集，遇失败或未解决疑点再扩大。小幅导航、设置改动若影响写入或恢复，也按高风险处理。

| 改动类型 | 日常验证 | 不默认要求 |
|---|---|---|
| 纯文档、注释、图片 | diff、相关链接与事实、指令冲突和约束保留 | 后端全量、前端构建、smoke |
| 局部前端 | 相关测试；代码变化执行 `npm run build`（含 TypeScript 检查）；交互变化用内置浏览器走查对应页面 | 后端全量、Docker、真实模型 |
| 局部后端 | 相关 pytest、ruff；类型或接口变化时检查受影响的 mypy 范围 | 无关前端、Docker、真实模型 |
| 跨层契约、共享基础设施 | 受影响两端测试、静态检查/构建及相关集成验证；门禁脚本验证成功与失败传播 | 无关真实模型场景 |
| 数据迁移、删除/覆盖、权限/auth、HITL、运行恢复、并发所有权 | 扩大回归，覆盖失败与恢复路径，独立审查；不能仅凭局部单测交付 | 不豁免相关安全检查 |
| 发布候选或用户明确要求完整验收 | 完整本地 release gate，加上发布范围涉及的可选门禁 | 不用零散测试代替完整 gate |

- 完成普通任务、提交或推送分支本身不等于发布验收。完整入口：`bash scripts/release-gate.sh` 或 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release-gate.ps1`；保留 pytest、ruff、mypy、前端测试与构建、真实 CLI/HTTP smoke 和 `oc verify --profile local`。
- 容器/部署变化追加 `--docker`（PowerShell `-Docker`）；安装、打包或依赖变化追加 `--install`（`-Install`）；模型接入、提示词或模型交互链路变化追加针对性真实模型验收，完整入口用 `--real-ai`（`-RealAi`）。真实 provider 调用仍须符合已有费用与凭据授权。
- 同一代码快照（提交及未提交 diff）、依赖锁文件、相关配置和环境未变化时，可引用本轮已完成的验证证据，不重复相同命令。记录命令、结果和对应快照；后续改动使受影响检查失效，高风险变化扩大复验。局部检查通过不能宣称完整 gate 全绿，零散结果不能自动免除发布候选的完整 gate。
- 不为可逆、低影响改动新增只复述实现的测试。回归测试应证明实际失败模式或行为边界。`npm test` 已是单次运行，`npm run build` 已含 `tsc -b`，不无条件重复执行。
- 高风险变化（跨层 schema/API/AI tool 契约、数据迁移/破坏性写入、权限/auth、写工具确认、checkpoint/pending/confirm 恢复、并发所有权）必须由未参与实现的人或子代理独立审查。自查不是独立审查；无法取得时明确缺口，不把高风险改动标为可发布。
- 局部布局、样式、文案、低影响交互及不改变行为的内部整理，自查 diff 与相关验证即可；复杂、影响不明或有疑点时追加独立审查。子代理是审查方式之一，不为满足形式拆分任务。
- CR 问题修复后验证受影响部分；接受的剩余风险说明理由。不要只凭子代理的成功描述判断完成。
- 报告实际执行的验证及结果。未运行或失败的必要检查说明命令、原因和风险；需要 Docker 却不可用时明确说明，不声称 Docker smoke 通过。

## 8. 技能工作流

- 工作流以结果为准：需求明确、范围局部时可以直接实施；有重要设计取舍时先讨论方案，跨模块或存在顺序依赖时先明确计划。不为了流程补写固定格式材料，也不省略必要验证和审查。
- 按上级指令和任务匹配使用可读取的技能；不把安装整套 Superpowers 作为前置条件。设计取舍可用 `brainstorming`，实施计划可用 `writing-plans`；行为变更和 bugfix 优先先复现/写回归测试再修复，排错可用 `systematic-debugging`，完成前用 `verification-before-completion` 核验证据。
- 审查按 §7 风险触发，可用 `requesting-code-review` 或等价独立审查；技能名称、子代理数量和固定文档格式本身不是通过条件。
- 纯文档整理可直接审计、编辑和验证，不机械套用产品设计审批、完整代码计划或 TDD。已授权实施不因技能的执行方式选择题再次暂停；技能不能新增审批门槛。
- 技能缺失或不适用时说明，并采用最接近的手工流程；不能因此略过必要的安全或业务验证。

## 9. 飞书文档 / 画板操作

涉及飞书文档或画板时读取 [操作参考](docs/architecture/lark-document-operations.md)。编辑前读取 `lark-cli skills read lark-doc`；遵守 §0 授权边界及参考中的备份、块编辑和回读验证要求。

## 10. 最终汇报

简洁说明改了什么、破坏性变化、剩余风险、验证结果；无破坏性变化时写“无”。未运行的相关测试说明原因。若更新飞书文档，提供链接以及 revision 或回读验证结果。
