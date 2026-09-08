import dayjs, { type ConfigType } from 'dayjs';
import type { Application, ApplicationStatus } from '@/types/application';
import type { ScheduleEvent } from '@/types/event';
import type { MaterialKitStatus, MaterialKitViewModel } from '@/types/materialKit';
import type { Offer } from '@/types/offer';
import type { PracticeStats } from '@/types/question';
import type { ViewMode } from '@/layout/navigation';
import type { PipelineHealth, PipelineInsight, PipelineInsightKind } from './pipelineInsights';

export type MissionMetricKind =
  | 'applications'
  | 'followups'
  | 'interviews'
  | 'practice'
  | 'materials'
  | 'offers';

export type MissionMetricState = 'on_track' | 'watch' | 'behind' | 'blocked';
export type ApplicationReadinessState = 'ready' | 'watch' | 'blocked';
export type ReadinessMaterialStatus = MaterialKitStatus | 'missing' | 'unknown';

export const READINESS_STATE_LABELS: Record<ApplicationReadinessState, string> = {
  ready: '就绪',
  watch: '关注',
  blocked: '阻塞',
};

export const READINESS_MATERIAL_STATUS_LABELS: Record<ReadinessMaterialStatus, string> = {
  draft: '草稿',
  ready: '就绪',
  submitted: '已提交',
  missing: '缺失',
  unknown: '未知',
};

export interface MissionMetric {
  kind: MissionMetricKind;
  label: string;
  current: number;
  target?: number;
  state: MissionMetricState;
  reason: string;
  targetView: ViewMode;
}

export interface ApplicationReadiness {
  applicationId: number;
  companyName: string;
  positionName: string;
  status: ApplicationStatus;
  readiness: ApplicationReadinessState;
  materialStatus: ReadinessMaterialStatus;
  hasUpcomingEvent: boolean;
  staleDays?: number;
  dueQuestionCount?: number;
  evidence: string[];
}

export interface MissionActionGroups {
  urgent: PipelineInsight[];
  prepare: PipelineInsight[];
  momentum: PipelineInsight[];
}

export interface MissionControlSummary {
  weekStart: string;
  weekEnd: string;
  headline: string;
  healthLabel: PipelineHealth['label'];
  metrics: MissionMetric[];
  actions: PipelineInsight[];
  actionGroups: MissionActionGroups;
  readiness: ApplicationReadiness[];
  focusApplicationId?: number;
}

interface DeriveMissionControlInput {
  apps: Application[] | null | undefined;
  events: ScheduleEvent[] | null | undefined;
  offers: Offer[] | null | undefined;
  materialKits?: MaterialKitViewModel[] | null;
  practiceStats?: PracticeStats | null;
  insights: PipelineInsight[];
  healthLabel: PipelineHealth['label'];
  weeklyTarget?: number;
  now?: ConfigType;
}

const ACTIVE_STATUSES: ApplicationStatus[] = ['pending', 'applied', 'written_test', 'interview', 'offer'];
const WAITING_STATUSES: ApplicationStatus[] = ['pending', 'applied', 'written_test'];
const PREPARE_KINDS: PipelineInsightKind[] = [
  'offer_deadline',
  'interview_soon',
  'material_kit_incomplete',
  'question_due',
];
const DEFAULT_WEEKLY_TARGET = 6;
const FOLLOWUP_TARGET = 3;
const DATE_ONLY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

function parseDate(value?: string): dayjs.Dayjs | null {
  if (!value) return null;
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed : null;
}

function parseDeadline(value?: string): dayjs.Dayjs | null {
  if (!value) return null;
  if (DATE_ONLY_PATTERN.test(value)) return dayjs(value).endOf('day');
  return parseDate(value);
}

function isActiveApplication(app: Application): boolean {
  return ACTIVE_STATUSES.includes(app.status);
}

function isWaitingApplication(app: Application): boolean {
  return WAITING_STATUSES.includes(app.status);
}

function isReadyMaterial(status: ReadinessMaterialStatus): boolean {
  return status === 'ready' || status === 'submitted';
}

function stateForProgress(current: number, target: number): MissionMetricState {
  if (target <= 0) return 'on_track';
  if (current >= target) return 'on_track';
  if (current === 0) return 'behind';
  if (current / target >= 0.5) return 'watch';
  return 'behind';
}

function formatWeekDate(value: dayjs.Dayjs): string {
  return value.format('YYYY-MM-DD');
}

function getMaterialStatus(
  appId: number,
  materialKits: MaterialKitViewModel[] | null | undefined,
): ReadinessMaterialStatus {
  if (materialKits == null) return 'unknown';
  return materialKits.find((kit) => kit.application_id === appId)?.status ?? 'missing';
}

function getUpcomingEvents(appId: number, events: ScheduleEvent[], now: dayjs.Dayjs): ScheduleEvent[] {
  return events
    .filter((event) => event.application_id === appId)
    .filter((event) => {
      const scheduled = parseDate(event.scheduled_at);
      return Boolean(scheduled?.isAfter(now));
    })
    .sort((left, right) => dayjs(left.scheduled_at).valueOf() - dayjs(right.scheduled_at).valueOf());
}

function getStaleDays(app: Application, now: dayjs.Dayjs): number | undefined {
  if (!isWaitingApplication(app)) return undefined;
  const base = parseDate(app.updated_at || app.applied_at);
  if (!base) return undefined;
  const days = now.diff(base, 'day');
  return Number.isFinite(days) && days > 7 ? days : undefined;
}

function buildMetrics(
  apps: Application[],
  events: ScheduleEvent[],
  offers: Offer[],
  materialKits: MaterialKitViewModel[] | null | undefined,
  practiceStats: PracticeStats | null | undefined,
  weeklyTarget: number,
  now: dayjs.Dayjs,
): MissionMetric[] {
  const weekStart = now.startOf('week');
  const weekEnd = now.endOf('week');
  const activeApps = apps.filter(isActiveApplication);
  const weeklyApplications = apps.filter((app) => {
    const appliedAt = parseDate(app.applied_at);
    return Boolean(appliedAt && !appliedAt.isBefore(weekStart) && !appliedAt.isAfter(weekEnd));
  }).length;
  const staleCount = activeApps.filter((app) => (getStaleDays(app, now) ?? 0) > 7).length;
  const interviewsThisWeek = events.filter((event) => {
    const scheduled = parseDate(event.scheduled_at);
    return Boolean(scheduled && !scheduled.isBefore(weekStart) && !scheduled.isAfter(weekEnd));
  }).length;
  const dueQuestions = practiceStats?.due ?? 0;
  const readyMaterials =
    materialKits == null
      ? 0
      : activeApps.filter((app) => isReadyMaterial(getMaterialStatus(app.id, materialKits))).length;
  const pendingOffers = offers.filter((offer) => ['pending', 'negotiating'].includes(offer.status));
  const urgentOffers = pendingOffers.filter((offer) => {
    const deadline = parseDeadline(offer.deadline);
    return Boolean(deadline && deadline.diff(now, 'hour', true) >= 0 && deadline.diff(now, 'hour', true) <= 48);
  }).length;

  return [
    {
      kind: 'applications',
      label: '本周投递',
      current: weeklyApplications,
      target: weeklyTarget,
      state: stateForProgress(weeklyApplications, weeklyTarget),
      reason: `本周已新增 ${weeklyApplications} 个投递，目标 ${weeklyTarget} 个。`,
      targetView: 'board',
    },
    {
      kind: 'followups',
      label: '跟进节奏',
      current: staleCount,
      target: staleCount > 0 ? FOLLOWUP_TARGET : 0,
      state: staleCount === 0 ? 'on_track' : staleCount >= FOLLOWUP_TARGET ? 'blocked' : 'watch',
      reason: staleCount === 0 ? '暂无明显停滞的活跃投递。' : `${staleCount} 个活跃投递需要跟进。`,
      targetView: 'reminders',
    },
    {
      kind: 'interviews',
      label: '本周面试',
      current: interviewsThisWeek,
      state: interviewsThisWeek > 0 ? 'watch' : 'on_track',
      reason: interviewsThisWeek > 0 ? `本周有 ${interviewsThisWeek} 个面试或测评日程。` : '本周暂无已安排面试。',
      targetView: 'calendar',
    },
    {
      kind: 'practice',
      label: '刷题准备',
      current: dueQuestions,
      target: dueQuestions > 0 ? 1 : 0,
      state: dueQuestions >= 10 ? 'behind' : dueQuestions > 0 ? 'watch' : 'on_track',
      reason: dueQuestions > 0 ? `${dueQuestions} 道题到期需要复习。` : '暂无到期题目。',
      targetView: 'questions',
    },
    {
      kind: 'materials',
      label: '材料就绪',
      current: readyMaterials,
      target: activeApps.length,
      state:
        materialKits == null
          ? 'watch'
          : readyMaterials >= activeApps.length
            ? 'on_track'
            : readyMaterials === 0 && activeApps.length > 0
              ? 'behind'
              : 'watch',
      reason:
        materialKits == null
          ? '材料状态暂不可用。'
          : `${readyMaterials}/${activeApps.length} 个活跃投递材料已就绪。`,
      targetView: 'board',
    },
    {
      kind: 'offers',
      label: 'Offer 压力',
      current: urgentOffers,
      target: pendingOffers.length,
      state: urgentOffers > 0 ? 'blocked' : pendingOffers.length > 0 ? 'watch' : 'on_track',
      reason: urgentOffers > 0 ? `${urgentOffers} 个 Offer 截止期进入 48 小时内。` : '暂无 48 小时内截止的 Offer。',
      targetView: 'offers',
    },
  ];
}

function buildReadiness(
  apps: Application[],
  events: ScheduleEvent[],
  materialKits: MaterialKitViewModel[] | null | undefined,
  practiceStats: PracticeStats | null | undefined,
  now: dayjs.Dayjs,
): ApplicationReadiness[] {
  const dueQuestionCount = practiceStats?.due ?? 0;

  return apps
    .filter(isActiveApplication)
    .map((app) => {
      const materialStatus = getMaterialStatus(app.id, materialKits);
      const upcomingEvents = getUpcomingEvents(app.id, events, now);
      const nextEvent = upcomingEvents[0];
      const hoursToEvent = nextEvent ? dayjs(nextEvent.scheduled_at).diff(now, 'hour', true) : undefined;
      const staleDays = getStaleDays(app, now);
      const evidence: string[] = [];
      let readiness: ApplicationReadinessState = 'ready';

      if (nextEvent) evidence.push(`下一场日程：${dayjs(nextEvent.scheduled_at).format('MM-DD HH:mm')}`);
      if (materialStatus === 'unknown') evidence.push('材料状态暂不可用');
      else evidence.push(`材料状态：${READINESS_MATERIAL_STATUS_LABELS[materialStatus]}`);
      if (staleDays) evidence.push(`已 ${staleDays} 天未更新`);
      if (dueQuestionCount > 0) evidence.push(`${dueQuestionCount} 道题到期`);

      if (hoursToEvent != null && hoursToEvent >= 0 && hoursToEvent <= 24 && !isReadyMaterial(materialStatus)) {
        readiness = 'blocked';
        evidence.push('24 小时内有日程但材料未就绪');
      } else if (
        staleDays ||
        (app.status === 'interview' && upcomingEvents.length === 0) ||
        materialStatus === 'draft' ||
        materialStatus === 'missing' ||
        dueQuestionCount > 0
      ) {
        readiness = 'watch';
      }

      return {
        applicationId: app.id,
        companyName: app.company_name,
        positionName: app.position_name,
        status: app.status,
        readiness,
        materialStatus,
        hasUpcomingEvent: upcomingEvents.length > 0,
        staleDays,
        dueQuestionCount,
        evidence,
      };
    })
    .sort((left, right) => {
      const rank: Record<ApplicationReadinessState, number> = { blocked: 0, watch: 1, ready: 2 };
      return rank[left.readiness] - rank[right.readiness] || left.applicationId - right.applicationId;
    });
}

export function groupMissionActions(insights: PipelineInsight[]): MissionActionGroups {
  return {
    urgent: insights.filter((item) => item.priority === 'p0'),
    prepare: insights.filter((item) => item.priority !== 'p0' && PREPARE_KINDS.includes(item.kind)),
    momentum: insights.filter((item) => item.priority !== 'p0' && !PREPARE_KINDS.includes(item.kind)),
  };
}

export function selectDefaultFocusApplicationId(readiness: ApplicationReadiness[]): number | undefined {
  const rank: Record<ApplicationReadinessState, number> = { blocked: 0, watch: 1, ready: 2 };
  return [...readiness].sort(
    (left, right) => rank[left.readiness] - rank[right.readiness] || left.applicationId - right.applicationId,
  )[0]?.applicationId;
}

function buildHeadline(metrics: MissionMetric[], actions: PipelineInsight[], readiness: ApplicationReadiness[]): string {
  if (metrics.find((metric) => metric.kind === 'applications')?.current === 0 && readiness.length === 0) {
    return '添加第一条投递，OfferPilot 会开始组织你的求职节奏。';
  }

  const urgent = actions.find((item) => item.priority === 'p0');
  if (urgent) return `优先处理：${urgent.title}`;

  const blocked = readiness.find((item) => item.readiness === 'blocked');
  if (blocked) return `先解除 ${blocked.companyName} 的准备阻塞。`;

  const behind = metrics.find((metric) => metric.state === 'behind');
  if (behind) return `${behind.label}落后于本周目标。`;

  return '本周节奏稳定，保持跟进和准备即可。';
}

export function deriveMissionControl({
  apps,
  events,
  offers,
  materialKits,
  practiceStats,
  insights,
  healthLabel,
  weeklyTarget = DEFAULT_WEEKLY_TARGET,
  now = dayjs(),
}: DeriveMissionControlInput): MissionControlSummary {
  const current = dayjs(now);
  const safeApps = apps ?? [];
  const safeEvents = events ?? [];
  const safeOffers = offers ?? [];
  const weekStart = current.startOf('week');
  const weekEnd = current.endOf('week');
  const metrics = buildMetrics(safeApps, safeEvents, safeOffers, materialKits, practiceStats, weeklyTarget, current);
  const readiness = buildReadiness(safeApps, safeEvents, materialKits, practiceStats, current);
  const actionGroups = groupMissionActions(insights);

  return {
    weekStart: formatWeekDate(weekStart),
    weekEnd: formatWeekDate(weekEnd),
    headline: buildHeadline(metrics, insights, readiness),
    healthLabel,
    metrics,
    actions: insights,
    actionGroups,
    readiness,
    focusApplicationId: selectDefaultFocusApplicationId(readiness),
  };
}
