import { useEffect, useState } from 'react';
import { Button } from 'antd';
import { t, useLocale } from '@/i18n';
import { listAdaptivePracticePlans } from '@/services/adaptiveInterviewPractice';
import { getEventReadinessFeedback } from '@/features/reviewReadiness/service';
import type { AdaptivePracticeFocus, AdaptivePracticePlan } from '@/types/adaptiveInterviewPractice';
import type { ReadinessFeedbackItem } from '@/features/reviewReadiness/contracts';
import styles from './InterviewPracticeHome.module.css';

export type InterviewPracticeMode = 'quick' | 'review';
export type InterviewPracticeHomeFocus = Omit<AdaptivePracticeFocus, 'ownerGeneration'>;
export interface InterviewPracticeHomeProps {
  target?: { applicationId: number; eventId: number; label: string };
  onOpenPractice?: (mode: InterviewPracticeMode) => void;
  onOpenFocus?: (focus: InterviewPracticeHomeFocus) => void;
  onOpenQuestions?: () => void;
  dueCount?: number;
  dueLoading?: boolean;
  dueError?: boolean;
}

/** Read-only entry projection. Writing, models and voice remain with their existing task owners. */
export default function InterviewPracticeHome({ target, onOpenPractice, onOpenFocus, onOpenQuestions, dueCount, dueLoading, dueError }: InterviewPracticeHomeProps) {
  useLocale();
  const [plans, setPlans] = useState<AdaptivePracticePlan[]>([]);
  const [plansState, setPlansState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [feedback, setFeedback] = useState<{ eventId: number; items: ReadinessFeedbackItem[] } | null>(null);
  const [feedbackError, setFeedbackError] = useState(false);
  useEffect(() => {
    let current = true;
    listAdaptivePracticePlans().then((items) => {
      if (!current) return;
      if (!Array.isArray(items)) throw new Error('invalid_plans');
      setPlans(items); setPlansState('ready');
    }).catch(() => { if (current) setPlansState('error'); });
    return () => { current = false; };
  }, []);
  useEffect(() => {
    let current = true;
    setFeedback(null); setFeedbackError(false);
    if (target) void getEventReadinessFeedback(target.applicationId, target.eventId).then((result) => {
      if (!current) return;
      if (result.application_id !== target.applicationId || result.event_id !== target.eventId) throw new Error('readiness_target_mismatch');
      setFeedback({ eventId: target.eventId, items: result.items });
    }).catch(() => { if (current) setFeedbackError(true); });
    return () => { current = false; };
  }, [target?.applicationId, target?.eventId]);
  const continuing = plans.find((plan) => plan.status === 'in_progress' && plan.practice_state === 'in_progress');
  const continueFocus = continuing?.origin_contract === 'confirmed_readiness_signal_v1'
    && continuing.readiness_signal_version_id && continuing.target_application_event_id
    ? { signalVersionId: continuing.readiness_signal_version_id, targetEventId: continuing.target_application_event_id }
    : null;
  const focusedItems = feedback?.eventId === target?.eventId
    ? feedback?.items.filter((item) => item.state === 'available' && ['not_started', 'in_progress'].includes(item.practiceState)) ?? []
    : [];
  const validDueCount = typeof dueCount === 'number' && Number.isSafeInteger(dueCount) && dueCount >= 0;
  return <section data-testid="free-practice-workspace" aria-labelledby="free-practice-title" className={styles.home}>
    <h3 id="free-practice-title">{t("现在练习")}</h3>
    <p>{t("快速模拟、已确认复盘重点和题库复习各自保留记录；选择入口不会自动调用模型。")}</p>
    <div className={styles.grid}>
      <article><h4>{t("继续练习")}</h4>
        {plansState === 'loading' ? <p role="status">{t("正在读取练习记录…")}</p> : plansState === 'error' ? <p role="alert">{t("练习记录暂时无法读取。")}</p> : continuing ? <>
          <p>{continuing.title}</p><Button disabled={continueFocus ? !onOpenFocus : !onOpenPractice} onClick={() => continueFocus ? onOpenFocus?.(continueFocus) : onOpenPractice?.('review')}>{t("继续上次练习")}</Button>
        </> : <p>{t("还没有进行中的复盘练习。")}</p>}
      </article>
      <article><h4>{t("快速模拟")}</h4><p>{t("使用岗位描述和简历独立练习，不创建虚假投递或面试事件。")}</p>
        <Button type="primary" disabled={!onOpenPractice} onClick={() => onOpenPractice?.('quick')}>{t("快速模拟")}</Button>
      </article>
      <article><h4>{t("复盘重点练习")}</h4>
        {target ? <p>{t("近期目标：{label}", { label: target.label })}</p> : <p>{t("从面试准备中选择已确认重点和真实目标；已有练习仍可继续。")}</p>}
        {feedbackError ? <p role="alert">{t("已确认重点暂时无法读取。")}</p> : null}
        {focusedItems.map((item) => <div key={item.versionId}><p>{item.title}</p><Button disabled={!onOpenFocus} onClick={() => target && onOpenFocus?.({ signalVersionId: item.versionId, targetEventId: target.eventId })}>{t("练习这个重点")}</Button></div>)}
        <Button disabled={!onOpenPractice} onClick={() => onOpenPractice?.('review')}>{t("复盘重点练习")}</Button>
      </article>
      <article><h4>{t("到期题目")}</h4>
        <p role={dueError ? 'alert' : 'status'}>{dueLoading ? t("正在读取到期题目…") : dueError ? t("到期题目暂时无法读取。") : validDueCount ? t("{count} 道到期题", { count: dueCount }) : t("进入题库查看到期题目。")}</p>
        <Button disabled={!onOpenQuestions} onClick={onOpenQuestions}>{t("复习到期题")}</Button>
      </article>
    </div>
  </section>;
}
