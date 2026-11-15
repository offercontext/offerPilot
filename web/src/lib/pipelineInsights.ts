import { t } from '@/i18n';
import dayjs, { type ConfigType } from 'dayjs';
import type { Application, ApplicationStatus } from '@/types/application';
import { EVENT_TYPE_LABELS, type ScheduleEvent } from '@/types/event';
import type { ReminderCandidate } from '@/types/reminders';
import type { Offer } from '@/types/offer';
import type { PracticeStats } from '@/types/question';

export type PipelinePriority = 'p0' | 'p1' | 'p2';
export type PipelineInsightKind =
  | 'offer_deadline'
  | 'application_deadline'
  | 'interview_review'
  | 'interview_soon'
  | 'event_upcoming'
  | 'stale_application'
  | 'no_next_event'
  | 'material_kit_incomplete'
  | 'question_due'
  | 'pipeline_bottleneck'
  | 'weekly_goal_gap';

export type ActionCommandTarget = 'board' | 'calendar' | 'offers' | 'questions';

export interface ActionCommand {
  label: string;
  target: ActionCommandTarget;
  appId?: number;
  offerId?: number;
  eventId?: number;
}

/** Action identity is independent of translated labels. */
export function pipelineActionId(insight: { id: string }, action: ActionCommand & { id?: string }, kind: 'primary' | 'secondary'): string {
  return action.id ?? `${insight.id}:${kind}:${action.target}:${action.appId ?? ''}:${action.offerId ?? ''}:${action.eventId ?? ''}`;
}

export interface PipelineInsight {
  id: string;
  kind: PipelineInsightKind;
  priority: PipelinePriority;
  title: string;
  reason: string;
  evidence: string[];
  primaryAction: ActionCommand;
  sortKey: number;
  appId?: number;
  offerId?: number;
  eventId?: number;
  questionCount?: number;
  candidate?: ReminderCandidate;
}

export interface PipelineHealth {
  score: number;
  label: 'healthy' | 'watch' | 'critical';
  bottleneck: string;
  total: number;
  active: number;
  p0: number;
  p1: number;
  p2: number;
  offers: number;
  interviews: number;
  stale: number;
  dueQuestions: number;
  weeklyTarget: number;
  weeklyApplications: number;
  weeklyGap: number;
}

export interface LegacyActionItem {
  id: string;
  kind: PipelineInsightKind;
  priority: PipelinePriority;
  title: string;
  detail: string;
  primaryActionLabel: string;
  target: ActionCommandTarget;
  sortKey: number;
  appId?: number;
  offerId?: number;
  eventId?: number;
  questionCount?: number;
}

interface MaterialKitState {
  application_id: number;
  complete: boolean;
}

interface DerivePipelineInsightsInput {
  apps: Application[];
  events: ScheduleEvent[];
  offers: Offer[];
  materialKits?: MaterialKitState[];
  reminderCandidates?: ReminderCandidate[];
  practiceStats?: PracticeStats | null;
  weeklyTarget?: number;
  now?: ConfigType;
}

const WAITING_STATUSES: ApplicationStatus[] = ['pending', 'applied', 'written_test'];
const ACTIVE_STATUSES: ApplicationStatus[] = ['pending', 'applied', 'written_test', 'interview', 'offer'];
const PRIORITY_RANK: Record<PipelinePriority, number> = { p0: 0, p1: 1, p2: 2 };
const DATE_ONLY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const DATE_TIME_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?$/;

function isRealCalendarDate(yearText: string, monthText: string, dayText: string): boolean {
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const utc = new Date(Date.UTC(year, month - 1, day));

  return utc.getUTCFullYear() === year && utc.getUTCMonth() === month - 1 && utc.getUTCDate() === day;
}

function isStrictDateOnly(value: string): boolean {
  const match = DATE_ONLY_PATTERN.exec(value);
  return Boolean(match && match[0] === value && isRealCalendarDate(match[1], match[2], match[3]));
}

function isStrictDateTimeLike(value: string): boolean {
  const match = DATE_TIME_PATTERN.exec(value);
  return Boolean(match && match[0] === value && isRealCalendarDate(match[1], match[2], match[3]));
}

function parseStrictDate(value?: string): dayjs.Dayjs | null {
  if (!value) return null;
  if (!isStrictDateOnly(value) && !isStrictDateTimeLike(value)) return null;

  const parsed = dayjs(value);
  return parsed.isValid() ? parsed : null;
}

function formatCompanyPosition(company?: string, position?: string): string {
  return [company, position].filter(Boolean).join(' - ');
}

function makeAction(label: string, target: ActionCommandTarget, refs: Omit<ActionCommand, 'label' | 'target'> = {}): ActionCommand {
  return { label, target, ...refs };
}

export function formatPipelineHealthLabel(label: PipelineHealth['label']): string {
  if (label === 'critical') return t("高风险");
  if (label === 'watch') return t("需关注");
  return t("健康");
}

/** Presentation only. The server owns eligibility, source identity and windows. */
export function reminderCandidateInsight(candidate: ReminderCandidate, current: dayjs.Dayjs): PipelineInsight | null {
  const now = current.valueOf() / 1000;
  if (now < candidate.display_from || (candidate.display_until !== null && now >= candidate.display_until)) return null;
  const hours = (candidate.due_at - now) / 3600;
  const common = { id: candidate.id, candidate, appId: candidate.application_id ?? undefined,
    eventId: candidate.event_id ?? undefined, offerId: candidate.offer_id ?? undefined };
  const refs = { appId: common.appId, eventId: common.eventId, offerId: common.offerId };
  if (candidate.reason_code === 'offer_response_deadline') {
    const priority: PipelinePriority = hours <= 48 ? 'p0' : 'p1';
    return { ...common, kind: 'offer_deadline', priority,
      title: t("{company_name} offer 答复截止", { company_name: candidate.company }),
      reason: priority === 'p0' ? t("Offer 答复截止时间已进入 48 小时内。") : t("Offer 答复截止时间将在本周到来。"),
      evidence: [t("截止日：{deadlineLabel}", { deadlineLabel: candidate.deadline_label ?? '' })],
      primaryAction: makeAction(t("打开谈薪中心"), 'offers', refs), sortKey: hours };
  }
  if (candidate.reason_code === 'stale_application') {
    const days = Math.floor((now - (candidate.updated_at ?? candidate.due_at)) / 86400);
    return { ...common, kind: 'stale_application', priority: days >= 14 ? 'p1' : 'p2',
      title: t("{company_name} 需要跟进", { company_name: candidate.company }),
      reason: t("已 {days} 天没有更新。", { days }),
      evidence: [t("最近更新：{value}", { value: dayjs((candidate.updated_at ?? candidate.due_at) * 1000).format('YYYY-MM-DD') })],
      primaryAction: makeAction(t("打开投递看板"), 'board', refs), sortKey: 1000 - days };
  }
  const label = formatCompanyPosition(candidate.company, candidate.position) || t("已安排日程");
  const evidence = [t("日程时间：{value}", { value: dayjs(candidate.due_at * 1000).format('YYYY-MM-DD HH:mm') })];
  if (candidate.reason_code === 'review') {
    return { ...common, kind: 'interview_review', priority: 'p2',
      title: t("面试复盘提醒"), reason: t("面试结束，可记录复盘"), evidence,
      primaryAction: makeAction(t("打开日历"), 'calendar', refs), sortKey: 250 };
  }
  if (candidate.reason_code === 'event_upcoming') {
    return { ...common, kind: 'event_upcoming', priority: hours <= 24 ? 'p0' : 'p1',
      title: t("{label} 即将开始", { label }),
      reason: t("{value} 将在 {value2} 小时后开始。", {
        value: candidate.event_type ? EVENT_TYPE_LABELS[candidate.event_type] : t("日程"),
        value2: Math.max(1, Math.ceil(hours)),
      }), evidence,
      primaryAction: makeAction(t("打开日历"), 'calendar', refs), sortKey: 200 + hours };
  }
  const deadline = candidate.reason_code === 'deadline';
  return { ...common, kind: deadline ? 'application_deadline' : 'interview_soon', priority: hours <= 24 ? 'p0' : 'p1',
    title: deadline ? t("截止提醒") : t("{label} 即将开始", { label }),
    reason: deadline ? t("投递截止时间临近") : t("面试临近，可提前准备"), evidence,
    primaryAction: makeAction(t("打开日历"), 'calendar', refs), sortKey: 200 + hours };
}

export function derivePipelineInsights({
  apps,
  events,
  materialKits,
  reminderCandidates = [],
  practiceStats,
  weeklyTarget,
  now = dayjs(),
}: DerivePipelineInsightsInput): PipelineInsight[] {
  const current = dayjs(now);
  const insights: PipelineInsight[] = [];

  for (const candidate of reminderCandidates) {
    const insight = reminderCandidateInsight(candidate, current);
    if (insight) insights.push(insight);
  }

  const appsWithFutureEvents = new Set(
    events
      .filter((event) => {
        const scheduledAt = parseStrictDate(event.scheduled_at);
        return event.status === 'todo' && Boolean(scheduledAt?.isAfter(current));
      })
      .map((event) => event.application_id),
  );

  for (const app of apps) {
    if (app.closed_at || app.deleted_at || app.status === 'closed') continue;

    if (app.status === 'interview' && !appsWithFutureEvents.has(app.id)) {
      insights.push({
        id: `no-next-${app.id}`,
        kind: 'no_next_event',
        priority: 'p2',
        title: t("{company_name} 缺少下一步日程", { company_name: app.company_name }),
        reason: t("投递已进入面试阶段，但还没有安排下一次日程。"),
        evidence: [t("当前阶段：{status}", { status: app.status })],
        primaryAction: makeAction(t("打开日历"), 'calendar', { appId: app.id }),
        appId: app.id,
        sortKey: 2500,
      });
    }
  }

  if (materialKits) {
    const materialKitByApp = new Map(materialKits.map((kit) => [kit.application_id, kit]));
    for (const app of apps) {
      if (!WAITING_STATUSES.includes(app.status)) continue;

      const kit = materialKitByApp.get(app.id);
      if (kit?.complete) continue;

      insights.push({
        id: `material-kit-${app.id}`,
        kind: 'material_kit_incomplete',
        priority: 'p2',
        title: t("{company_name} 申请材料待完善", { company_name: app.company_name }),
        reason: t("简历、沟通记录或申请材料仍需要复核。"),
        evidence: [t("材料包：未完成")],
        primaryAction: makeAction(t("打开投递看板"), 'board', { appId: app.id }),
        appId: app.id,
        sortKey: 2700,
      });
    }
  }

  const due = practiceStats?.due ?? 0;
  if (due > 0) {
    insights.push({
      id: 'questions-due',
      kind: 'question_due',
      priority: due >= 10 ? 'p1' : 'p2',
      title: t("{due} 道题待复习", { due: due }),
      reason: t("今天有 {due} 道练习题到期需要复习。", { due: due }),
      evidence: [t("待复习题目：{due}", { due: due })],
      primaryAction: makeAction(t("打开题库练习"), 'questions'),
      questionCount: due,
      sortKey: 3000 - Math.min(due, 50),
    });
  }

  const weeklyHealth = summarizePipelineHealth(apps, insights, weeklyTarget, current);
  if (weeklyHealth.weeklyGap > 0) {
    insights.push({
      id: 'weekly-goal-gap',
      kind: 'weekly_goal_gap',
      priority: 'p2',
      title: t("本周投递目标需要关注"),
      reason: t("距离本周目标还差 {weeklyGap} 个投递。", { weeklyGap: weeklyHealth.weeklyGap }),
      evidence: [t("本周投递：{weeklyApplications}/{weeklyTarget}", { weeklyApplications: weeklyHealth.weeklyApplications, weeklyTarget: weeklyHealth.weeklyTarget })],
      primaryAction: makeAction(t("打开投递看板"), 'board'),
      sortKey: 4000,
    });
  }

  const activeApps = apps.filter((app) => ACTIVE_STATUSES.includes(app.status));
  const appliedCount = activeApps.filter((app) => app.status === 'applied').length;
  if (activeApps.length >= 5 && appliedCount / activeApps.length >= 0.7) {
    insights.push({
      id: 'pipeline-bottleneck-applied',
      kind: 'pipeline_bottleneck',
      priority: 'p2',
      title: t("投递集中在已申请阶段"),
      reason: t("大多数活跃投递还没有推进到后续阶段。"),
      evidence: [t("已申请：{appliedCount}/{length}", { appliedCount: appliedCount, length: activeApps.length })],
      primaryAction: makeAction(t("打开投递看板"), 'board'),
      sortKey: 4100,
    });
  }

  return insights.sort(
    (left, right) => PRIORITY_RANK[left.priority] - PRIORITY_RANK[right.priority] || left.sortKey - right.sortKey,
  );
}

export function summarizePipelineHealth(
  apps: Application[],
  insights: PipelineInsight[],
  weeklyTarget = 0,
  now: ConfigType = dayjs(),
): PipelineHealth {
  const current = dayjs(now);
  const weeklyApplications = apps.filter((app) => {
    const appliedAt = parseStrictDate(app.applied_at);
    return Boolean(appliedAt && !appliedAt.isBefore(current.locale('en').startOf('week')) && !appliedAt.isAfter(current.locale('en').endOf('week')));
  }).length;
  const dueQuestions = insights.reduce((total, insight) => total + (insight.questionCount ?? 0), 0);
  const p0 = insights.filter((insight) => insight.priority === 'p0').length;
  const p1 = insights.filter((insight) => insight.priority === 'p1').length;
  const p2 = insights.filter((insight) => insight.priority === 'p2').length;
  const stale = insights.filter((insight) => insight.kind === 'stale_application').length;
  const weeklyGap = Math.max(0, weeklyTarget - weeklyApplications);
  const score = Math.max(0, 100 - p0 * 35 - p1 * 18 - p2 * 8 - Math.min(dueQuestions, 10) * 2 - weeklyGap * 3);
  const label: PipelineHealth['label'] = score < 40 ? 'critical' : score < 85 ? 'watch' : 'healthy';
  const bottleneck =
    insights.find((insight) => insight.priority === 'p0')?.title ??
    insights.find((insight) => insight.priority === 'p1')?.title ??
    (weeklyGap > 0 ? t("本周投递节奏") : t("暂无明显瓶颈"));

  return {
    score,
    label,
    bottleneck,
    total: apps.length,
    active: apps.filter((app) => ACTIVE_STATUSES.includes(app.status)).length,
    p0,
    p1,
    p2,
    offers: apps.filter((app) => app.status === 'offer').length,
    interviews: apps.filter((app) => app.status === 'interview').length,
    stale,
    dueQuestions,
    weeklyTarget,
    weeklyApplications,
    weeklyGap,
  };
}

export function toLegacyActionItems(insights: PipelineInsight[]): LegacyActionItem[] {
  return insights.map((insight) => ({
    id: insight.id,
    kind: insight.kind,
    priority: insight.priority,
    title: insight.title,
    detail: insight.reason,
    primaryActionLabel: insight.primaryAction.label,
    target: insight.primaryAction.target,
    sortKey: insight.sortKey,
    appId: insight.appId,
    offerId: insight.offerId,
    eventId: insight.eventId,
    questionCount: insight.questionCount,
  }));
}
