# ADR-0003: V1 暂缓自动 Brief 并保留代码基础设施

**Status**: Accepted (2026-07-18)
**Decider**: 用户

## Context（背景）

Source Brief 此前作为 Imported Source Ingest 的自动下游：Extraction 成功后自动入队 Brief Job，`BriefWorker` 生成结构化导读。但代码核查确认 Pilot、练习和 Knowledge Context 均不读取 Brief，"Brief 辅助 Source 粗排"只是长期候选方向。为稳定 Brief 设计 supported-only 状态机会引入大量模型调用和实现复杂度，却不能改善 V1 内部知识消费链路（见 ADR-0002）。

KV1-01 已关闭自动触发，但采用"在调用点剪 callback"策略而非删除基础设施，以保留 V1.1 恢复路径。

## Decision（决策）

1. **自动触发链已断（KV1-01 落地）**：

   - `ExtractionWorker(...)` 不再传 `on_extraction_succeeded` callback（`api.py` runtime 注册点）
   - `commit_extraction()` 不再调 `_mark_brief_enqueue_pending_for_snapshot`（该方法保留但 0 caller，docstring 标注 "KV1-01 不再调用"）
   - `_repair_missing_brief_jobs` 保留但首行守卫 `if self._on_extraction_succeeded is None: return`，V1 runtime 永不注册 callback，实际是 dead path

2. **手动基础设施全保留（V1.1 恢复路径）**：

   - `POST /api/knowledge/sources/{id}/brief/rebuild` API → `service.rebuild_brief()` → `create_job(kind="brief")`
   - `BriefWorker` 仍被 `KnowledgeJobRunner` 注册，Brief job 队列在 `tick_brief` 中消费（有 job 就跑）
   - KBR-06 结构化 patch repair（`parse_repair_patch` / `apply_repair_patch`，replace / delete / split 三种操作，`BRIEF_REPAIR_PATCH_VERSION = 4`）
   - Brief Schema v2（`BriefPayload` schema_version 固定 2，未引入 Schema v3 / BriefClaim）
   - `enqueue_or_block_brief` 保留（acceptance / Brief 测试 callback 注册入口）

3. **V1 语义边界**：

   - Brief 不进入 V1 发布门禁（acceptance profile 通过 `enable_brief=False` 隔离）
   - Brief 不进入 Source 状态语义（Source 列表 / 详情头 / 轮询不展示 Brief 状态，前端 KV1-02 落地）
   - Brief 未生成、生成中或失败均不影响 Source / Evidence 可用性
   - Brief status `not_started` 不得表现为未完成或错误

4. **V1.1 恢复策略**：若真实人工使用证明 Brief 值得继续投入，恢复路径是重新挂 callback（在 `api.py` runtime 注册点传 `on_extraction_succeeded`），不需要重建 `BriefWorker` / patch repair / Schema 基础设施。恢复时必须基于真实使用数据新建设计，不默认恢复 supported-only 状态机的全部复杂度。

## Consequences（后果）

- 自动 Brief 模型调用降为零，Provider 不可用不影响 Imported Source 达到可用状态
- Brief 代码（`brief.py` + `BriefWorker` + patch repair）作为保留基础设施存在，V1 不计入功能完成度
- dead code（`_mark_brief_enqueue_pending_for_snapshot`、`_repair_missing_brief_jobs` 守卫早返回）与 live code（手动 rebuild API、BriefWorker）混存，阅读代码时需注意区分
- 若未来恢复自动 Brief，必须基于真实使用数据新建设计，不直接恢复旧 Spec

## Alternatives Considered（备选方案）

| 方案 | 优点 | 缺点 | 为什么没选 |
|---|---|---|---|
| 删除 Brief 全部代码 | V1 仓库最干净 | V1.1 恢复 Brief 需重建 `brief.py` + Worker + patch repair | 保留基础设施成本低于重建，且 Brief 仍可手动实验 |
| 保留自动 Brief | 用户导入即有导读 | Brief 模型调用非核心，验收依赖随机 verdict 翻转 | Brief 价值未证明，见 ADR-0002 |
| KV1-01 剪 callback + 删基础设施 | 折中 | 删 BriefWorker 后手动 rebuild 也失效 | 与"保留 V1.1 恢复路径"矛盾 |

## Related（关联）

- ADR-0002 V1 发布范围（解释为什么 Brief 不在 V1）
- [Knowledge 系统主文档](../knowledge-system.md) §10 Source Brief 生命周期
- `src/offerpilot/knowledge/brief.py`、`src/offerpilot/knowledge/worker.py`（BriefWorker）
