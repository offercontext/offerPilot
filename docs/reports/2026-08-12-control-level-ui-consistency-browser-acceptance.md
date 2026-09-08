# 控件级 UI 一致性浏览器验收

日期：2026-08-12

分支：`fix/20260812-story-history-polish`

## 验收范围

- 使用现有本地配置静默复制到隔离临时目录，未输出、修改或保存密钥。
- 使用中文合成案例：候选人筱哲、云栖智能高级后端工程师、中文 JD 与面试日程。
- 亮色模式、宽屏浏览器逐页检查面试索引、面试准备、日程表单与 AI 设置。
- 仅验证只读展示、导航、展开与控件状态；未发起 Provider 调用、未保存设置、未创建业务记录。

## 结果

- 四个页面的按钮、表单、列表、状态卡、证据区和空状态视觉语言一致。
- 面试准备中的原生 select、textarea、checkbox 和 button 已与 Ant 控件对齐，并保留禁用、悬停和键盘焦点状态。
- 面试列表在 390 px 窄视口下无页面横向滚动；操作区 `flex-wrap: wrap`，边界保持在列表行内。
- Command Palette 具备 combobox/listbox/option 语义，键盘选中项通过 `aria-activedescendant` 与 `aria-selected` 同步。
- 浏览器控制台错误：0。
- 临时服务、浏览器标签和隔离数据在验收后清理。

## 截图证据

| 文件 | 尺寸 | SHA-256 |
| --- | --- | --- |
| `01-interview-workflow-surface.png` | 1455×1200 | `9150c263d71d456ab2d6fa81e3afe9cfd268a8a37c8922ccadb46d38eda8a516` |
| `02-interview-preparation-controls.png` | 1455×1200 | `583f8870b93d1c2d5f595f974d7d33ef5886a92e22546921d129a5c256bb2e75` |
| `03-schedule-form-controls.png` | 1455×1200 | `7c4e6a786c2a4e12ac0638684e1141a74b9eaf8635a2be5dbe3dd5a2141f5877` |
| `04-ai-settings-controls.png` | 1440×1188 | `f39ccdb3a4356e028ce399fbe476da2bcf3e399868d569765e10e7093538e72f` |

截图目录：`artifacts/2026-08-12-control-level-ui-consistency/`

## 边界

- 本次仅调整控件级样式和可访问性，不改变既有页面布局、API、数据库、AI 契约或业务流程。
- 全量前端测试仍会输出仓库既有 React `act()` 警告；本次未新增浏览器控制台错误。
