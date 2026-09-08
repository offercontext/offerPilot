import dayjs, { type ConfigType } from 'dayjs';
import type { Application, ApplicationStatus } from '@/types/application';
import type { ScheduleEvent } from '@/types/event';
import type { Offer } from '@/types/offer';
import type { PracticeStats } from '@/types/question';

export type PipelinePriority = 'p0' | 'p1' | 'p2';
export type PipelineInsightKind =
  | 'offer_deadline'
  | 'interview_soon'
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

function parseDeadline(value?: string): dayjs.Dayjs | null {
  if (!value) return null;
  if (isStrictDateOnly(value)) return dayjs(value).endOf('day');
  return parseStrictDate(value);
}

function formatDate(value: string): string {
  if (isStrictDateOnly(value)) return value;
  return dayjs(value).format('YYYY-MM-DD');
}

function formatCompanyPosition(company?: string, position?: string): string {
  return [company, position].filter(Boolean).join(' - ');
}

function makeAction(label: string, target: ActionCommandTarget, refs: Omit<ActionCommand, 'label' | 'target'> = {}): ActionCommand {
  return { label, target, ...refs };
}

export function formatPipelineHealthLabel(label: PipelineHealth['label']): string {
  if (label === 'critical') return '高风险';
  if (label === 'watch') return '需关注';
  return '健康';
}

export function derivePipelineInsights({
  apps,
  events,
  offers,
  materialKits,
  practiceStats,
  weeklyTarget,
  now = dayjs(),
}: DerivePipelineInsightsInput): PipelineInsight[] {
  const current = dayjs(now);
  const insights: PipelineInsight[] = [];

  for (const offer of offers) {
    if (!['pending', 'negotiating'].includes(offer.status)) continue;

    const deadline = parseDeadline(offer.deadline);
    if (!deadline) continue;

    const hours = deadline.diff(current, 'hour', true);
    if (!Number.isFinite(hours) || hours < 0 || hours > 168) continue;

    const deadlineLabel = formatDate(offer.deadline);
    const priority: PipelinePriority = hours <= 48 ? 'p0' : 'p1';
    insights.push({
      id: `offer-${offer.id}`,
      kind: 'offer_deadline',
      priority,
      title: `${offer.company_name} offer 答复截止`,
      reason:
        priority === 'p0'
          ? 'Offer 答复截止时间已进入 48 小时内。'
          : 'Offer 答复截止时间将在本周到来。',
      evidence: [`截止日：${deadlineLabel}`],
      primaryAction: makeAction('打开谈薪中心', 'offers', {
        appId: offer.application_id,
        offerId: offer.id,
      }),
      appId: offer.application_id,
      offerId: offer.id,
      sortKey: hours,
    });
  }

  for (const event of events) {
    const scheduledAt = parseStrictDate(event.scheduled_at);
    if (!scheduledAt) continue;

    const hours = scheduledAt.diff(current, 'hour', true);
    if (!Number.isFinite(hours) || hours < 0 || hours > 72) continue;

    const label = formatCompanyPosition(event.company_name, event.position_name) || '已安排日程';
    const priority: PipelinePriority = hours <= 24 ? 'p0' : 'p1';
    insights.push({
      id: `interview-${event.id}`,
      kind: 'interview_soon',
      priority,
      title: `${label} 即将开始`,
      reason: `${event.event_type} 将在 ${Math.max(1, Math.ceil(hours))} 小时后开始。`,
      evidence: [`日程时间：${scheduledAt.format('YYYY-MM-DD HH:mm')}`],
      primaryAction: makeAction('打开日历', 'calendar', {
        appId: event.application_id,
        eventId: event.id,
      }),
      appId: event.application_id,
      eventId: event.id,
      sortKey: 200 + hours,
    });
  }

  const appsWithFutureEvents = new Set(
    events
      .filter((event) => {
        const scheduledAt = parseStrictDate(event.scheduled_at);
        return Boolean(scheduledAt?.isAfter(current));
      })
      .map((event) => event.application_id),
  );

  for (const app of apps) {
    if (WAITING_STATUSES.includes(app.status)) {
      const baseDate = parseStrictDate(app.updated_at || app.applied_at);
      if (baseDate) {
        const days = current.diff(baseDate, 'day');
        if (Number.isFinite(days) && days > 7) {
          insights.push({
            id: `stale-${app.id}`,
            kind: 'stale_application',
            priority: days >= 14 ? 'p1' : 'p2',
            title: `${app.company_name} 需要跟进`,
            reason: `已 ${days} 天没有更新。`,
            evidence: [`最近更新：${baseDate.format('YYYY-MM-DD')}`],
            primaryAction: makeAction('打开投递看板', 'board', { appId: app.id }),
            appId: app.id,
            sortKey: 1000 - days,
          });
        }
      }
    }

    if (app.status === 'interview' && !appsWithFutureEvents.has(app.id)) {
      insights.push({
        id: `no-next-${app.id}`,
        kind: 'no_next_event',
        priority: 'p2',
        title: `${app.company_name} 缺少下一步日程`,
        reason: '投递已进入面试阶段，但还没有安排下一次日程。',
        evidence: [`当前阶段：${app.status}`],
        primaryAction: makeAction('打开日历', 'calendar', { appId: app.id }),
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
        title: `${app.company_name} 申请材料待完善`,
        reason: '简历、沟通记录或申请材料仍需要复核。',
        evidence: ['材料包：未完成'],
        primaryAction: makeAction('打开投递看板', 'board', { appId: app.id }),
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
      title: `${due} 道题待复习`,
      reason: `今天有 ${due} 道练习题到期需要复习。`,
      evidence: [`待复习题目：${due}`],
      primaryAction: makeAction('打开题库练习', 'questions'),
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
      title: '本周投递目标需要关注',
      reason: `距离本周目标还差 ${weeklyHealth.weeklyGap} 个投递。`,
      evidence: [`本周投递：${weeklyHealth.weeklyApplications}/${weeklyHealth.weeklyTarget}`],
      primaryAction: makeAction('打开投递看板', 'board'),
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
      title: '投递集中在已申请阶段',
      reason: '大多数活跃投递还没有推进到后续阶段。',
      evidence: [`已申请：${appliedCount}/${activeApps.length}`],
      primaryAction: makeAction('打开投递看板', 'board'),
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
    return Boolean(appliedAt && current.diff(appliedAt, 'day') < 7);
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
    (weeklyGap > 0 ? '本周投递节奏' : '暂无明显瓶颈');

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
