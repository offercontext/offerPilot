# 结构化面试故事库：发布验证记录

## 结论

**状态：当前分支的发布门禁与真实浏览器验收已通过，可进入最终合并审核；本轮未推送、未合并。**

- 实施基线：`bc706a68e1ae05d355df93a1985e439e0b117fbd`
- 最终浏览器验收代码：`858cb25a477b4c0b42538e09529615f8e15125df`
- 验收日期：2026-08-11 至 2026-08-12（Asia/Shanghai）
- 正式 Provider 配置未修改；执行过程未输出密钥、原始提示、模型原文、用户快照或原始 Provider request ID。

本轮确认 UI 与 Pilot 两个入口均能基于用户显式选择的原始来源生成 Story Proposal，经人工编辑与确认后保存为不可变版本，并在历史中显示冻结来源及来源变化状态。浏览器验收使用同一专用 Target、亮色中文界面和 `1455×1200` 单视口截图；CDP、本地请求、Provider 出站、跨领域零写入和进程清理均通过 fail-closed 校验。

## 本轮收口修复

- 完整 real-AI 验收客户端上限由错误的 60 秒改为 180 秒；local 验收仍保持 60 秒，不增加 Provider 业务重试。
- Provider 未知结果返回准确的 `retry_after_ms`；前端在响应丢失时使用 30.25 秒安全回退，冻结输入并仅允许原 Attempt/key 重放。
- Story 结构修复继续使用原冻结 Evidence Catalog；语义证据失败仍不可重试。
- `user_assertion` 可作为显式用户陈述支持事实性 Story 目标，同时保持“用户陈述、未外部核验”语义。
- 手动保存改为逐 target 显式绑定证据，不再把第一条来源自动复用到所有区块。
- `provider_unknown` 在租约有效期内稳定返回 pending，租约过期后才允许 fencing 接管；所有终态清理 lease/token。
- 浏览器 Provider 审计区分“模型调用”与“HTTPS CONNECT 传输隧道”：Attempt 的 `repair_count` 与浏览器重放次数负责调用审计，代理负责证明端点和请求窗口；同一调用的连接重建或同一连接复用不会被误判。

## 完整本地门禁

### 后端五组

结果目录：`%TEMP%\offerpilot-story-pytest-release-ac5a4f43344d4cfbaeb913ead63a4432`

先执行全量收集、原始 node ID 重复检查并写入 `full-manifest.txt`，再运行五个命名组及 aggregate。最终收集 **1,851** 个唯一 node ID，分组并集与 manifest 完全一致，无重复、遗漏或额外项。

| 分组 | 收集 | 通过 | 允许 skip | failures/errors | exit code |
| --- | ---: | ---: | ---: | ---: | ---: |
| agent | 423 | 423 | 0 | 0 | 0 |
| domain | 70 | 70 | 0 | 0 | 0 |
| knowledge | 659 | 655 | 4 | 0 | 0 |
| proposals | 384 | 384 | 0 | 0 | 0 |
| misc | 315 | 315 | 0 | 0 | 0 |
| 合计 | 1,851 | 1,847 | 4 | 0 | 0 |

4 个 skip 均为既定 Windows 符号链接权限用例，node ID 与原因已由 aggregate 校验。

### 前端十组

结果目录：`%TEMP%\offerpilot-story-vitest-72fc088077a543ce8c8faa8e38e4580f`

当前 Web 指纹与分组结果一致；aggregate 通过，共 **114 个测试文件、862 项测试**：

| 分组 | 文件 | 测试 |
| --- | ---: | ---: |
| components-core | 26 | 124 |
| components-chat | 12 | 184 |
| components-interview | 13 | 62 |
| components-offer | 8 | 45 |
| components-support | 8 | 32 |
| features | 12 | 129 |
| layout | 11 | 66 |
| lib | 11 | 167 |
| services | 12 | 52 |
| theme | 1 | 1 |

### 静态、构建与 local 验收

| 命令 | 结果 |
| --- | --- |
| `uv run ruff check .` | 通过 |
| `uv run mypy src` | 通过，66 个源文件 |
| `npm.cmd run build` | TypeScript 与 Vite 生产构建通过 |
| `uv run oc smoke --static-dir web/dist` | 通过 |
| `uv run oc verify --profile local --static-dir web/dist` | 通过 |
| `uv run oc verify-interview-stories --profile local --static-dir web/dist` | 通过 |
| `uv run pytest tests/test_interview_story_browser_harness.py -q` | 23 passed |
| `git diff --check` | 通过 |

变更范围相对固定 baseline 共 39 个文件，全部位于计划的精确 allowlist；未新增 Story Usage、Knowledge 写入、JD Version 消费或外部招聘平台行为。

## Real-AI 验收

产品/runtime 代码在 `51583c3` 后未再变化；后续 `858cb25` 仅修正浏览器验收脚本及其测试。

| 验收 | UTC 时间 | 结果 |
| --- | --- | --- |
| `uv run oc verify-interview-stories --profile real-ai --static-dir web/dist` | 2026-08-11 17:19:00.675 — 17:20:29.545 | 通过，exit 0 |
| `uv run oc verify --profile real-ai --static-dir web/dist` | 2026-08-11 17:20:29.555 — 17:28:45.544 | 通过，exit 0 |

完整 real-AI 覆盖面试准备、材料、Opportunity Fit Triage/Deep、复盘、知识沉淀、模拟面试和 Chat 确认。未以专项成功替代完整验收，也未通过放宽证据契约获得通过结果。

## UI / Pilot 真实浏览器闭环

证据目录：`D:\Users\yuqi.chen\.offerpilot\verification\interview-story-browser-release-verified-20260812-022741`

Harness 最终输出：`Story browser acceptance passed.`

通过序列：

1. UI：面试故事库 → 新建故事 → 显式选择原始来源 → 来源确认 → AI 草稿 → 人工编辑 → 确认保存 → 关闭并重开历史。
2. 历史：先验证冻结来源为当前，再受控改变临时来源并验证“当前来源已变化”；随后恢复原始临时数据。
3. Pilot：打开真实 Pilot tab → 主动点击“整理面试故事” → 显式选择来源 → 来源确认 → AI 草稿 → 人工编辑 → 确认保存 → 关闭并重开独立历史。
4. 两个入口各创建一个独立 Attempt、Story 和 Version；确认请求各一次；没有 `/api/chat` 或 `/api/chat/confirm` 写入。
5. 浏览器仅访问本地应用/API；Provider CONNECT 全部落在配置 allowlist 与对应请求窗口；未发生跨领域写入。

### 截图矩阵

10 张截图均已人工回读：亮色、中文、单视口、无空白或挤压面板，尺寸统一为 `1455×1200`。

| 文件 | 内容 | SHA-256 |
| --- | --- | --- |
| `01-story-library.png` | 故事库与新建入口 | `9e43a3cc346f6f9aa032aa0a5cce37e8455a32547b3cc843e4a021d2fa9c7a22` |
| `02-source-picker.png` | 未预选的来源选择器 | `0ac68b3410a65896edde30e515facd641fd44f6429e84a632f1a2a062e808e17` |
| `03-source-preview.png` | UI 显式来源选择 | `d5da441c6419f2833b1c6764e969c8bc8ba167f0f99c2e38efc16fb1d2ecccf1` |
| `04-generated-draft.png` | 证据化结构草稿 | `a80a92e88ba2a327d28505ae3d5cf7b48dacaebbd5952edea83a71a533067213` |
| `05-confirmation.png` | 人工编辑与最终确认点 | `d5895b54da893668082db774ab20d6fd4aa0d8fb2d5e4b29b694ad3abad254ba` |
| `06-history.png` | 重开的冻结版本历史 | `60ab1ec5d2d8f020759a025bcc9bd1de545dbc7c364c1693642744a6b2b6254a` |
| `07-source-changed.png` | 来源变化只读提示 | `3ca3f7c55e97ceed2c02e42454c95f7fbaee143024252643f4d3f9ba9623e2d4` |
| `08-pilot-entry.png` | 可见的 Pilot 主动入口 | `4a516066ee65335529cdbd8bba549b2f5045e1a46afd8db906147c6ca0608264` |
| `09-pilot-source-choice.png` | Pilot 显式来源选择 | `f5f45d8962c9f3e8856de23801f6c887238415c217a61bc00cb18b212861e8bc` |
| `10-pilot-history.png` | Pilot 确认结果与独立历史 | `abde2b52abe4dc8404c035ff450513028a5118762cc50c4fa80c33dab0bbd8ca` |

## 清理、破坏性变化与剩余风险

- `cleanup-audit.json` 证明 browser auditor、专用 Chromium、隔离服务和 Provider 代理均已退出；临时 SQLite、浏览器 profile 和隔离数据目录均已删除，端口已释放。
- 破坏性变化：无。迁移 `0019_interview_story_library` 为新增表；未删除或改写既有领域数据。
- Story Usage 第一阶段未建表、未预留字段、未提供入口。
- 剩余风险：真实 Provider 仍是外部依赖，偶发网络未知结果不能完全消除；当前实现通过租约、fencing、原 key 有界恢复和严格证据校验避免重复写入或把未知结果误报为成功。
- 独立只读复审在产品收口后未发现 P0/P1/P2；本轮随后仅修改验收脚本对 CONNECT 隧道的判定，并由 23 项 harness 回归及最终真实 CDP 通过验证。
