# 投递事实档案与结果反馈闭环发布验证

## 结论

`feat/20260812-application-outcome-feedback` 已完成实现与发布级本地验证，可快进合并到本地 `main`。本轮不推送远端。

该功能把一次真实投递中面试官实际看到的简历、JD 和可选材料冻结为不可变档案，并以追加式历史分别保存外部反馈、用户复盘与下一步行动。UI 与 Pilot 复用相同 API、幂等键、CAS 和人工确认语义；Pilot 的两类确认卡均由确定性本地动作生成，Provider 调用数为 `0`。

## 实现边界

- 新增迁移 `0020_application_outcome_feedback`、`ApplicationSubmissionSnapshot` 与 `ApplicationOutcome`；不删除、不改写既有表或历史数据。
- 投递档案保存 Resume、JD 与可选 Material Kit 的完整冻结快照和 hash；后续来源变化只派生 `current / changed / missing` 提示，不覆盖历史。
- 结果记录为 append-only；原始反馈、个人复盘、下次行动分字段保存，不生成录用概率、候选人评分或岗位排名。
- UI 支持直接保存和交给 Pilot 确认；Pilot 确认卡展示完整事实并在确认前不写领域表。
- 未知结果保留原幂等键并冻结输入；确定性失败按稳定错误码清理或恢复。
- 摘要只陈述已记录模式：事实档案数、结果数、推进数、下一步行动数和反馈标签频次。

## 自动化门禁

### 后端

Windows 五组门禁及 aggregate 通过：完整 manifest 为 **2,052** 个唯一 node ID，分组并集与 manifest 一致，无重复或遗漏。

| 组 | 收集 | 通过 | 允许 skip |
| --- | ---: | ---: | ---: |
| agent | 454 | 454 | 0 |
| domain | 73 | 73 | 0 |
| knowledge | 659 | 655 | 4 |
| proposals | 418 | 418 | 0 |
| misc | 448 | 448 | 0 |
| 合计 | 2,052 | 2,048 | 4 |

4 个 skip 均为既定 Windows 符号链接权限用例，node ID 与原因由 aggregate 精确校验。

### 前端

Windows 十组门禁及 aggregate 通过：**119 个测试文件、929 项测试**，实际文件集合、manifest、源码指纹和结果 hash 一致，无 skip/todo 或重复测试 ID。前端源码指纹为 `99761341214c0a1ad24bce43ba9037bac8de90c172198e20e8dd0cb56dd3459c`。

### 静态与集成验证

- `uv run ruff check .`：通过。
- `uv run mypy src`：通过，70 个源码文件无问题。
- `npm.cmd run build`：TypeScript 与 Vite 生产构建通过。
- `uv run oc smoke --static-dir web/dist`：通过。
- `uv run oc verify --profile local --static-dir web/dist`：通过；新增 `http_application_outcome_feedback` 步骤证明 UI 档案幂等重放和 Provider-free Pilot 结果确认。
- `git diff --check`：通过。

## 浏览器验收

使用隔离临时数据目录、亮色中文界面、候选人“筱哲”和 `1455×1200` 单视口完成：

1. 投递详情打开“投递事实与结果”，选择实际提交的 Resume/JD 并直接冻结档案。
2. 填写真实外部反馈、个人复盘、下一步行动与反馈标签。
3. 从同一表单交给 Pilot，确认卡完整展示关联档案、阶段、结果、时间、标签和三类文本；确认前无领域写入。
4. UI 与 Pilot 各写入一条结果后关闭重开；API 回读为 `1` 份档案、`2` 条结果，来源精确为 `ui,pilot`。
5. 摘要正确显示 2 条推进记录与反馈模式；历史区分 UI/Pilot 来源，并可展开冻结内容。
6. 浏览器控制台错误为 `0`；页面只访问本地应用/API。该能力不需要也未调用真实 Provider。

### 截图矩阵

截图均已人工回读，亮色、中文、宽屏、单视口，尺寸统一为 `1455×1200`。

| 文件 | 内容 | SHA-256 |
| --- | --- | --- |
| `01-ui-submission-archive.png` | UI 冻结实际投递档案 | `580f168ebd40d2a6075f43d9ffb525cb616d72edf6d60692b3e25a7213941fd6` |
| `02-ui-feedback-entry.png` | UI 分离填写原始反馈、复盘与行动 | `f5eca047dee2047ae46603367ad3be539def7517586ddb714ddad2337f71871e` |
| `03-pilot-outcome-confirmation.png` | Pilot 完整人工确认卡 | `2874fd0dd206f11975fcdfa3b827cfd420c43ddcc6ec549a191a1ec5928ba6a1` |
| `04-summary-and-history.png` | 摘要与重复反馈模式 | `fd65be92b5b3b91a7751165333d41f1a1b4eea46bddd51c92ad288ad6fe754b7` |
| `05-ui-pilot-history.png` | UI/Pilot 追加式历史与来源状态 | `9fcbdad5552df3ebd17950dee25f212552655e7b4458a0a64e28c073499502ac` |

截图位于 `artifacts/2026-08-12-application-outcome-feedback/`。

## 清理、破坏性变化与剩余风险

- 临时浏览器服务、端口与隔离数据在验收结束后清理；截图和本报告作为发布证据保留。
- 破坏性变化：无。迁移只新增表和索引；既有投递、Resume、JD、材料、面试、Knowledge、Offer 与 Memory 数据不改写。
- 已知非阻塞项：既有 React/AntD 测试警告、FastAPI lifespan 弃用警告及 npm audit 依赖告警仍存在，本功能未扩大这些问题。
- 本期只记录并展示历史模式，不把结果反馈写入 Knowledge/Memory，不自动改变投递状态，也不据此生成成功率或推荐结论。
