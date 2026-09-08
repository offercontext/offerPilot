# 材料流程中文化设计

## 目标

补齐材料包与简历优化流程的固定界面文案，使用户可见的标题、说明、表单、状态、按钮、加载提示、空状态、证据标签、确认弹窗和错误提示统一使用中文。

本次只改前端展示层，不改变 API、错误码、接口枚举、数据结构、证据门控、人工确认或禁止自动投递行为。

## 范围

覆盖以下组件及其直接渲染的材料流程文案：

- `web/src/components/MaterialKitDrawer.tsx`
- `web/src/components/MaterialProposalReviewModal.tsx`

固定产品文案中的 `Resume` 统一显示为“简历”；`JD` 可以保留。材料提案的证据来源只有 `resume`、`evidence_bundle`、`user_assertion` 三种：分别显示为“简历”“已确认的投递证据快照”“用户断言”。材料提案证据来源不包含 JD，不能把 `evidence_bundle` 误标为岗位描述。证据路径和证据摘录不翻译。

以下内容保留原文：

- 用户粘贴的 JD
- 公司名、职位名和简历标题
- 用户输入的候选人断言
- 模型生成的建议正文、修改前后内容和修改理由
- 证据路径与证据摘录

提案的 `summary` 属于模型生成内容，但当 `changes` 为空时不直接渲染它。此时只显示固定中文空状态“当前没有可由证据安全支持的简历改写建议”，避免把模型返回的英文空变更句子展示给用户。只有存在至少一条 change 时才展示 summary。

## 实现方案

新增材料流程专用文案模块，例如 `web/src/components/materialFlowCopy.ts`，集中维护固定文案和来源标签映射。该模块不接入全局国际化系统，不改变服务层或后端契约。

组件通过文案模块读取：

- 材料包工作区标题、字段标签、占位提示、状态标签、按钮、加载/空状态和证据历史文案
- 简历优化提案弹窗标题、人工确认提示、变更字段标签、证据来源标签、接受/拒绝按钮和二次确认文案
- 服务端错误码和 HTTP 状态的可理解中文提示
- 无障碍标签、按钮 aria-label 和加载提示

动态值使用插值传入文案函数或放在中文固定标签之后，避免翻译动态数据。

## 错误与安全边界

`material_proposal_unverifiable`、HTTP 409 和其他服务端错误仍按原错误码和状态处理；仅将用户可见文本映射为中文。两个组件均禁止直接展示 `response.data.error`、Axios 的错误消息或 `Error.message`。错误处理只能根据安全错误码或 HTTP 状态选择固定中文文案；未识别的错误统一显示固定中文兜底提示。

提案生成、接受、拒绝和证据确认的请求逻辑不变。中文化不得绕过禁用状态、二次确认、来源指纹冲突或证据预览门控。

## 测试方案

在现有 Vitest 测试上扩展：

- 生成提案：固定按钮、输入提示、人工确认和错误提示为中文；用户断言与模型内容保留原文。
- 无可用改写：当 `changes` 为空时显示中文空状态，不渲染 `proposal.summary`，并断言 `No safe evidence-backed changes are available.` 不在页面中出现。
- 接受/拒绝：中文操作按钮、二次确认弹窗、成功提示和来源冲突提示。
- 证据展示：`resume`、`evidence_bundle`、`user_assertion` 分别显示为“简历”“已确认的投递证据快照”“用户断言”，路径和摘录保持原文。
- 错误提示：已知错误码/HTTP 状态显示固定中文；服务端 error、Axios message、Error.message 等原始文本均不得出现在页面中，未知错误显示统一中文兜底。
- 无障碍：关键按钮、复选框和加载状态使用中文可访问名称或提示。
- 遗留英文扫描：只断言已知固定英文短语不存在，例如 `Generate evidence-gated resume proposal`、`Reject proposal`、`Accept selected changes`、`AI recommendation`、`Confirm new derived resume`、`Create derived resume`、`Source resume`、`User assertion supplied for this proposal`。不扫描任意英文，以免误伤动态 JD、公司名、简历、AI 内容和证据摘录。

验证命令：

```text
cd web
npm.cmd test -- --run
npm.cmd run build
```

## 验收标准

- 材料包和简历优化流程中的固定界面文案不再显示已知遗留英文。
- 动态英文数据仍按原文显示。
- 中文错误提示不显示 Axios 原始错误或内部技术细节。
- 生成、无可用改写、接受、拒绝、来源冲突和证据展示均有回归覆盖。
- 后端测试和 API 行为无需修改。
