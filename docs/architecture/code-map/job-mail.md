# 邮件同步与人工审阅地图

[返回业务总图](README.md) · [投递与日程](applications.md) · [设置与桌面](settings-desktop.md)

核对基线：2026-10-28，`454af3d7`。业务依据：[邮件建议审阅 ADR](../decisions/0013-review-job-mail-suggestions.md)。

## 1. 先区分三种状态

| 用户说法 | 对象与入口 | 边界 |
|---|---|---|
| 邮箱连接不上、设置失败 | [jobMailSecureSetup](../../../web/src/services/jobMailSecureSetup.ts) → [secure_setup](../../../src/offerpilot/job_mail/secure_setup.py)、[transport](../../../src/offerpilot/job_mail/transport.py) | 本机凭据与真实邮箱入口有独立安全条件；fixture/粘贴成功不证明真实连接可用 |
| 没读到邮件、同步卡住、预算用尽 | [jobMail service](../../../web/src/services/jobMail.ts) → [job_mail API](../../../src/offerpilot/job_mail/api.py) → [JobMailSyncService](../../../src/offerpilot/job_mail/sync.py) | connection、sync run、extraction attempt 分开；读到邮件不等于抽取建议或业务写入 |
| 建议不对、预览过期、确认结果未知 | 同一 service → [JobMailReviewService](../../../src/offerpilot/job_mail/review.py) | suggestion、preview、receipt 分开；邮件内容和模型输出都不构成确认授权 |

数据模型见 [models.py](../../../src/offerpilot/job_mail/models.py)；请求契约见 [schemas.py](../../../src/offerpilot/job_mail/schemas.py)。注册前缀 `/api/job-mail`，主装配位于 [api.py](../../../src/offerpilot/api.py) `register_job_mail_routes`。

## 2. 同步与未知结果

`start_sync` → 保存 run/范围版本/预算状态 → 后台 `_execute` → transport 读文件夹 → `_process_mail` → [extraction](../../../src/offerpilot/job_mail/extraction.py) / [model_adapter](../../../src/offerpilot/job_mail/model_adapter.py) → 建议与证据。

- `_fenced` 检查连接、scope_version、owner、租约、取消与服务停止；迟到结果必须重新通过检查才发布。
- 过期租约不能证明物理线程已退出；本机旧线程活跃时拒绝新同步。
- 抽取在事务外执行；预算和 attempt 在调用前保存。旧 running/unknown 证据保留供人工审阅，不能静默重调模型。
- 取消、断连、范围编辑查 `cancel_sync` / `disconnect` / `update_settings`，再核对旧 run 与建议是否仍有可操作资格。
- `import_text` 是粘贴导入路径；不能拿它代替真实邮箱 transport、调度或凭据验收。

## 3. 预览 → 确认 → 回执

1. `JobMailReviewService.preview` 绑定建议版本、scope_version、operation_id、精确编辑字段、投递/目标事件快照，生成有有效期的 preview token。
2. `confirm` 首先要求显式确认。若已有同操作回执，先比对完整确认指纹，再恢复结果。
3. 首次确认核对预览、有效期、建议/范围版本与实时目标；目标或字段变化需重新审阅，不在错误分支写事件。
4. 绑定同一 Session 的 `ApplicationEventsRepository` 执行创建/更新；事件、建议 applied 和回执一次提交。
5. 响应丢失先查询 receipt；`ignore` 是忽略建议，不删除已经落地的业务事件。

本服务是经过认证的显式人工动作，不是 Agent 自动写工具，也不使用 `chat_auto_approve_writes` 绕过审阅。

## 4. 诊断与回归

| 症状 | 关键证据 | 测试 |
|---|---|---|
| 同步重复、取消后仍有结果 | connection/scope_version、run ID、owner、lease_until、cancel_requested | [同步](../../../tests/test_job_mail_sync.py)、[调度](../../../tests/test_job_mail_scheduler.py)、[进程锁](../../../tests/test_job_mail_process_lock.py) |
| 邮件未识别、模型未知 | 文件夹/游标、读取 diagnostics、extraction attempt 状态、预算、保留证据 | [transport](../../../tests/test_job_mail_transport.py)、[抽取](../../../tests/test_job_mail_extraction.py)、[diagnostics](../../../tests/test_job_mail_diagnostics.py) |
| 预览或确认冲突、重复事件 | suggestion version、preview token、operation_id、before/after、receipt | [审阅事务](../../../tests/test_job_mail_review.py)、[API](../../../tests/test_job_mail_api.py)、[前端请求](../../../web/src/services/jobMail.test.ts) |
| 桌面可用而网页失败 | Origin/认证、本机 secure setup、连接状态 | [安全设置](../../../tests/test_job_mail_secure_setup.py)、[桌面集成](../../../tests/test_desktop_job_mail_integration.py) |

日志仅用诊断所需标识和安全错误；不要在排查记录中输出邮箱密码、授权码、完整邮件或无关个人信息。

### 邮件专项 CI

[邮件工作流](../../../.github/workflows/job-mail-assistant.yml) 始终响应 master PR/push 和手动触发，保留 `synthetic-acceptance`、`windows-native-lock` 两个检查入口，不使用 workflow 级 paths 过滤。[范围判定脚本](../../../scripts/job-mail-ci-scope.py) 根据 PR base→merge candidate 或 push before→after 的完整 Git diff 决定重任务：

| 改动范围 | 浏览器专项 | Windows 原生锁 |
|---|---|---|
| docs、根 Markdown、DSH 插件及其验收脚本 | 不运行 | 不运行（轻量 Linux 检查返回结果） |
| web、邮件浏览器脚本及 fixture | 运行 | 不运行 |
| 原生锁测试自身 | 不运行 | 运行 |
| 后端/共享测试、依赖、工作流、未知路径 | 运行 | 运行 |
| 手动触发、缺失/无效差异范围 | 运行 | 运行 |

范围为空或不可确定时保守执行；删除和重命名包含旧路径。两个输出必须明确为 true/false，Windows 入口始终检查上游结果：失败、取消、跳过或缺失结果不能变为成功。回归入口：[范围与失败传播测试](../../../tests/test_job_mail_ci_scope.py)。

通用 pytest、ruff、mypy 与前端单测由 [完整 CI](../../../.github/workflows/desktop-windows.yml) 负责，邮件工作流不再重复执行；浏览器所需生产构建、等待工具测试、两项真实合成浏览器验收及 Windows 原生锁仍保留。邮件专项通过不代表完整 CI 通过，也不代表真实邮箱/模型验收；master 的直接 push 不自动启动完整 CI，仍需正常 PR 门禁或显式 dispatch。
