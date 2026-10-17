# ADR-0013：Windows 桌面验证包边界

- Status：Experimental；Windows 安装及真实 UI 验收待执行，不作为生产发行承诺。
- 日期：2026-10-05
- Decider：用户授权最小 Windows 桌面验证范围；本 ADR 记录本次实现选择，不宣称用户逐项选择了技术细节。
- 依据：现有 `web/` React 前端、`src/offerpilot/` Python/FastAPI 与 SQLite、用户要求复用后端的验证范围、[仓库验收规则](../../../AGENTS.md#7-验证与-code-review)。

## Context

当前产品已有 Web/CLI 运行方式。需要验证 Windows 用户无需开发环境即可安装、启动、保存数据并在重启后恢复。完整后端改写、生产分发和自动更新不在本次范围；仅在开发机调用 API，不能证明安装和桌面 UI 链路成立。

## Decision

使用最薄的 Electron 主进程包装现有构建后的 SPA，随 NSIS 安装包携带 PyInstaller 冻结的现有 Python 后端。Python 应用依赖继续由仓库 `uv.lock` 管理，打包工具单独固定版本。

新增桌面组合入口，不改变 Web/CLI 入口的默认使用方式。Electron 负责后端启动、ready/health 等待、窗口与退出生命周期；后端保留监听 socket，并在 ASGI 完成启动后通过 stdout 输出带版本的 ready 消息。父进程持有 stdin pipe，EOF 触发有界退出，避免父窗口结束后遗留后台服务器。日志走 stderr，不得输出会话密钥。

ready 报告实际 PID 与父 PID。冻结程序保持精确子进程 PID 校验；Windows 开发环境的 Python venv redirector 允许一次直属子进程转发，但必须显式启用、校验正安全整数 PID 及父 PID 等于所启动的 launcher PID，并继续执行原有本地鉴权健康检查。关闭与强制终止仍操作 Electron 持有的子进程和 stdin pipe，不能信任协议中的 PID 来终止其他进程。

桌面 HTTP 只绑定 `127.0.0.1`。每次启动由主进程生成独立随机 token；主进程只向当前窗口的精确本地 origin 注入 token。后端在最外层校验唯一 Host、Origin 和 token，覆盖 API、静态文件、健康检查及 preflight；它独立于用户可编辑的产品 auth 设置。会话密钥不存 URL、localStorage 或工作区配置。它防止来自其他网页的无凭据访问，不承诺防御同一系统用户下的恶意软件。

首次由操作系统分配端口并持久化，以后复用端口及 Electron persistent session，避免 origin 改变导致前端状态丢失。端口被占用时明确失败，禁止自动换端口绕过。Electron 持有单实例锁。桌面工作区位于用户 AppData，与原 CLI 默认工作区分离；不自动迁移或覆盖旧数据，不在安装资源目录写数据库。

首轮采用保守 renderer 权限：sandbox、context isolation、禁用 Node integration，拒绝弹窗/下载/麦克风及跨源导航，CSP 限制外部资源。语音、外部模型素材及导出等未验证能力须显式列为范围外。打包资源包括 tokenizer 缓存和本地 LiteLLM 价格表，基础启动不能依赖临时下载。

Windows workflow 仅对精确验证分支 `feat/20261005-windows-desktop-validation` 的 push 激活，并保留可选人工触发；不响应 master、其他分支或 PR，不发布 release。产物为未签名 NSIS 安装包及 SHA256。推送该分支及随之执行远端构建须有明确授权。实验包构建与原有完整发布回归在独立 job 执行；前者必须通过桌面专项、冻结及打包资源检查才上传，并明确不是生产发行版，不能替代完整 `release-gate.ps1 -Install` 的通过证据。最终状态如实汇总，失败、取消或跳过的完整回归不能算成功。可选 `workflow_dispatch` 的默认分支注册条件不构成自动修改默认分支的授权。详细命令、目录和人工清单仅维护在 [桌面验证说明](../desktop-validation.md)。

## Consequences

- 复用已有 Python 业务逻辑、SQLite 和前端，降低一次试验同时更改业务与分发架构的风险。
- 增加 Electron、PyInstaller、动态依赖收集和双进程管理的成本；Windows 必须在 Windows 构建，Linux 冻结 smoke 不能代替 Windows 证据。
- 冻结进程 smoke 可以证明导入、鉴权、写入/重启和清理；安装、renderer 与 UI 流程需要独立真机验收。
- pilot 的保守权限会使部分已有 Web 功能不可用，必须公开说明，不能以核心 CRUD 通过推断全部产品支持。
- 暂无签名、自动更新、真实数据迁移及升级回滚保证。生产化前需要重新审查凭据存储、分发与第三方许可证、权限策略及支持范围。

## Alternatives Considered

1. 保留 Python 服务，使用 Tauri/WebView2 外壳：可能减小壳体体积，但会引入 Rust/WebView2 工具链、Windows runtime 依赖与另一套 sidecar/IPC 验证工作。当前原型优先直接复用 Node/Electron 工具链；这是工程权衡，并非已测量的性能结论。
2. 将业务后端改写为 JavaScript/Rust 并嵌入外壳：可减少运行时种类，但需要重新证明 API、SQLite、AI 写确认和恢复链路，超出“现有 Python 后端验证包”的授权范围。
3. 浏览器打开本地 Python 服务或只发布启动脚本：实现更轻，但仍需要环境或浏览器协同，无法完成同等的独立 Windows 安装包与窗口生命周期验证目标。

## Related

- [桌面验证说明及人工验收清单](../desktop-validation.md)
- [Python rewrite 契约](../../python-rewrite-contract.md)
- [AGENTS.md](../../../AGENTS.md)
