# 简历、JD 分析与投递材料地图

[返回业务总图](README.md) · [投递与 JD 版本](applications.md) · [岗位匹配](opportunity-offers.md)

核对基线：2026-10-28，`454af3d7`。此页记录实现入口；产品依据仍按 [AGENTS §4](../../../AGENTS.md#4-事实源) 读取。

## 1. 先区分材料对象

| 用户说法 | 对象与边界 | 页面 / 请求入口 |
|---|---|---|
| 主简历、复制、岗位版本 | `Resume`，通过 parent_resume_id 表示派生关系 | [ResumeLibraryView](../../../web/src/components/ResumeLibraryView.tsx)、[resumes service](../../../web/src/services/resumes.ts) |
| PDF 导入、原文分类 | 上传与解析；可选模型结构化预览及用户确认是另外两步 | [ResumeUploadModal](../../../web/src/components/ResumeUploadModal.tsx)、[ResumeImportReview](../../../web/src/components/ResumeImportReview.tsx) |
| 简历体检、补充事实、版本对比 | 前端可观察事实检查、创建副本后写入、只读结构差异 | 下文第 3 节 |
| JD 分析、简历与 JD 匹配 | `JDAnalysis` / 简历匹配记录；不是岗位匹配 V2 的 triage/deep_review | [ai service](../../../web/src/services/ai.ts)、[ai/workflows.py](../../../src/offerpilot/ai/workflows.py) |
| 投递材料包、改写建议、确认已提交 | `MaterialKit`、`MaterialRevisionProposal`、`EvidenceBundle`；三个独立生命周期 | [MaterialKitDrawer](../../../web/src/components/MaterialKitDrawer.tsx)、[MaterialProposalReviewModal](../../../web/src/components/MaterialProposalReviewModal.tsx) |

## 2. 简历与分析执行链

HTTP 装配在 [api.py](../../../src/offerpilot/api.py)：`upload_resume` 负责 PDF 输入与保存，`preview_resume_structure` 读取原文并冻结指纹，调用 [AI 分类](../../../src/offerpilot/ai/resume_structured_import.py)；模型返回后再核对来源。`confirm_resume_structure` → [ResumesRepository.merge_structured_import](../../../src/offerpilot/repositories/resumes.py) → [apply_structured_fields](../../../src/offerpilot/resume_structured_import.py)，只填空字段/空集合，不覆盖已有用户结构内容。

`ResumesRepository.create/update/copy/delete` 管理主标记、软删除和派生关系；公开 HTTP `delete_resume` 还禁止删除非空主简历。不能仅凭 Repository 有接任逻辑，就认定 UI/API 允许删除所有主简历。

`/api/jd/analyze`、`/api/resumes/{resume_id}/match` → [workflows](../../../src/offerpilot/ai/workflows.py) 的 `resolve_jd_input` / `analyze_jd` / `match_resume`。关联投递时使用 JD 版本；[JDAnalysesRepository.create_for_current](../../../src/offerpilot/repositories/jd.py) 在保存前复核当前版本。诊断分别保留原文版本、分析结果和匹配结果，不能互相当作事实源。

## 3. 不经过独立模型接口的前端业务

| 功能 | 关键符号 | 失败 / 恢复边界与测试 |
|---|---|---|
| 事实体检 | [auditResume](../../../web/src/lib/resumeEvidenceAudit.ts) → [ResumeEvidenceAuditPanel](../../../web/src/components/ResumeEvidenceAuditPanel.tsx) | present/review/unknown 是可观察字段判断，不评判真实性或 PDF 视觉版式；[规则测试](../../../web/src/lib/resumeEvidenceAudit.test.ts) |
| 用户补充事实 | [ResumeFactSupplementWorkspace.handleSubmit](../../../web/src/components/ResumeFactSupplementWorkspace.tsx) → `copyResume` → `updateResume`；[applyResumeFactSupplement](../../../web/src/lib/resumeFactSupplement.ts) | 先复制后写入；两个请求不构成一个事务。复制/保存未知时冻结，回库核对；来源不符不把旧文本写入副本；[交互测试](../../../web/src/components/ResumeFactSupplementWorkspace.test.tsx) |
| 简历版本比较 | [diffResumeContent](../../../web/src/lib/resumeVersionDiff.ts) → [ResumeVersionCompareDrawer](../../../web/src/components/ResumeVersionCompareDrawer.tsx) | 只读比较用户选定快照；不自动合并或评价优劣；[差异测试](../../../web/src/lib/resumeVersionDiff.test.ts)、[谱系](../../../web/src/features/materialSurfaces/resumeLineage.ts) |

依据：[简历事实工作台](../../superpowers/specs/2026-08-12-resume-fact-workspace-design.md)、[结构化导入设计](../../superpowers/specs/2026-09-07-resume-structured-import-design.md)、[版本比较设计](../../superpowers/specs/2026-08-06-resume-version-review-design.md)。

## 4. 材料包、改写和提交证据

| 阶段 | 前端 → HTTP → 业务实现 | 必须观察的边界 |
|---|---|---|
| 生成 / 编辑材料包 | [materialKits](../../../web/src/services/materialKits.ts) → `generate_application_material_kit` / `update_material_kit` → [MaterialKitsRepository](../../../src/offerpilot/repositories/material_kits.py) | 绑定的当前 JD 校验 `_require_current_jd`；保存包不等于发送材料 |
| 生成改写 | [materialRevisionProposals](../../../web/src/services/materialRevisionProposals.ts) → `create_material_revision_proposal` → [build_source_snapshot / create_generated](../../../src/offerpilot/repositories/material_revision_proposals.py) → [validate_material_proposal](../../../src/offerpilot/ai/material_proposals.py) | 冻结唯一材料包、关联简历、当前 JD、最新确认证据包；模型校验/有限修复与落库是不同结果 |
| 接受 / 拒绝 | `accept_material_revision_proposal` / `reject_material_revision_proposal` → Repository `accept` / `reject` | 接受只应用用户选中修改并重新检查来源；拒绝不删除原简历，也不能撤回已接受改写 |
| 用户确认已提交 | [evidenceBundles](../../../web/src/services/evidenceBundles.ts) → `preview_application_evidence_bundle` / `confirm_application_evidence_bundle` → [EvidenceBundlesRepository._confirm_once](../../../src/offerpilot/repositories/evidence_bundles.py) | 审阅的 bundle hash 要匹配；证据包、材料状态和 submission_confirmed 事件同事务；这是用户声明，不是系统发送证明 |

依据：[材料改写设计](../../superpowers/specs/2026-07-15-evidence-gated-material-proposal-design.md)、[提交证据包设计](../../superpowers/specs/2026-07-14-application-evidence-bundle-design.md)。归档当时材料另见[投递快照](applications.md#4-归档反馈与沟通草稿)。

## 5. 诊断与回归

先收集 resume/material_kit/proposal/bundle ID、JD 版本、来源指纹、幂等键、选中修改、HTTP error_code。区分模型生成失败、来源过期、写入冲突、提交后响应丢失。

测试入口：[简历 API](../../../tests/test_resumes_api.py)、[结构化导入](../../../tests/test_resume_structured_import.py)、[前端导入](../../../web/src/components/ResumeEditorDrawer.import.test.tsx)、[共享分析 workflow](../../../tests/test_module_workflows.py)、[改写 API](../../../tests/test_material_revision_proposals_api.py)、[改写事务](../../../tests/test_material_revision_proposals_repository.py)、[改写模型契约](../../../tests/test_material_revision_proposals_ai.py)、[证据包前端](../../../web/src/services/evidenceBundles.test.ts)、[材料包确认交互](../../../web/src/components/MaterialKitDrawer.evidenceBundles.test.tsx)。
