import { t } from '@/i18n';
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
  get ready() { return t("就绪"); },
  get watch() { return t("关注"); },
  get blocked() { return t("阻塞"); },
};

export const READINESS_MATERIAL_STATUS_LABELS: Record<ReadinessMaterialStatus, string> = {
  get draft() { return t("草稿"); },
  get ready() { return t("就绪"); },
  get submitted() { return t("已提交"); },
  get missing() { return t("缺失"); },
  get unknown() { return t("未知"); },
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
  'event_upcoming',
  'application_deadline',
  'material_kit_incomplete',
  'question_due',
];
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
  weeklyTarget: number | undefined,
  now: dayjs.Dayjs,
): MissionMetric[] {
  const weekStart = now.locale('en').startOf('week');
  const weekEnd = now.locale('en').endOf('week');
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

  const target = weeklyTarget != null && Number.isFinite(weeklyTarget) && weeklyTarget > 0 ? weeklyTarget : undefined;

  return [
    {
      kind: 'applications',
      label: t("本周投递"),
      current: weeklyApplications,
      target,
      state: stateForProgress(weeklyApplications, target ?? 0),
      reason: target == null
        ? t("本周已新增 {weeklyApplications} 个投递。", { weeklyApplications })
        : t("本周已新增 {weeklyApplications} 个投递，目标 {weeklyTarget} 个。", { weeklyApplications, weeklyTarget: target }),
      targetView: 'board',
    },
    {
      kind: 'followups',
      label: t("跟进节奏"),
      current: staleCount,
      state: staleCount === 0 ? 'on_track' : 'watch',
      reason: staleCount === 0 ? t("暂无明显停滞的活跃投递。") : t("{staleCount} 个活跃投递需要跟进。", { staleCount: staleCount }),
      targetView: 'reminders',
    },
    {
      kind: 'interviews',
      label: t("本周面试"),
      current: interviewsThisWeek,
      state: interviewsThisWeek > 0 ? 'watch' : 'on_track',
      reason: interviewsThisWeek > 0 ? t("本周有 {interviewsThisWeek} 个面试或测评日程。", { interviewsThisWeek: interviewsThisWeek }) : t("本周暂无已安排面试。"),
      targetView: 'calendar',
    },
    {
      kind: 'practice',
      label: t("刷题准备"),
      current: dueQuestions,
      target: dueQuestions > 0 ? 1 : 0,
      state: dueQuestions >= 10 ? 'behind' : dueQuestions > 0 ? 'watch' : 'on_track',
      reason: dueQuestions > 0 ? t("{dueQuestions} 道题到期需要复习。", { dueQuestions: dueQuestions }) : t("暂无到期题目。"),
      targetView: 'questions',
    },
    {
      kind: 'materials',
      label: t("材料就绪"),
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
          ? t("材料状态暂不可用。")
          : t("{readyMaterials}/{length} 个活跃投递材料已就绪。", { readyMaterials: readyMaterials, length: activeApps.length }),
      targetView: 'board',
    },
    {
      kind: 'offers',
      label: t("Offer 压力"),
      current: urgentOffers,
      target: pendingOffers.length,
      state: urgentOffers > 0 ? 'blocked' : pendingOffers.length > 0 ? 'watch' : 'on_track',
      reason: urgentOffers > 0 ? t("{urgentOffers} 个 Offer 截止期进入 48 小时内。", { urgentOffers: urgentOffers }) : t("暂无 48 小时内截止的 Offer。"),
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

      if (nextEvent) evidence.push(t("下一场日程：{value}", { value: dayjs(nextEvent.scheduled_at).format('MM-DD HH:mm') }));
      if (materialStatus === 'unknown') evidence.push(t("材料状态暂不可用"));
      else evidence.push(t("材料状态：{value}", { value: READINESS_MATERIAL_STATUS_LABELS[materialStatus] }));
      if (staleDays) evidence.push(t("已 {staleDays} 天未更新", { staleDays: staleDays }));
      if (dueQuestionCount > 0) evidence.push(t("{dueQuestionCount} 道题到期", { dueQuestionCount: dueQuestionCount }));

      if (hoursToEvent != null && hoursToEvent >= 0 && hoursToEvent <= 24 && !isReadyMaterial(materialStatus)) {
        readiness = 'blocked';
        evidence.push(t("24 小时内有日程但材料未就绪"));
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
    return t("添加第一条投递，OfferPilot 会开始组织你的求职节奏。");
  }

  const urgent = actions.find((item) => item.priority === 'p0');
  if (urgent) return t("优先处理：{title}", { title: urgent.title });

  const blocked = readiness.find((item) => item.readiness === 'blocked');
  if (blocked) return t("先解除 {companyName} 的准备阻塞。", { companyName: blocked.companyName });

  const behind = metrics.find((metric) => metric.kind === 'applications' && metric.target != null && metric.state === 'behind');
  if (behind) return t("{label}落后于本周目标。", { label: behind.label });

  return t("本周节奏稳定，保持跟进和准备即可。");
}

export function deriveMissionControl({
  apps,
  events,
  offers,
  materialKits,
  practiceStats,
  insights,
  healthLabel,
  weeklyTarget,
  now = dayjs(),
}: DeriveMissionControlInput): MissionControlSummary {
  const current = dayjs(now);
  const safeApps = apps ?? [];
  const safeEvents = events ?? [];
  const safeOffers = offers ?? [];
  const weekStart = current.locale('en').startOf('week');
  const weekEnd = current.locale('en').endOf('week');
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
