import { t } from '@/i18n';
import type { ApplicationStatus } from '@/types/application';
import type { EventLifecycleV1 } from '@/features/interviewEvents/eventLifecycle';

export interface ApplicationWorkspaceStage {
  label: string;
  primaryActionLabel: string;
  action: 'materials' | 'followup' | 'written-test' | 'interview-prepare' | 'interview-review' | 'offer' | 'outcome' | 'none';
}

export function getApplicationWorkspaceStage(
  status: ApplicationStatus,
  options: { lifecycle?: EventLifecycleV1; hasCompletedInterview?: boolean; hasInterviewReview?: boolean } = {},
): ApplicationWorkspaceStage {
  switch (status) {
    case 'pending':
      return { label: t("准备投递"), primaryActionLabel: t("完善投递材料"), action: 'materials' };
    case 'applied':
      return { label: t("已投递"), primaryActionLabel: t("设置跟进时间"), action: 'followup' };
    case 'written_test':
      return { label: t("笔试"), primaryActionLabel: t("准备笔试"), action: 'written-test' };
    case 'interview':
      if (options.lifecycle === 'cancelled' || options.lifecycle === 'unknown') {
        return { label: t("已约面试"), primaryActionLabel: t("暂无可用操作"), action: 'none' };
      }
      return (options.lifecycle === 'completed' || (options.lifecycle === undefined && options.hasCompletedInterview === true))
        ? {
            label: t("面试结束"),
            primaryActionLabel: options.hasInterviewReview ? t("查看本轮复盘") : t("完成面试复盘"),
            action: 'interview-review',
          }
        : { label: t("已约面试"), primaryActionLabel: t("准备本轮面试"), action: 'interview-prepare' };
    case 'offer':
      return { label: t("已获 Offer"), primaryActionLabel: t("查看 Offer 与截止时间"), action: 'offer' };
    case 'closed':
      return { label: t("已结束"), primaryActionLabel: t("记录结果与经验"), action: 'outcome' };
  }
}
