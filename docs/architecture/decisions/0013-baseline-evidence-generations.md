# ADR 0013：已批准产品基线的评估器证据代际

- Status：Implementation scope approved；具体冻结代际尚未批准或激活。
- Date：2026-10-20
- Decider：维护者在协调会话 `01a12eef-1ce9-7c53-9f29-5e82750fcb34` 批准基线刷新实现、114项离线对照及完整本地门禁；批准原文与范围另存本阶段工件。
- 依据：v42 的 `baseline-evaluator-refresh-proposal-v3.md`；原产品基线审批链；当时的 `core-release-v5`。

## Context

产品基线 `harness-core-f78346f0-d3-20261015` 绑定旧评估器。严格 `read_run` 不能由新评估器重算旧 raw，比较也必须使用同一评估器。旧 `source.no_write_intent` 与当前完整过程观测契约不同，不能为复用分数退回旧 grader、放宽 hash 或使用新空 store。

刷新仅证明原批准产品在新评估器下获得的新证据。它不改变原 baseline ID、产品批准身份、promote 记录和 current 产品指针，不自动晋升新候选，也不迁移任何旧人工标签。当前阶段的“同意”不是对尚未产生的证据的审批。

## Decision

### 证据和审批

1. 正式计划为同一新评估器驱动两个独立 checkout，旧产品 `baseline`、新产品 `candidate`，19项 deterministic ×3轮 ×2，共114项。完整分母、失败、unknown、未执行均保留；两臂使用同一 suite、规则、fixture 和网络政策，声明实际实验变量。
2. 旧产品完整 snapshot、runtime 与原批准 product hash 必须相同。新 BRIDGE 始终位于新评估器根，产品 PYTHONPATH 指向相应 checkout/src。新增来源观察记录实际 product package/factory 与 evaluator 文件路径及 hash；不修改用例评分规则或历史 handshake。
3. 原产品批准链由新 journal 解析器核对 record/reservation/promote/revoke，再用原冻结评估器的只读子进程严格重算旧 raw 与原 plan。原 reader 的 checkout、解释器、runtime、evaluator hash 和 source snapshot 全部绑定；不调用旧 journal 解析器读取新事件，不修改旧 checkout，不跳过其 `read_run` hash。原 authority 的 byte hash 绑定原 request，再绑定原 results 和逐行 index/fragments；不以本次首次计算的 hash 代替历史原始身份。
4. 新 gate 使用当前规范 initial-baseline policy 的完整19×3绝对要求（原为 v5；明确时区的用例版本更新后为 v6），明确选择 `baseline` 臂，使用新运行绑定的新独立审查。此 gate 只是刷新资格视图；不是新的产品首次晋升。激活要求旧产品57项实际完成、全部硬规则通过且新 gate enforce/exit0；fail/unknown/未执行可保存测量材料，但不能成为激活的相对基线，也不能沿用旧通过标签。新候选的失败仍阻塞其自身发布，刷新不豁免候选绝对门禁。通用 gate 默认仍选择 candidate，评分规则和阈值不变。
5. 准备命令只向新目录输出 request、fresh gate、raw/manifest/来源/比较 fingerprint 及待审批材料，不写原 store。原 baseline record hash、原 product hash、旧/新 evaluator hash、完整114运行、57项旧产品证据、新 gate/review、历史 reader、expected current/generation 全部进入 request hash。
6. 激活另需 `RefreshApproval`：维护者、有效时间窗、baseline/generation ID、request hash、双 CAS 期望值，以及内容完全匹配的独立人工审批记录引用/hash。不能用一句自由文本、旧标签或实现授权代替新冻结审批。提交时审批和审查需有效；历史加载按实际提交时点核验其有效性，原批准记录不因自然到期被重新解释为当年无效。

### Journal 与恢复

- 继续使用原 store 的内核锁和唯一序号 journal，新增 `evaluator_evidence_refresh` 与 `revoke_refresh`，不创建替代 store。记录位于独立 refresh records 命名空间。
- 产品 current 与 evidence generation 分开。刷新在同一锁内比较 `expected_current` 与 `expected_previous_generation`；只允许刷新当前已批准且未撤销的原产品。
- 写入顺序为独占 reservation、完整不可变 record、journal commit、generation cache。record/request/审批/raw/hash 任一不完整时不可激活；不自动重写审批、清理 reservation 或修复 generation cache。
- 只读加载发现孤立 reservation、半写/tmp、journal 缺序、record 冲突或 cache 与 journal 不符，报告 audit required；不能隐式退回旧 evaluator 证据。
- 撤销当前刷新代际后不自动退回上一代际。撤销原产品基线使其全部刷新不可用于门禁。历史记录均保留；撤销事件同样绑定真实维护者来源，不是删除操作。
- 原 journal 没有精确提交时间，按 record.created_at 代理、原 native 执行区间佐证审批有效性，明确收据自身未被原审批反向固定的限制。新事件记录锁内逻辑 committed_at，并在昂贵重算后复验有效期及单调顺序，不回填历史时间。
- 故障后当前没有自动审计恢复接口。到期留下 orphan 或 journal/cache 中断时，保留全部状态、停止后续解析并交维护者审计；不能把内核锁释放称为 store 已恢复可用。

### 统一解析与门禁

`resolve_baseline` 是 _promote_baseline、CLI gate、release preflight/read/execute/receipt 的共同解析器。原 evaluator 可用时仍返回原已批准视图；不同 evaluator 时必须取得已提交、未撤销且匹配当前 evaluator 的刷新代际，严格重算新 raw。

解析后的视图保留原 baseline ID、product hash、原批准记录 hash 和独立刷新链；sources、source_variant、gate_path、frozen policy/review 来自新证据。相对比较显式选取该臂，不将旧 gate 或旧 review 套在新运行上。候选自比较、忽略 evaluator hash、替换成空库或新的 baseline ID 均不可代替原 current。

完整本地 release gate 与带相对评测的 enforce 门禁分别记录。若相对门禁等待本次冻结审批，先完成可独立执行的完整 local gate、114及审查，交付明确阻塞，不伪造批准或宣布可发布。

## Consequences

增加明确的证据代际和历史 reader 可用性要求；原 checkout/依赖或原证据不可复原时停止，不自动迁移。原始分数和旧审批保留，新分数不覆盖旧结果。当前实现仅接纳固定19项 deterministic、每臂3轮的刷新，不扩展真实模型校准或例外机制。

原生环境崩溃原因仍未知；一次通过不证明底层故障已修复。刷新接口为本地可追溯证明，不认证真人身份，也不抵抗同一 OS 用户或管理员主动伪造所有本地来源。

## Alternatives Considered

1. 直接由新评估器读取旧 raw 或只改 evaluator hash：破坏严格版本与评分语义，拒绝。
2. 回退旧 grader 运行候选：遗漏当前过程观测要求，拒绝。
3. 新空 store / 新 baseline ID / 候选自比较：丢失原产品批准链，拒绝。
4. 两个 candidate-only 运行：可做比较，但不如单一114计划显式保留旧/新臂、顺序和完整分母，本阶段使用 baseline/candidate 两臂。

## Related

- [Harness P2/P3 实施方案](../harness-evaluation-p2-p3-implementation.md)
- [九项驱动与发布边界](../harness-targeted-offline-driver.md)
- [仓库验收规则](../../../AGENTS.md)
- [刷新操作与故障边界](../harness-baseline-refresh.md)
