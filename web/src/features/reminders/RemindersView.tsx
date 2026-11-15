import { pipelineActionId } from '@/lib/pipelineInsights';
import { t, useLocale, getLocale } from '@/i18n';
import ProactiveInbox from '@/components/ProactiveInbox';
import { useReminderCandidates } from '@/lib/useReminderCandidates';
import JobMailInbox from '@/features/jobMail/JobMailInbox';
import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Input, Select } from 'antd';
import dayjs from 'dayjs';
import { listApplications } from '@/services/applications';
import { listEvents } from '@/services/events';
import { listOffers } from '@/services/offers';
import { getPracticeStats } from '@/services/questions';
import {
  type ActionCommand,
  type PipelineInsight,
  type PipelineInsightKind,
  type PipelinePriority,
} from '@/lib/pipelineInsights';
import { deriveActionHints } from '@/lib/actionHints';
import ActionDetailDrawer from '@/features/pipeline/ActionDetailDrawer';
import type { ViewMode } from '@/layout/navigation';
import styles from './reminders.module.css';

interface Props {
  onNavigate: (v: ViewMode) => void;
  onOpenDetailById: (id: number) => void;
}

const GROUPS: { key: PipelinePriority; label: string }[] = [
  { key: 'p0', get label() { return t("今日紧急"); } },
  { key: 'p1', get label() { return t("本周重点"); } },
  { key: 'p2', get label() { return t("跟进队列"); } },
];

function defineKindFilterOptions<const Options extends readonly { value: PipelineInsightKind | 'all'; label: string }[]>(
  options: Options &
    (Exclude<PipelineInsightKind, Exclude<Options[number]['value'], 'all'>> extends never ? unknown : never),
) {
  return options;
}

const KIND_FILTER_OPTIONS = defineKindFilterOptions([
  { value: 'all', get label() { return t("全部行动"); } },
  { value: 'offer_deadline', get label() { return t("Offer 截止"); } },
  { value: 'application_deadline', get label() { return t("截止提醒"); } },
  { value: 'interview_review', get label() { return t("面试复盘提醒"); } },
  { value: 'event_upcoming', get label() { return t("日程临近"); } },
  { value: 'interview_soon', get label() { return t("面试临近"); } },
  { value: 'stale_application', get label() { return t("投递待跟进"); } },
  { value: 'no_next_event', get label() { return t("缺少下一步"); } },
  { value: 'material_kit_incomplete', get label() { return t("材料待完善"); } },
  { value: 'question_due', get label() { return t("题目待复习"); } },
  { value: 'pipeline_bottleneck', get label() { return t("流程瓶颈"); } },
  { value: 'weekly_goal_gap', get label() { return t("本周目标差距"); } },
] satisfies { value: PipelineInsightKind | 'all'; label: string }[]);

type DetailAction = ActionCommand & { id?: string };
type DetailInsight = PipelineInsight & {
  primaryAction: DetailAction;
  secondaryActions?: DetailAction[];
};

const getActionId = pipelineActionId;

function findInsightAction(item: PipelineInsight, actionId: string): DetailAction {
  const detail = item as DetailInsight;
  const actions = [detail.primaryAction, ...(detail.secondaryActions ?? [])];
  return (
    actions.find((action, index) => getActionId(item, action, index === 0 ? 'primary' : 'secondary') === actionId) ??
    detail.primaryAction
  );
}

export default function RemindersView({ onNavigate, onOpenDetailById }: Props) {
  useLocale();
  const [now, setNow] = useState(() => dayjs());
  const [selectedInsightId, setSelectedInsightId] = useState<string | null>(null);
  const [kind, setKind] = useState<PipelineInsightKind | 'all'>('all');
  const [keyword, setKeyword] = useState('');

  useEffect(() => {
    const id = window.setInterval(() => setNow(dayjs()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  const { data: rawApps = [] } = useQuery({ queryKey: ['applications'], queryFn: () => listApplications() });
  const { data: rawEvents = [] } = useQuery({ queryKey: ['events'], queryFn: () => listEvents() });
  const { data: rawOffers = [] } = useQuery({ queryKey: ['offers'], queryFn: () => listOffers() });
  const reminderCandidatesQ = useReminderCandidates();
  const practiceStatsQ = useQuery({
    queryKey: ['questions', 'stats'],
    queryFn: () => getPracticeStats(),
    retry: false,
  });

  // Backend serializes empty []T as JSON `null`; coalesce to [] for safe iteration.
  const apps = rawApps ?? [];
  const events = rawEvents ?? [];
  const offers = rawOffers ?? [];

  const insights = useMemo(
    () => deriveActionHints({ apps, events, offers, practiceStats: practiceStatsQ.data, reminderCandidates: reminderCandidatesQ.isError ? [] : reminderCandidatesQ.data, now }),
    [apps, events, offers, practiceStatsQ.data, reminderCandidatesQ.data, reminderCandidatesQ.isError, now, getLocale()],
  );

  const filteredInsights = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return insights.filter((item) => {
      if (kind !== 'all' && item.kind !== kind) return false;
      if (!normalizedKeyword) return true;

      const searchable = [
        item.title,
        item.reason,
        item.primaryAction.label,
        ...(item.evidence ?? []),
      ]
        .join(' ')
        .toLowerCase();
      return searchable.includes(normalizedKeyword);
    });
  }, [insights, keyword, kind]);

  const selectedInsight = useMemo(
    () => insights.find((item) => item.id === selectedInsightId) ?? null,
    [insights, selectedInsightId],
  );

  useEffect(() => {
    if (selectedInsightId && !selectedInsight) {
      setSelectedInsightId(null);
    }
  }, [selectedInsight, selectedInsightId]);

  const runInsightAction = (item: PipelineInsight, actionId: string) => {
    const action = findInsightAction(item, actionId);
    const appId = action.appId ?? item.appId;

    setSelectedInsightId(null);
    if (action.target === 'board' && appId) {
      onOpenDetailById(appId);
      return;
    }
    onNavigate(action.target);
  };

  if (selectedInsight) {
    return (
      <ActionDetailDrawer
        insight={selectedInsight}
        open={!!selectedInsight}
        onClose={() => setSelectedInsightId(null)}
        onRunAction={runInsightAction}
      />
    );
  }

  return (
    <div className={styles.wrap}>
      <JobMailInbox onNavigate={onNavigate} onOpenDetailById={onOpenDetailById} />
      <ProactiveInbox representedCandidateIds={filteredInsights.filter((item) => item.candidate).map((item) => item.id)} />
      {reminderCandidatesQ.isPending && <div role="status">{t("正在加载提醒")}</div>}
      {reminderCandidatesQ.isError && <div role="alert">
        {t("提醒加载失败，请重试")}
        <button type="button" onClick={() => void reminderCandidatesQ.refetch()}>{t("重试")}</button>
      </div>}
      <div className={styles.toolbar}>
        <Input.Search
          allowClear
          placeholder={t("搜索流程行动")}
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          onSearch={setKeyword}
        />
        <Select value={kind} options={KIND_FILTER_OPTIONS.map((option) => ({ ...option }))} onChange={setKind} aria-label={t("筛选行动类型")} />
      </div>

      {filteredInsights.length === 0 ? (
        reminderCandidatesQ.isPending || reminderCandidatesQ.isError ? null : (
        <div className={styles.empty}>
          {t("暂无匹配的流程行动。可以新增投递、复习到期题目，或整理一次复盘来保持节奏。")}</div>
        )
      ) : (
        GROUPS.map(({ key, label }) => {
          const items = filteredInsights.filter((item) => item.priority === key);
          if (items.length === 0) return null;
          return (
            <div key={key} className={styles.group}>
              <div className={styles.groupTitle}>
                {label} ({items.length})
              </div>
              {items.map((item, i) => (
                <button
                  key={item.id}
                  type="button"
                  className={styles.item}
                  style={{ animationDelay: `${i * 40}ms` }}
                  onClick={() => setSelectedInsightId(item.id)}
                >
                  <span className={`${styles.dot} ${styles[item.priority]}`} aria-hidden />
                  <span className={styles.body}>
                    <span className={styles.title}>{item.title}</span>
                    <span className={styles.detail}>{item.reason}</span>
                  </span>
                  <span className={styles.primaryAction}>{item.primaryAction.label}</span>
                </button>
              ))}
            </div>
          );
        })
      )}
    </div>
  );
}
