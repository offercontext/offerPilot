# Knowledge、记忆与可选上下文地图

[返回业务总图](README.md) · [面试知识捕获](interview.md) · [Pilot 执行](pilot-runtime.md)

核对基线：2026-10-28，`454af3d7`。领域职责的唯一事实源是 [knowledge-system.md](../knowledge-system.md)；本页只补代码导航，不重新定义架构。

## 1. 业务对象和入口

| 用户说法 | 对象 / 职责 | 前端 → 后端 |
|---|---|---|
| 上传资料、原文、抽取失败 | Imported Source → Extraction Snapshot → Evidence | [KnowledgeSourcesView](../../../web/src/components/KnowledgeSourcesView.tsx)、[knowledge service](../../../web/src/services/knowledge.ts) → [KnowledgeIngestService](../../../src/offerpilot/knowledge/service.py) |
| 面试复盘沉淀知识 | Captured Source、Evidence、Knowledge Note Version | [面试捕获链](interview.md#6-相邻功能) → [interview_knowledge_capture Repository](../../../src/offerpilot/repositories/interview_knowledge_capture.py) |
| 修改/归档知识笔记 | Note 的版本与生命周期 | [KnowledgeNoteManager](../../../web/src/components/KnowledgeNoteManager.tsx)、[knowledgeNoteLifecycle](../../../web/src/services/knowledgeNoteLifecycle.ts) → [note_api](../../../src/offerpilot/knowledge/note_api.py) → [KnowledgeNoteLifecycle](../../../src/offerpilot/knowledge/note_lifecycle.py) |
| 记住我的偏好、撤回记忆 | ConfirmedMemory / Version；用户持续认知，不证明外部事实 | [ConfirmedMemorySettings](../../../web/src/components/ConfirmedMemorySettings.tsx)、[confirmedMemory](../../../web/src/services/confirmedMemory.ts) → [memory API](../../../src/offerpilot/confirmed_memory/api.py) → [ConfirmedMemoryRepository](../../../src/offerpilot/confirmed_memory/repository.py) |
| Pilot 没带入知识/重点/历史 | 有开关、范围、版本、预算的可选来源 | [ContextPolicySettings](../../../web/src/components/ContextPolicySettings.tsx)、[contextPolicies](../../../web/src/services/contextPolicies.ts) → [context_sources](../../../src/offerpilot/context_sources/) |

完整面试记录、投递、JD 和普通对话不因存在就自动成为 Source；捕获需经过对应确认链。Source Brief 有独立实现及状态，但不得把旧 Brief/Wiki 管线当成当前统一知识消费路径。

## 2. 导入、抽取和来源删除

`upload_knowledge_source`（[api.py](../../../src/offerpilot/api.py)）→ `KnowledgeIngestService.ingest` 校验正文/资产/来源信息 → [KnowledgeRepository](../../../src/offerpilot/knowledge/repository.py) 保存来源和任务 → [KnowledgeWorkerRuntime / ExtractionWorker.execute](../../../src/offerpilot/knowledge/worker.py) → Snapshot/Evidence/索引。

- 导入接纳不等于抽取完成；分别观察 source、job status/stage/error、snapshot 和 evidence。`origin_url` 在粘贴场景仅记 provenance，不自动抓取网页。
- Worker 的 `_commit_extraction_if_active` 检查活跃任务和来源；取消、删除或丢失所有权后不能提交迟到结果。
- archive/unarchive 改来源可用性；`purge_source` 只排入删除任务。Worker `_execute_delete` 负责隔离目录、数据库清理与后续文件清理，HTTP 返回删除任务不等于文件已经永久删除。
- Brief 相关 `/brief`、`/brief/rebuild` 和 `BriefWorker` 单独排查；不能由 Brief ready 推断 Note、Evidence 引用或 Pilot 注入正确。

## 3. Note 与 Memory 生命周期

| 命令 | 实现与约束 | 失败或恢复 |
|---|---|---|
| Note 修订 | `KnowledgeNoteLifecycle.mutate`；mutation_id、expected_version_id、expected_archived | 复用 key 必须是同一请求；修订保留 block 身份及 Evidence 绑定，只推进新版本 |
| Note 归档 / 恢复 | 检查当前版本；恢复还检查 Source 可用性 | Source 失效不能靠恢复 Note 绕过 |
| Note 删除 | 清空历史正文与捕获预览，移除 Evidence 关联并置空当前指针 | 与归档不同；保留删除回执语义，不能宣称可恢复正文 |
| Memory 确认 / 撤回 / 删除 | `ConfirmedMemoryRepository.mutate`；mutation_id + expected_version | 撤回保留内容，删除清空全部版本正文；重放返回当前可见状态，不复活被删内容 |

## 4. Pilot 消费链

[load_optional_sources](../../../src/offerpilot/context_sources/loader.py) → [ContextSourceLoader](../../../src/offerpilot/context_projector/loader.py) 的有界只读快照 → Contributor → [context_projector](../../../src/offerpilot/context_projector/) 总预算与投影 → Pilot 模型输入。

- Knowledge：`recall_knowledge`（[context_sources/knowledge.py](../../../src/offerpilot/context_sources/knowledge.py)）同时召回当前 Note 与 Evidence，回读来源快照和有效性；[knowledge/search.py](../../../src/offerpilot/knowledge/search.py) `parse_query` 是检索解析入口。
- Readiness：[readiness.py](../../../src/offerpilot/context_sources/readiness.py) 保存用户对目标面试/简历/确认版本的绑定；路由由 [readiness_api.py](../../../src/offerpilot/context_sources/readiness_api.py) 动态注册，不能从最近一次记录自动推断绑定。
- 早期对话：[ConversationSummaryRepository.generate](../../../src/offerpilot/context_sources/summary.py) 显式生成确定性摘录，不调用模型；按精确来源替换可替换历史前缀。工具链、最近消息和 provider_blocks 不能被当成普通文本删去。
- 已识别的临时锁/超时可省略可选来源并记录 unavailable；作用域、完整性和未知错误不能一律降级。来源缺失、临时不可读、开关关闭与预算裁剪是不同证据。

依据：[可选上下文 ADR](../decisions/0011-bound-optional-context-sources.md)、[Knowledge V1 ADR](../decisions/0002-knowledge-v1-source-evidence-scope.md)。

## 5. 诊断与回归

记录 Source/Snapshot/Evidence/Note version、capture attempt、mutation_id、当前生命周期、Contributor 状态及安全指纹；不要只记“知识库不可用”。

测试入口：[来源 API](../../../tests/test_knowledge_sources_api.py)、[导入完整性](../../../tests/test_knowledge_ingest_integrity.py)、[Worker](../../../tests/test_knowledge_worker_runtime.py)、[删除持久化](../../../tests/test_knowledge_delete_persistence.py)、[知识捕获](../../../tests/test_interview_knowledge_capture_repository.py)、[Memory](../../../tests/test_confirmed_memory.py)、[可选来源](../../../tests/test_context_sources.py)、[双通道知识](../../../tests/test_context_knowledge.py)、[Readiness 绑定](../../../tests/test_conversation_readiness_context.py)、[摘要](../../../tests/test_conversation_summary.py)、[暂时不可读](../../../tests/test_runtime_source_unavailable.py)。
