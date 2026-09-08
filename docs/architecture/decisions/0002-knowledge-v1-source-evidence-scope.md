# ADR-0002: Knowledge V1 发布范围为 Source/Evidence 工作台

**Status**: Accepted (2026-07-18)
**Decider**: 用户

## Context（背景）

Imported Source、确定性 Extraction、Evidence 和 FTS 已形成独立可用的知识资料工作台。Brief 只面向人类，尚未证明实际价值；Pilot、练习和 Knowledge Context 当前未接入 Knowledge（`ai/tools.py` 中无任何 knowledge tool registry）。把 Brief 或消费链路纳入首发会扩大领域契约和验收面，延迟对 Source/Evidence 基础可靠性的验证。

## Decision（决策）

1. **V1 active 路径（代码已实现）**：

   - Imported Source 导入（`file` / `bundle` / `paste` 三种 import_method）
   - 确定性 Extraction（`MarkdownExtractor` + KBR-02/03 元数据过滤，见 ADR-0004）
   - Snapshot 持久化（不可变原文 + hash）
   - Evidence 生成与 FTS 提交（trigram tokenizer + bm25 分列权重 `0/0/1/2/8`）
   - 搜索（`parse_query` 三模式：empty / fts / substring；短查询 LIKE 回退；Retrieval Trace 每次写一条）
   - 原文回读（Evidence ID → Source provenance → heading / line / char 定位）
   - 归档 / 恢复 / 永久删除（文件 + Evidence + FTS + Job 一致清理）
   - Knowledge-only 一次性 reset（CLI-only，见 ADR-0005）
   - Retrieval Trace（query / filters / hits / duration / error_code）

2. **V1 不包含**：

   - 自动 Source Brief（见 ADR-0003）
   - Captured Source、Knowledge Note、Note Version、Note citation
   - Knowledge Context、Pilot、练习或其他业务模块消费 Knowledge
   - embedding、向量数据库、rerank、GraphRAG、标签、主题树、Collection、Wikilink
   - PDF、DOCX、OCR、网页抓取、远程 URL 自动导入

3. **V1 发布门禁**：完全在无 AI Provider 条件下执行。`oc knowledge-acceptance --profile v1` 通过 `enable_brief=False` 隔离 Brief，评估 5 份真实 Source + ≥20 条人工确认查询，Evidence 回读成功率 100%，lexical Recall@5 100%，MRR ≥ 0.9；自然语言 Recall@5 低于 80% 时记录结果，不在 V1 偷加向量方案。

4. **后续阶段**：V1.1 是 Brief 决策检查点（不是已承诺版本）；V2 再设计内部消费链路。各阶段不能用后续能力阻塞前一阶段发布，也不能为尚未确认的后续需求预建抽象。

## Consequences（后果）

- V1 发布门禁可完全在无 AI Provider 条件下执行
- 自动 Brief 触发必须关闭（KV1-01 已落地，见 ADR-0003）
- V1 验收重点：确定性、数据完整性、搜索质量、原文回读、失败恢复、工作台交互
- V1.1 和 V2 都需基于 V1 真实使用数据重新进入设计，不直接继承旧 Spec 全部范围

## Alternatives Considered（备选方案）

| 方案 | 优点 | 缺点 | 为什么没选 |
|---|---|---|---|
| V1 含 Brief | 用户导入即有导读 | Brief 模型调用非核心，扩大验收面，延迟 Source/Evidence 验证 | Brief 价值未证明，见 ADR-0003 |
| V1 含内部消费（Pilot/练习）| 知识库立即产生价值 | Knowledge Context 契约未实现，扩张数据库 / API / tools / 前端 | 消费链路是独立垂直切片，不应伪装成 V1 小改动 |
| V1 含 Captured Source / Note | 资料沉淀闭环 | Note / Version / citation / HITL 全未实现，范围爆炸 | Evidence 已足够验证"知识库能否可靠使用"假设 |

## Related（关联）

- ADR-0001 SQLite SSOT
- ADR-0003 Brief 暂缓（解释为什么 Brief 不在 V1）
- ADR-0004 Evidence 过滤契约
- ADR-0005 一次性 reset
