# ADR-0005: Knowledge 一次性破坏性 reset 为 CLI-only

**Status**: Accepted (2026-07-17)
**Decider**: 用户

## Context（背景）

Knowledge 模块经过 KI / KBR / KV1 多轮迭代，schema 和数据格式经历过破坏性变更（KI-01 切到 Imported Source Ingest、KBR-07 reset 切换）。本地开发数据可破坏性迁移，但生产环境需要明确的"清空 Knowledge 域"入口，且不能误触发。

早期版本考虑过运行时审计快照（reset 前保存 manifest 供回滚），但 commit `d0e4ded` 已删除该机制——审计快照引入复杂度但实际从未被用于回滚，且与"一次性、不可恢复"语义矛盾。

## Decision（决策）

1. **CLI-only**：`oc knowledge reset --confirm` 是唯一 reset 入口。不在运行时（API / Worker 启动 / 定时任务）触发 reset。

2. **破坏性 + 一次性 + 不可恢复**：

   - 清空 Knowledge 域所有表（Source / Snapshot / Evidence / FTS / Job / Brief / Trace 等）
   - 删除 `$OFFERPILOT_DATA/knowledge/` 下所有不可变文件
   - 不保留审计快照、不保留 quarantine 待清理记录、不可回滚

3. **明确不做运行时审计快照**：`reset.py` 显式声明不做审计快照，`db.py` 启动恢复已删除旧 quarantine / manifest 协议。审计快照在 `d0e4ded` 后是真删，不是关闭。

4. **预检与原子性**：

   - reset 前预检路径（防 symlink / 路径逃逸）
   - DB 提交即完成 reset；quarantine 清理失败记录待清理状态而非落到失败半状态
   - 启动恢复兜底两方向（DB 与文件系统）

5. **范围闭集**：reset 只清 Knowledge 域表，不影响 Application / Resume / Question / Offer 等其他领域。表清单为 Knowledge 闭集，独立核对。

## Consequences（后果）

- 本地开发可破坏性迁移（reset 后重建），生产环境需用户显式 CLI 触发
- reset 不可逆，执行前必须 `--confirm` 二次确认
- 不存在"半 reset"状态：要么完全成功，要么完全失败回滚
- 审计快照删除后，若未来需要 reset 前备份，需独立决策（不默认恢复）

## Alternatives Considered（备选方案）

| 方案 | 优点 | 缺点 | 为什么没选 |
|---|---|---|---|
| 运行时审计快照 + 回滚 | reset 可回滚 | 复杂度高，实际从未用于回滚，与"一次性"语义矛盾 | `d0e4ded` 已删，commit 历史可查 |
| API 触发 reset | 远程可调用 | 误触发风险高，与"破坏性不可逆"不匹配 | CLI 需本地访问 + `--confirm`，误触概率低 |
| 按 Source 增量清理 | 渐进安全 | 模式切换期需"清空全部"，增量无法覆盖 schema 破坏性变更 | 一次性 reset 是破坏性迁移的必要补充 |

## Related（关联）

- ADR-0001 SQLite SSOT（reset 清空 Knowledge 域表）
- ADR-0002 V1 发布范围（reset 是 V1 运维入口）
- `src/offerpilot/knowledge/reset.py`（reset 实现）
- `src/offerpilot/cli.py`（`oc knowledge reset` CLI）
