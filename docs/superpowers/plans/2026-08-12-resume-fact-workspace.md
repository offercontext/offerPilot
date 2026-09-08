# 简历事实补充工作台实施计划

## 目标

在不新增后端、API、数据库或 AI 调用的前提下，把现有简历事实体检和版本差异审阅连接为可恢复的派生版本工作流。

## 实施顺序

1. 测试先行新增 `resumeFactSupplement.ts`：可操作 finding 判定、严格 Pointer 读取/替换、安全预算与 Unicode 校验。
2. 测试先行新增 `ResumeFactSupplementWorkspace`：事实核对、显式确认、复制/更新两阶段恢复、键盘可访问性。
3. 扩展 `ResumeEvidenceAuditPanel` 与 `ResumeEditorDrawer`，只对可操作 finding 显示入口。
4. 扩展 `ResumeLibraryView`，成功后同步新版本并自动打开现有版本差异 Drawer。
5. 增加真实挂载零 AI/跨领域副作用测试与视觉样式。
6. 运行定向测试、前端全量、TypeScript、生产构建和 `git diff --check`。
7. 启动独立代码复审，修复 P0/P1/P2。
8. 用隔离中文数据和亮色宽屏浏览器完成走查，保存并逐张回读截图，清理服务和临时数据。

## 文件边界

允许修改或新增：

- `web/src/lib/resumeFactSupplement.ts`
- `web/src/lib/resumeFactSupplement.test.ts`
- `web/src/lib/resumeEvidenceAudit.ts`
- `web/src/lib/resumeEvidenceAudit.test.ts`
- `web/src/components/ResumeFactSupplementWorkspace.tsx`
- `web/src/components/ResumeFactSupplementWorkspace.test.tsx`
- `web/src/components/ResumeEvidenceAuditPanel.tsx`
- `web/src/components/ResumeEvidenceAuditPanel.test.tsx`
- `web/src/components/ResumeEditorDrawer.tsx`
- `web/src/components/ResumeEditorDrawer.mount.test.tsx`
- `web/src/components/ResumeLibraryView.tsx`
- `web/src/components/ResumeLibraryView.versionCompare.mount.test.tsx`
- `web/src/components/ResumeLibraryView.module.css`
- 本设计、计划与浏览器验收报告/截图。

禁止修改 `src/offerpilot/**`、`tests/**`、`web/src/services/**`、`web/src/types/**`、JD、Opportunity Fit、材料、面试、Story、Knowledge 和 Pilot 文件。
