# 工作区备份与恢复边界

本页描述恢复模块的当前边界。导出、恢复 API、冷启动安装及回滚已接入；设置页入口尚待接入。预检成功不等于恢复完成。

## 格式与兼容

`backup_export.export_archive` 使用 SQLite backup 快照，并将最终 ZIP 交给 `backup_archive.stage_archive` 再校验。二者使用同一预检约束：压缩包 256 MiB、展开总量 1 GiB、20,000 条目、单条压缩比 1000、manifest 8 MiB。路径限 32 层、总 UTF-8 长度 2048 字节、单段 240 字节。目录读取失败会使导出失败，不能静默遗漏。

新包含 `offerpilot-backup.json` 文件摘要清单；旧包无清单时只能验证现有结构、schema 和附件，不能证明原始备份完整。预检要求数据库 schema 与当前工作区完全一致，不对上传库执行迁移；SQLite WAL/SHM/journal sidecar 包被拒绝。

真实历史样本来自 `d0cd40688f9ec4802c760d7ae8e868516ca3ba9f`（首次 ZIP 导出实现）。[生成脚本](../../scripts/generate_legacy_backup_fixture.py)执行该提交原始 db/models 模块及 AST 提取的原始导出函数，仅写入合成记录；[fixture](../../tests/fixtures/backups/legacy-d0cd4068.json)保留源代码摘要、ZIP 摘要及内容。[测试](../../tests/test_backup_legacy.py)验证旧 schema 被拒绝、原工作区和 ZIP 不变。它不证明支持该版本迁移，也不代表所有历史版本已测试。

遇到 schema 不兼容，应保留原 ZIP 与原工作区，在独立目录使用匹配的历史版本读取数据；不要用当前版本直接打开唯一的旧数据库尝试升级。跨版本自动恢复需另行验证迁移链，当前没有此保证。

## 图片、Journal 与 ledger

聊天图片存放在 `chat-images/<id>.image`，不是数据库内的图片字节。[独立目录往返测试](../../tests/test_backup_image_roundtrip.py)通过真实上传/消息 API 建立关联，使用离线模型替身；导出并预检到另一目录、移走源目录后，验证会话关联和图片 API 字节读回。该测试不代替未来冷启动替换与回滚验收。

`agent-journal-key.json` 是 Journal **HMAC 指纹域密钥，不是日志加密密钥**。本次新导出及当前主线 `36bc7044` 的既有导出排除该 key；跨工作区不能承诺沿用原指纹域续记运行。`RunRecorderFactory` 对 key 域变化返回 `fingerprint_key_domain_changed`，不能把此限制描述为“日志无法解密”。此边界与恢复后自动任务/旧授权是否继续执行是两件事。

`write-operation-ledger.key` 是另一套写操作账本密钥。预检要求其格式有效，且 `write_operations.fingerprint_key_id` 与 key ID 匹配。这不等价于重新验证每条历史回执的全部 HMAC，也不能单凭 key ID 证明 secret 未被替换。

## 安装与回滚边界

预检仅写新候选目录；确认绑定任务 ID、ZIP 摘要和 REPLACE WORKSPACE 短语。确认后排队，取消保留候选目录。全部参与进程退出后，仅 create_app 在开库前执行替换；安装进程在运行时启动成功后标记完成。切换或启动中断时，下次冷启动回滚，原目录不自动删除。此机制覆盖约定的进程中断，不承诺修复任意外部人工删改。

DB、配置读取/写入、日志写入、MCP 和 Knowledge reset 持进程级共享租约；普通入口不消费待恢复队列。配置读取也必须先持租约，防止恢复后写回旧确认密钥。恢复保留目标安装登录/模型配置及 Journal key，轮换 confirmation_secret，关闭自动写入、proactive 任务、外部连接授权、邮件连接/AI 和排队中的 Knowledge 工作。

API 为 GET /api/backups/restore 与 POST preview/confirm/cancel，沿用应用认证，突变操作另做同源检查。网页刷新不等于重启；用户确认后到重启前新增的工作也会被替换。
