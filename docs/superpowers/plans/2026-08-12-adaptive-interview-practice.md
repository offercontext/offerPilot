# 自适应面试练习测试先行实施计划

## 0. 基线与范围

1. 固定当前基线 `95f48a4`，保存到系统临时 locator，后续范围检查只读取该值。
2. 允许修改：
   - `src/offerpilot/models.py`
   - `src/offerpilot/db.py`
   - `src/offerpilot/schemas.py`
   - `src/offerpilot/api.py`
   - `src/offerpilot/repositories/adaptive_interview_practice.py`
   - `tests/test_adaptive_interview_practice*.py`
   - `web/src/types/adaptiveInterviewPractice.ts`
   - `web/src/services/adaptiveInterviewPractice.ts`
   - `web/src/components/AdaptiveInterviewPractice*.tsx`
   - `web/src/components/AdaptiveInterviewPractice*.css`
   - `web/src/components/InterviewV01View*`
   - `web/src/components/QuestionBankView*`
   - `web/src/layout/AppShell.tsx`
   - `web/src/layout/AppShell.adaptiveInterviewPractice.test.tsx`
   - 本设计、计划、发布报告和浏览器截图目录。
3. 禁止修改 AI/Provider、Question repository、Knowledge、Story Usage、Application 状态逻辑。

## 1. 推荐规则（RED → GREEN）

1. 先写 Repository/纯函数测试：四类 evidence path 映射、稳定排序、无 evidence/非法 source 拒绝、已开始 focus 排除、删除资源过滤。
2. 运行测试，确认因模块缺失失败。
3. 实现最小的冻结候选构造和来源状态派生，再运行到绿色。

## 2. 数据模型与迁移（RED → GREEN）

1. 先写迁移测试，要求 fresh database 与已有 `0019/0020` 数据库均出现 `adaptive_practice_plans` 和 `0021_adaptive_interview_practice`。
2. 先写模型约束测试：start key/来源 focus 唯一、completion key 唯一、状态与 revision 默认值。
3. 确认 RED 后添加模型和 additive migration，跑到绿色。

## 3. 开始与完成生命周期（RED → GREEN）

1. 先写 Repository 测试：开始成功、同 key 同输入重放、同 key 改输入冲突、同 focus 不重复、来源变化、归属错误、跨投递不可见。
2. 先写完成测试：revision CAS、空回答、完成幂等、同 key 改输入冲突、已完成不可覆盖、冻结内容不变、读取派生 current/changed/missing。
3. 用短 `BEGIN IMMEDIATE` 事务实现；不引入 Provider/lease。

## 4. API（RED → GREEN）

1. 先写四个路由的 API 测试及稳定错误码测试。
2. 注入会抛错的 ChatModel，断言所有练习接口仍成功且 Provider 调用为 0。
3. 实现 schemas、repository 注入、JSON mapper 和路由。
4. 补 smoke：建立一条建议、开始、完成、历史回读，并断言 Question/Knowledge/Story/Application 状态无写入。

## 5. 前端 service 与组件（RED → GREEN）

1. 先写类型和 service 契约测试，覆盖错误码与请求字段。
2. 先写 `AdaptiveInterviewPracticeWorkspace` 真实交互测试：推荐、确认开始、填写、完成、历史、来源变化、未知结果同 key 重试、零 AI/导航副作用。
3. 实现组件和样式，使用双栏宽屏/单栏窄屏、明确视觉层级与 40px 点击区。
4. 先写 `InterviewV01View` 推荐卡挂载测试，再接入推荐查询和导航回调。
5. 先写 `QuestionBankView` 页签/聚焦测试，再接入复盘训练页签。
6. 先写真实 AppShell 导航挂载测试，再增加焦点状态与回调；不改变现有默认题库入口。

## 6. 定向验证与复审

1. 运行新增后端/前端测试、Ruff、Mypy、TypeScript、生产构建和 `git diff --check`。
2. 机器化检查 baseline 到 HEAD 的文件均在 allowlist，且 AI/Provider、Question repository、Knowledge、Story Usage 文件未改变。
3. 发起独立 CR，修复所有 P0/P1/P2 后重新运行定向矩阵。

## 7. 浏览器与截图

1. 构建亮色中文隔离数据：候选人“筱哲”、一条已保存复盘和证据化 practice focus。
2. 真实浏览器依次完成：面试首页查看建议 → 显式确认开始 → 填写练习 → 完成 → 关闭重开历史。
3. 审计本地请求及数据库写入，断言 Provider/AI、Question、Knowledge、Story、Application 状态写入为 0。
4. 保存至少 1455×1200 的三张单视口截图并逐张回读：
   - `01-interview-recommendation.png`
   - `02-adaptive-practice-active.png`
   - `03-adaptive-practice-completed.png`
5. 在 `docs/reports/2026-08-12-adaptive-interview-practice-release-verification.md` 记录截图尺寸、hash、请求审计和清理结果。

## 8. 完整门禁与交付

1. 后端采用现有 Windows 五组 manifest/aggregate 门禁，校验 node ID 无重复、并集完整、skip 仅允许既定 symlink 权限项。
2. 前端采用现有十组 source-fingerprint aggregate 门禁；过期则重跑全部分组。
3. 运行 Ruff、Mypy、TypeScript、生产构建、local smoke、local verify。
4. 本功能 Provider 0 调用；不为本功能额外执行 real-AI。若发布基线要求全量 real-AI，则如实记录外部 Provider 结果，不将其混作本功能失败。
5. 独立提交报告，确认工作区干净后交付，不推送、不合并。
