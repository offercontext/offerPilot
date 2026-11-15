import { t } from '@/i18n';
import type { MaterialEvidenceSource } from '@/types/materialRevisionProposal';

export const MATERIAL_FLOW_COPY = {
  proposal: {
    get title() { return t("AI 建议，仅供人工审核"); },
    get warning() { return t("AI 建议仅供人工审核。在你确认之前，不会修改任何简历。"); },
    get reject() { return t("拒绝提案"); },
    get accept() { return t("接受选中的修改"); },
    get sourceResume() { return t("来源简历"); },
    get jdDirection() { return t("JD 方向"); },
    get generatedAt() { return t("生成时间"); },
    get before() { return t("修改前"); },
    get after() { return t("修改后"); },
    get why() { return t("修改原因"); },
    get userAssertion() { return t("用户断言"); },
    get empty() { return t("当前没有可由证据安全支持的简历改写建议"); },
    get confirmTitle() { return t("确认创建派生简历"); },
    get createDerivedResume() { return t("创建派生简历"); },
    get backToReview() { return t("返回审核"); },
    get confirmBody() { return t("这会创建一个新的派生简历版本，并将投递准备改为指向该版本。不会覆盖来源简历。"); },
    get selectChange() { return t("选择此项变更"); },
  },
  drawer: {
    get materialKitTitle() { return t("投递准备"); },
    get evidenceHistoryTitle() { return t("本次投递记录"); },
    get candidateFactsLabel() { return t("候选人补充事实（每行一条）"); },
    get candidateFactsPlaceholder() { return t("每行填写一条候选人事实"); },
    get generateProposal() { return t("生成基于证据的简历提案"); },
    get proposalValidationTooMany() { return t("最多填写 10 条非空断言"); },
    get proposalValidationTooLong() { return t("每条断言不得超过 500 个字符"); },
    get proposalAccepted() { return t("已根据人工确认创建派生简历"); },
    get confirmationKindUnknown() { return t("其他确认方式"); },
    get evidencePreviewFallback() { return t("材料证据尚未准备完成，请补充必要来源"); },
  },
  surface: {
    get loading() { return t("正在加载投递准备，请稍候"); },
    get missingJd() { return t("补充岗位资料"); },
    get missingResume() { return t("选择本次简历"); },
    get notGenerated() { return t("生成投递准备"); },
    get dirtyDraft() { return t("保存修改"); },
    get waitingConfirmation() { return t("查看并确认"); },
    get resultUnknown() { return t("确认处理结果"); },
    get readyUnsubmitted() { return t("记录已投递"); },
    get submitted() { return t("查看本次投递记录"); },
  },
  errors: {
    get proposalUnverifiable() { return t("AI 输出未通过证据校验，已保护原简历且未创建草稿，请重试"); },
    get proposalSourceConflict() { return t("原始来源已发生变化，请重新生成提案后再审核"); },
    get confirmationSourceConflict() { return t("提交材料已变化，请重新核对"); },
    get aiServiceUnavailable() { return t("AI 服务暂不可用，请稍后重试"); },
    get generalConflict() { return t("操作未完成，请稍后重试"); },
    get notFound() { return t("请求的材料已不存在或不可用，请刷新后重试"); },
    get invalidRequest() { return t("输入内容无法处理，请检查后重试"); },
    get fallback() { return t("操作未完成，请稍后重试"); },
  },
} as const;

const EVIDENCE_SOURCE_LABELS: Record<MaterialEvidenceSource, string> = {
  get resume() { return t("简历"); },
  get evidence_bundle() { return t("本次投递记录"); },
  get user_assertion() { return t("用户断言"); },
};

const EVIDENCE_PREVIEW_ISSUE_LABELS: Record<string, string> = {
  get '缺少投递材料包'() { return t("缺少投递准备"); },
  get '投递材料包不唯一'() { return t("投递准备不唯一，请保留一份有效投递准备"); },
  get '缺少职位描述'() { return t("缺少职位描述，请补充 JD"); },
  get '缺少关联简历'() { return t("缺少关联简历，请先选择简历"); },
  get '关联简历不存在或已删除'() { return t("关联简历不存在或已删除，请重新选择简历"); },
  get '简历内容不是 JSON 对象'() { return t("简历内容格式异常，请重新选择简历"); },
  get '材料包内容不是 JSON 对象'() { return t("材料包内容格式异常，请重新生成材料包"); },
  get '缺少已选择的简历'() { return t("缺少已选择的简历，请先选择简历"); },
};

export function materialEvidenceSourceLabel(source: string): string {
  return EVIDENCE_SOURCE_LABELS[source as MaterialEvidenceSource] || t("未知证据来源");
}

export function materialEvidencePreviewIssueLabels(issues: string[]): string[] {
  const labels = issues.map((issue) => EVIDENCE_PREVIEW_ISSUE_LABELS[issue] || MATERIAL_FLOW_COPY.drawer.evidencePreviewFallback);
  return [...new Set(labels)];
}

function responseDetails(error: unknown): { status?: number; code?: string } {
  if (typeof error !== 'object' || error === null) return {};
  const response = (error as { response?: unknown }).response;
  if (typeof response !== 'object' || response === null) return {};
  const responseRecord = response as { status?: unknown; data?: unknown };
  const data = typeof responseRecord.data === 'object' && responseRecord.data !== null
    ? responseRecord.data as { error_code?: unknown; code?: unknown }
    : undefined;
  const status = typeof responseRecord.status === 'number' ? responseRecord.status : undefined;
  const codeValue = data?.error_code ?? data?.code;
  const code = typeof codeValue === 'string' ? codeValue : undefined;
  return { status, code };
}

export type MaterialFlowErrorContext = 'proposal' | 'confirmation' | 'general';

export function materialFlowErrorKey(
  error: unknown,
  context: MaterialFlowErrorContext = 'general',
): string {
  const { status, code } = responseDetails(error);
  if (code === 'material_proposal_unverifiable') {
    return "AI 输出未通过证据校验，已保护原简历且未创建草稿，请重试";
  }
  if (status === 502 && context === 'proposal') return "AI 服务暂不可用，请稍后重试";
  if (status === 409) {
    return context === 'proposal'
      ? "原始来源已发生变化，请重新生成提案后再审核"
      : context === 'confirmation'
        ? "提交材料已变化，请重新核对"
        : "操作未完成，请稍后重试";
  }
  if (status === 404) return "请求的材料已不存在或不可用，请刷新后重试";
  if (status === 422) return "输入内容无法处理，请检查后重试";
  return "操作未完成，请稍后重试";
}


export function materialFlowErrorMessage(error: unknown, context: MaterialFlowErrorContext = 'general'): string {
  return t(materialFlowErrorKey(error, context));
}

export function isMaterialFlowSourceConflict(error: unknown): boolean {
  return responseDetails(error).status === 409;
}

export function materialConfirmationKindLabel(value: string): string {
  return value === 'user_asserted' ? t("用户确认") : MATERIAL_FLOW_COPY.drawer.confirmationKindUnknown;
}
