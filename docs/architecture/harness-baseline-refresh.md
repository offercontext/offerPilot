# 已批准产品基线的评估器证据刷新

本能力实现 [ADR 0013](decisions/0013-baseline-evidence-generations.md)。它向原 store 追加证据代际，保留原产品 baseline ID、批准链、promote 记录和 current 产品指针。实现授权不等于具体冻结代际审批，刷新也不晋升候选或授予发布资格。

## 准备与执行

1. 保持旧批准 checkout、原解释器及其依赖只读。历史 manifest 决定两者路径，不能从解释器推断 checkout。原冻结评估器严格读取原 raw、plan 与 manifest；原 authority → 原 request → results → index → fragments 链必须匹配。缺失原字节绑定时拒绝刷新。
2. 在新评估器中用正式 `oc eval plan` 创建同一 deterministic 计划：旧产品 `baseline`、新产品 `candidate`，19 项 ×3 轮 ×2，共114项；两个 checkout 必须不同。真实 Provider、裁判和付费 grant 不属于此流程。
3. 执行前冻结每个产品完整文件集合：

   ```powershell
   oc eval baseline refresh-sources --plan <run>/run-plan.json --output <run>/source-files.json
   oc eval run --plan <run>/run-plan.json
   ```

   来源档案的完整文件集合 hash 必须匹配 manifest。新 handshake 实际记录产品 package、create_app 工厂与新评估器脚本的路径及 SHA。历史加载与当时冻结文件集合比较，不要求未来候选 checkout 一直保持旧内容；原批准产品的历史 reader 仍需精确复原。
4. 保留全部114项原始结果，失败、unknown、未执行不删除。原始 `incomplete` 和旧人工标签不改写。对照声明所有实际变化的变量；候选失败仍阻塞候选发布。
5. 将新独立审查写为 `RefreshIndependentRecord`，明确 `reviewed_variant=baseline`，绑定旧/新产品 snapshot、当前 evaluator、完整 manifest 与 `evidence_fingerprint`。使用 `ReviewBundle.independent_reviews` 引用其字节 hash，不复制历史 adjudication 或例外。

历史 descriptor 是 schema 3 的 `baseline-history-reader`，只包含 `native_receipt`、`approval_file` 两个绝对路径/SHA 引用。历史 native 收据用于佐证原审批有效期覆盖执行区间；未被原审批反向固定的收据不称为不可变提交证明。旧 journal 没有精确提交时间，保留 `record.created_at` 代理这一边界。

受测进程禁止写字节码，并使用每次新建名称、尚未创建的 cache 前缀，避免读取 checkout 内同时间戳/大小的陈旧 `.pyc`。隔离解释器显式传 `-B` 与 `-X pycache_prefix=...`；唯一前缀记录于执行收据，稳定历史证明仅绑定加载策略。旧源码及既有缓存不清理、不改写，此策略不补写历史运行证据。冷编译仍遵守原 startup/wall 阈值。

## 冻结审批材料

```powershell
oc eval baseline refresh-prepare --store <原store> --baseline-id <原ID> --generation-id <新代际ID> --run <run> --policy evals/policies/core-initial-baseline-v5.json --review <新review.json> --experiment <experiment.json> --history <history-descriptor.json> --output <新目录>
```

准备只向新目录写入历史审计收据、measurement、qualification gate 和 `refresh-request.json`，不写原 store。没有合格新审查或旧产品57项未全部硬规则通过时，材料可记录测量结果，但 `activatable=false`，不能激活。计划 checksum、实测 manifest、原始字节、来源档案、review 和 gate 均进入冻结绑定。

维护者必须审阅具体 request 后另行批准。`RefreshApproval` 绑定 baseline/generation ID、request hash、`source_variant=baseline`、双 CAS、维护者和有效期。`record_ref/record_hash` 指向精确内容：schema 3、kind=`maintainer-source-record`、record 为该审批的 Pydantic JSON 规范化字段，排除 `record_ref/record_hash`。UTC 时间按 JSON 序列化形式保留；自由文本或实现阶段“同意”不是本次冻结审批。

只有取得该项明确批准后，才执行：

```powershell
oc eval baseline refresh-activate --request <refresh-request.json> --approval <本次冻结approval.json>
```

激活在原内核锁内重算新证据与旧批准链，检查 `expected_current` 和 `expected_previous_generation`，重验冻结输入。昂贵重算结束后、record 写入前先检查时效；写入后再采样锁内逻辑 `committed_at`，复核审批/审查及顺序，随后追加 journal。它不是文件系统物理提交纳秒时间。

## 撤销与故障边界

`oc eval baseline refresh-revoke --store <原store> --revocation <本次明确撤销记录.json>` 使用 `RefreshRevocation` 及对应精确维护者来源。撤销必须绑定同一产品、已提交代际、原 record digest 和当前双 CAS。撤销当前代际后不回退旧代际；撤销原产品使所有代际不可用，历史文件保留。

写入顺序为 reservation → record → journal → generation cache。孤立文件、半写/tmp、缺序、错绑或 cache 不符一律拒绝，不能自动清理、修复 cache、重放审批或降级旧 evaluator。锁释放不等于故障状态已经可恢复。

若到期/进程死亡发生在 reservation 后，或 journal 后 cache 写入前，该 store 进入 **audit required**，后续 gate/refresh/release 都停止。当前没有自动审计恢复接口。保留目录和字节，导出 journal、record、reservation、request/审批及缓存差异，交维护者审计后另行决定修复；不得以手工删文件或调用 legacy recovery 继续门禁。

CLI gate、release preflight/read/execute/receipt 及后续产品晋升共用 `resolve_baseline`。普通本地 release gate 与带批准基线的相对 enforce 门禁分别报告；具体代际待批准时，不能用本地 gate 或114结果宣布可发布。真实模型、裁判、人工质量校准及发布仍受各自前提约束。

破坏性变化：无。该本地来源机制不认证真人身份，也不防御同一 OS 用户同时伪造全部材料。
