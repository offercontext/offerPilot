# Architecture Decision Records

> 本目录存放所有架构决策记录(ADR)。每条 ADR 编号 `00NN-<slug>.md`,slug 为动词性短描述。
> 模板与字段要求见 [`../documentation-rules.md`](../documentation-rules.md) §4。

## 当前记录

### Knowledge 系统

- [ADR-0001: 采用 SQLite 作为 Knowledge 运行时唯一事实源](./0001-sqlite-as-knowledge-ssot.md) — Accepted
- [ADR-0002: Knowledge V1 发布范围为 Source/Evidence 工作台](./0002-knowledge-v1-source-evidence-scope.md) — Accepted
- [ADR-0003: V1 暂缓自动 Brief 并保留代码基础设施](./0003-defer-automatic-brief-keep-infrastructure.md) — Accepted
- [ADR-0004: Evidence 确定性过滤与 provenance 契约](./0004-evidence-deterministic-filter-and-provenance.md) — Accepted
- [ADR-0005: Knowledge 一次性破坏性 reset 为 CLI-only](./0005-knowledge-one-time-destructive-reset.md) — Accepted

Knowledge 长期领域模型与数据流见 [Knowledge 系统主文档](../knowledge-system.md)。

### 决策链

```
ADR-0001 SQLite SSOT
    └─ ADR-0002 V1 范围（Source/Evidence 工作台）
           ├─ ADR-0003 Brief 暂缓（自动链路断 + 手动基础设施保留）
           ├─ ADR-0004 Evidence 过滤与 provenance（KBR-02/03）
           └─ ADR-0005 一次性 reset（KBR-07，CLI-only）
```

## 命名约定

- 文件名:`00NN-<kebab-case-slug>.md`,如 `0001-sqlite-as-knowledge-ssot.md`
- 标题:`# ADR-00NN: 标题(动词性)`,如 `# ADR-0001: 采用 SQLite 作为 Knowledge 运行时唯一事实源`

## 何时写 ADR

- 新增/修改接口、模块、协议、跨包约定、依赖方向
- 领域红线落地(application_events、context_type/ref、auth gate 等)
- 完成迭代功能后,把 `docs/superpowers/specs|plans/` 浓缩为 ADR

## 必填段

Context / Decision / Consequences / Alternatives Considered。
**Alternatives 不得少于 2 个备选方案**,且每个都要写"为什么没选"。

## 历史决策

- 旧自动 Wiki 方向的 ADR 已删除，调研结论、否决理由和决策过程保留在 [Knowledge 主文档](../knowledge-system.md) §3、§4、§15 和 §19
- Brief supported-only 状态机的详细设计（原 ADR-0008 + 2026-07-18 brief-mvp spec）在 KV1-01 关闭自动 Brief 后退出 V1 核心链路，已 stash 备查；恢复路径见 ADR-0003
- 本次整顿前 ADR 曾使用 0007-0010 编号，2026-07-19 重新编号为 0001-0005（内容浓缩 + 结合代码现状修订）
