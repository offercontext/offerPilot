# Material Flow Chinese Copy Implementation Plan

## Goal

将材料包与简历优化提案流程中的固定界面文案统一为中文，同时保持 API 契约、人工确认、证据门控、来源漂移保护和禁止自动投递行为不变。用户数据、模型内容和证据摘录继续按原文展示。

## Architecture

新增材料流程专用的前端文案模块，集中维护固定标签、状态、按钮和安全错误映射；`MaterialKitDrawer` 与 `MaterialProposalReviewModal` 只通过该模块获取固定文案。错误展示仅依据安全错误码或 HTTP 状态映射，不透传服务端错误、Axios message 或 `Error.message`。空变更提案显示固定中文空状态并跳过模型 summary。

## Tech Stack

React + TypeScript、Ant Design、Axios、Vitest/Testing Library。仅修改材料流程前端组件及其测试，不引入全局国际化，不修改后端或 API 数据结构。

## Implementation Tasks

### 1. 建立材料流程文案与错误映射模块

- [ ] 新增 `web/src/components/materialFlowCopy.ts`。
- [ ] 定义材料流程固定文案，包括标题、说明、表单标签、占位符、加载/空状态、状态标签、证据标签、确认弹窗和无障碍文本。
- [ ] 定义证据来源映射：`resume` 为“简历”、`evidence_bundle` 为“已确认的投递证据快照”、`user_assertion` 为“用户断言”；未知来源返回固定中文兜底，不返回原始枚举值。
- [ ] 定义按安全错误码/HTTP 状态的错误映射，覆盖提案不可验证、来源冲突、资源不存在和未知错误；禁止返回 `response.data.error`、Axios message 或 `Error.message`。

### 2. 先写提案审核回归测试

- [ ] 修改 `web/src/components/MaterialProposalReviewModal.test.tsx`，将固定英文断言替换为中文文案断言。
- [ ] 增加三种证据来源标签测试，并断言证据路径与摘录仍保持原文。
- [ ] 增加 `changes=[]` 测试：显示固定中文“无可用改写”状态，不渲染模型返回的 `No safe evidence-backed changes are available.`。
- [ ] 增加安全错误测试：模拟 409、502/`material_proposal_unverifiable`、未知错误和恶意服务端 error，断言只显示固定中文映射，不显示原始错误文本。
- [ ] 增加已知固定英文短语扫描，只检查材料流程已知短语，不禁止动态英文数据。
- [ ] 运行 `cd web; npm.cmd test -- --run MaterialProposalReviewModal.test.tsx`，确认新增测试在实现前按预期失败。

### 3. 先写材料包抽屉回归测试

- [ ] 修改 `web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx`，覆盖中文按钮、字段标签和断言占位符。
- [ ] 保留并补充断言预校验测试，确保错误提示、按钮禁用和服务未调用行为使用中文固定文案。
- [ ] 增加生成失败测试，模拟后端英文 error 和 Axios 原始错误，断言只显示中文产品提示。
- [ ] 增加生成成功、无可用改写、接受、拒绝和证据展示的固定文案回归断言。
- [ ] 运行 `cd web; npm.cmd test -- --run MaterialKitDrawer.evidenceBundles.test.tsx`，确认实现前测试失败。

### 4. 实现提案审核组件中文化与安全渲染

- [ ] 在 `MaterialProposalReviewModal.tsx` 使用材料流程文案模块，替换标题、说明、证据标签、字段标签、按钮、确认弹窗和 aria-label 中的固定英文。
- [ ] 对空变更提案只渲染固定中文空状态，不渲染 `proposal.summary`；有变更时保留模型 summary 原文。
- [ ] 将审核错误改为安全错误映射，仅检查状态/安全错误码，不读取错误文本用于展示。
- [ ] 保持选中变更、拒绝和接受流程逻辑及 API 请求体不变。

### 5. 实现材料包抽屉中文化与安全错误提示

- [ ] 在 `MaterialKitDrawer.tsx` 使用材料流程文案模块，替换剩余固定英文、断言校验提示、生成成功提示和无障碍文本。
- [ ] 将生成、证据详情和接受失败统一改为安全错误映射；未知错误使用固定中文兜底。
- [ ] 保持用户断言 trim/过滤、`user_assertions` API 字段、动态 JD/简历/公司/证据原文和人工确认行为不变。

### 6. 验证、审查与提交

- [ ] 运行 `cd web; npm.cmd test -- --run`。
- [ ] 运行 `cd web; npm.cmd run build`。
- [ ] 运行 `git diff --check` 与材料流程固定英文短语扫描测试。
- [ ] 检查 `git diff --stat`、`git status --short --branch`，确认无 API/后端改动和无敏感数据。
- [ ] 请求一次子代理代码审查，修复发现的问题或记录剩余风险。
- [ ] 按仓库规范分开执行 `git add` 与 `git commit`，提交信息使用 `fix: AI localize material flow copy`。

## Self-Review Checklist

- [ ] `evidence_bundle` 显示为“已确认的投递证据快照”，没有误标为 JD。
- [ ] 空变更不显示模型英文 summary。
- [ ] 任意错误路径都不展示服务端 error、Axios message 或 `Error.message`。
- [ ] 动态用户数据、模型正文、证据摘录和路径没有被翻译或过滤。
- [ ] 不修改后端接口、枚举、数据结构、证据门控或人工确认行为。
