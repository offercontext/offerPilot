# ADR-0004: Evidence 确定性过滤与 provenance 契约

**Status**: Accepted (2026-07-13)
**Decider**: 用户

## Context（背景）

Imported Source 的原文常含 frontmatter、作者署名、阅读数、导航条、装饰图、Obsidian wiki embed、Evernote resource 等"样板噪声"。若不过滤，这些内容会污染 Evidence 的 `heading_path` / `search_text` / FTS 索引，搜索优先返回噪声而非正文。同时，canonical Source 原文必须保留用于回读，过滤不能改写原件。

KBR-02（结构化 provenance + frontmatter 排除）和 KBR-03（元数据样板过滤 + 规则统计）已实现并 active。

## Decision（决策）

1. **frontmatter 边界识别（KBR-02）**：确定性识别文档头部 YAML frontmatter 边界（`---` ... `---`）。canonical text 不改写，frontmatter 原文保留用于回读；只跳过落在 frontmatter 行范围内的 token 的 Evidence 发射，避免键值污染 `heading_path` / `search_text` / FTS。

2. **provenance 白名单提取（KBR-02）**：从 frontmatter 提取白名单字段（title / author / source_url / date 等）作为 Source provenance。非白名单字段（tags、自定义元数据）只忽略 + 警告，不进领域模型，不进 FTS。provenance 用于出处展示，不进 `search_text`。

3. **元数据样板过滤（KBR-03，6 类稳定 rule_id）**：

   - `author_byline`：作者署名行
   - `reading_count`：阅读数 / 浏览量
   - `navigation`：导航条 / 面包屑
   - `decorative_image_shell`：装饰性图片占位
   - `obsidian_wiki_embed`：Obsidian wiki embed / 双向链接
   - `evernote_resource_fragment`：Evernote resource 片段

4. **adapter 信号隔离**：平台适配器（adapter）信号只从允许的独立结构块提取，排除 fence / table / code_block / 行内 code。教程示例语法不得激活平台 adapter（防止 Java/Python 教程误触发 Spring/Django adapter）。适配器激活集合贯穿 `_emit_block` → `_consume_*` → `evaluate_block`；无信号时仅全局低歧义规则。

5. **canonical 不改写**：过滤不改写原件。canonical Source 保留 frontmatter 与噪声原文，Evidence 从过滤后的 token 流发射，回读时通过 `_line_offset_table` 回到 canonical 原文。

6. **版本登记**：`evidence_policy_version`（当前 2）和 `metadata_extraction_version` 写入 `structure_manifest`。`filtered_by_rule` 记录每类规则命中计数和 `filtered_block_total` 总数，不复制被过滤正文 / URL / 作者名 / 本机路径。

7. **policy 独立模块**：`evidence_policy.py` 是独立模块，规则与 adapter 通过 `ExtractionContext` + `select_adapters` 组合，不在 extractor 内硬编码。

## Consequences（后果）

- Evidence / FTS 优先返回正文，噪声不污染搜索
- canonical Source 原文完整保留，回读准确
- 规则命中可追溯（`structure_manifest` 记录统计），不暴露被过滤正文
- 过滤规则演进时通过 `evidence_policy_version` 版本号区分，旧 Extraction 不自动失效

## Alternatives Considered（备选方案）

| 方案 | 优点 | 缺点 | 为什么没选 |
|---|---|---|---|
| 不过滤，原样索引 | 实现最简单 | frontmatter / 样板污染搜索，优先返回噪声 | 违反 V1 搜索质量门禁 |
| 改写 canonical Source | 单一文本源 | 回读丢失原文，违反不可变原则 | 与 ADR-0001 不可变文件冲突 |
| LLM 清洗 | 智能识别样板 | 引入模型调用，破坏 V1 无 Provider 门禁 | V1 必须确定性可复现 |

## Related（关联）

- ADR-0001 SQLite SSOT（canonical 文件 + Evidence 行）
- ADR-0002 V1 发布范围（Extraction 是 V1 active 路径）
- `src/offerpilot/knowledge/evidence_policy.py`（规则定义）
- `src/offerpilot/knowledge/extractor.py`（frontmatter 边界 + adapter 信号）
