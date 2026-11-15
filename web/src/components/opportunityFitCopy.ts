import { t } from '@/i18n';
export type OpportunityFitStatus = 'met' | 'unmet' | 'unknown';

const FIT_STATUS_LABELS: Record<OpportunityFitStatus, string> = {
  get met() { return t("已满足"); },
  get unmet() { return t("未满足"); },
  get unknown() { return t("待确认"); },
};

export const OPPORTUNITY_FIT_COPY = {
  drawer: {
    get title() { return t("岗位决策漏斗"); },
    get description() { return t("先判断是否值得投入，再决定是否准备材料。分析只基于本地 Application、选定简历、用户粘贴 JD 和你的补充断言。"); },
    get history() { return t("历史评估（只读快照）"); },
    get view() { return t("查看"); },
    get resumeLabel() { return t("用于审阅的简历"); },
    get resumePlaceholder() { return t("选择一份简历"); },
    get jdLabel() { return t("用户粘贴的 JD"); },
    get jdSourceLabel() { return t("用户粘贴 JD"); },
    get jdPlaceholder() { return t("只粘贴岗位要求文本；不会抓取链接。"); },
    get assertionsLabel() { return t("本次补充断言（每行一条）"); },
    get assertionsPlaceholder() { return t("例如：我可以在上海办公"); },
    get assertionsHint() { return t("最多 10 条，每条最多 500 字。"); },
    get assertionsTooMany() { return t("最多填写 10 条非空断言。"); },
    get assertionsTooLong() { return t("每条断言最多 500 字。"); },
    get humanConfirmation() { return t("人工确认"); },
    get humanConfirmationDescription() { return t("AI 只生成带证据引用的分析，不会自动接受、投递或访问外部招聘平台。"); },
    get startTriage() { return t("开始快速判断"); },
    get sourceFrozen() { return t("已保留当时版本"); },
    get sourceChanged() { return t("来源已更新，已有结果仍保留"); },
    get triage() { return t("快速判断"); },
    get hardConstraints() { return t("岗位约束"); },
    get fitSignals() { return t("候选人匹配信号"); },
    get gaps() { return t("差距与待确认问题"); },
    get nextQuestions() { return t("截止日期"); },
    get notStated() { return t("未在输入材料中陈述"); },
    get evidenceSources() { return t("证据来源"); },
    get jdOriginal() { return t("JD 原文"); },
    get candidateAssertions() { return t("用户断言（用户提供，未外部核验）"); },
    get deepReview() { return t("深入分析"); },
    get recommendedPath() { return t("建议路径"); },
    get nextActions() { return t("下一步行动"); },
    get prepareMaterials() { return t("去准备材料"); },
    get startDeepReview() { return t("开始深入分析"); },
    get noDirectEvidence() { return t("无直接证据引用"); },
  },
  evidence: {
    get resume() { return t("简历"); },
    get jd() { return t("岗位描述（仅用于分析方向）"); },
    get user_assertion() { return t("用户断言（用户提供，未外部核验）"); },
    get unknown() { return t("未知证据来源"); },
  },
  errors: {
    get unverifiable() { return t("AI 输出未通过证据校验，可重试；原简历已保护，未创建草稿。"); },
    get providerUnavailable() { return t("AI 服务配置或提供方不可用，请检查设置后重试"); },
    get aiServiceUnavailable() { return t("AI 服务暂不可用，请稍后重试"); },
    get generalConflict() { return t("操作未完成，请稍后重试"); },
    get notFound() { return t("请求的岗位评估不存在或不可用，请刷新后重试"); },
    get invalidRequest() { return t("输入内容无法处理，请检查后重试"); },
    get fallback() { return t("操作失败，请稍后重试"); },
  },
  enums: {
    recommendation: {
      get advance() { return t("建议推进"); },
      get hold() { return t("需要澄清"); },
      get decline() { return t("建议放弃"); },
    },
    fitStatus: FIT_STATUS_LABELS,
    gapKind: {
      get required() { return t("必要条件"); },
      get preferred() { return t("优先条件"); },
    },
    recommendedPath: {
      get prepare_materials() { return t("建议准备材料"); },
      get clarify_first() { return t("建议先澄清"); },
      get do_not_pursue() { return t("不建议继续推进"); },
    },
  },
} as const;

function fixedCopyLabel(
  mapping: Readonly<Record<string, string>>,
  value: string,
  fallback: string,
): string {
  return Object.prototype.hasOwnProperty.call(mapping, value) ? mapping[value] : fallback;
}

function responseDetails(error: unknown): { status?: number; errorCode?: string } {
  if (typeof error !== 'object' || error === null) return {};
  const response = (error as { response?: unknown }).response;
  if (typeof response !== 'object' || response === null) return {};

  const responseRecord = response as { status?: unknown; data?: unknown };
  const data = typeof responseRecord.data === 'object' && responseRecord.data !== null
    ? responseRecord.data as { error_code?: unknown }
    : undefined;
  const status = typeof responseRecord.status === 'number' ? responseRecord.status : undefined;
  const errorCode = typeof data?.error_code === 'string' ? data.error_code : undefined;
  return { status, errorCode };
}

export function getOpportunityFitErrorMessage(error: unknown): string {
  const { status, errorCode } = responseDetails(error);

  if (errorCode === 'opportunity_fit_unverifiable') return OPPORTUNITY_FIT_COPY.errors.unverifiable;
  if (errorCode === 'opportunity_fit_provider_error') return OPPORTUNITY_FIT_COPY.errors.providerUnavailable;
  if (status === 404) return OPPORTUNITY_FIT_COPY.errors.notFound;
  if (status === 410) return t("岗位评估确认已过期，请开始新的评估。");
  if (status === 409) return OPPORTUNITY_FIT_COPY.errors.generalConflict;
  if (status === 422) return OPPORTUNITY_FIT_COPY.errors.invalidRequest;
  if (status === 502) return OPPORTUNITY_FIT_COPY.errors.aiServiceUnavailable;
  return OPPORTUNITY_FIT_COPY.errors.fallback;
}

export function opportunityFitEvidenceLabel(source: string): string {
  return fixedCopyLabel(OPPORTUNITY_FIT_COPY.evidence, source, OPPORTUNITY_FIT_COPY.evidence.unknown);
}

export function opportunityFitRecommendationLabel(value: string): string {
  return fixedCopyLabel(OPPORTUNITY_FIT_COPY.enums.recommendation, value, t("未知建议"));
}

export function opportunityFitRecommendationColor(value: string): 'green' | 'gold' | 'red' {
  if (value === 'advance') return 'green';
  if (value === 'decline') return 'red';
  return 'gold';
}

export function opportunityFitStatusLabel(value: string): string {
  return fixedCopyLabel(FIT_STATUS_LABELS, value, t("未知状态"));
}

export function opportunityFitGapKindLabel(value: string): string {
  return fixedCopyLabel(OPPORTUNITY_FIT_COPY.enums.gapKind, value, t("未知条件"));
}

export function opportunityFitRecommendedPathLabel(value: string): string {
  return fixedCopyLabel(OPPORTUNITY_FIT_COPY.enums.recommendedPath, value, t("未知建议路径"));
}
