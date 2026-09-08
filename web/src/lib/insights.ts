import dayjs from 'dayjs';
import type { Application, ApplicationStatus } from '@/types/application';
import type { ScheduleEvent } from '@/types/event';
import type { Offer } from '@/types/offer';

export interface Kpis {
  total: number;
  interviewing: number;
  offers: number;
  responseRate: number; // 0..1
  weeklyDelta: number; // 近 7 天新增投递数
}

export interface FunnelStage {
  key: string;
  label: string;
  count: number;
  ratio: number; // 相对总投递
}

export interface MomentumBucket {
  label: string; // 如 "6/23"
  count: number;
}

export type ReminderSeverity = 'red' | 'amber' | 'green';
export type ReminderKind = 'stale' | 'interview' | 'offer_deadline' | 'no_next';
export type ReminderTarget = 'board' | 'calendar' | 'offers';

export interface Reminder {
  id: string;
  kind: ReminderKind;
  severity: ReminderSeverity;
  title: string;
  detail: string;
  appId?: number;
  offerId?: number;
  eventId?: number;
  target: ReminderTarget;
  sortKey: number; // 越小越紧急
}

// 阈值（集中管理，便于将来做成设置）
export const STALE_DAYS = 7;
export const STALE_RED_DAYS = 14;
export const INTERVIEW_SOON_HOURS = 72;
export const OFFER_SOON_DAYS = 7;

// 表示"已收到回音"的状态。
const RESPONDED: ApplicationStatus[] = ['written_test', 'interview', 'offer', 'closed'];
// 仍在等待对方回复的状态（用于停滞检测）
const WAITING: ApplicationStatus[] = ['pending', 'applied', 'written_test'];

export function computeKpis(apps: Application[], now = dayjs()): Kpis {
  const total = apps.length;
  const interviewing = apps.filter((a) => a.status === 'interview').length;
  const offers = apps.filter((a) => a.status === 'offer').length;
  const responded = apps.filter((a) => RESPONDED.includes(a.status)).length;
  const weeklyDelta = apps.filter(
    (a) => a.applied_at && now.diff(dayjs(a.applied_at), 'day') < 7
  ).length;
  return {
    total,
    interviewing,
    offers,
    responseRate: total === 0 ? 0 : responded / total,
    weeklyDelta,
  };
}

export function computeFunnel(apps: Application[]): FunnelStage[] {
  const total = apps.length;
  const inScreen = apps.filter((a) =>
    ['written_test', 'interview', 'offer'].includes(a.status)
  ).length;
  const inInterview = apps.filter((a) => ['interview', 'offer'].includes(a.status)).length;
  const inOffer = apps.filter((a) => a.status === 'offer').length;
  const ratio = (n: number) => (total === 0 ? 0 : n / total);
  return [
    { key: 'applied', label: '投递', count: total, ratio: 1 },
    { key: 'screen', label: '初筛', count: inScreen, ratio: ratio(inScreen) },
    { key: 'interview', label: '面试', count: inInterview, ratio: ratio(inInterview) },
    { key: 'offer', label: 'Offer', count: inOffer, ratio: ratio(inOffer) },
  ];
}

export function computeMomentum(apps: Application[], weeks = 4, now = dayjs()): MomentumBucket[] {
  const buckets: MomentumBucket[] = [];
  for (let i = weeks - 1; i >= 0; i--) {
    const start = now.subtract(i, 'week').startOf('week');
    const end = start.add(1, 'week');
    const count = apps.filter((a) => {
      if (!a.applied_at) return false;
      const d = dayjs(a.applied_at);
      return (d.isAfter(start) || d.isSame(start)) && d.isBefore(end);
    }).length;
    buckets.push({ label: start.format('M/D'), count });
  }
  return buckets;
}

export function deriveReminders(
  apps: Application[],
  events: ScheduleEvent[],
  offers: Offer[],
  now = dayjs()
): Reminder[] {
  const out: Reminder[] = [];

  // 1. 投递停滞
  for (const a of apps) {
    if (!WAITING.includes(a.status)) continue;
    const base = a.updated_at || a.applied_at;
    if (!base) continue;
    const days = now.diff(dayjs(base), 'day');
    if (days <= STALE_DAYS) continue;
    out.push({
      id: `stale-${a.id}`,
      kind: 'stale',
      severity: days >= STALE_RED_DAYS ? 'red' : 'amber',
      title: `${a.company_name} · ${a.position_name}`,
      detail: `已投 ${days} 天无回音，建议跟进`,
      appId: a.id,
      target: 'board',
      sortKey: 10000 - days, // 停滞越久越靠前
    });
  }

  // 2. 面试倒计时
  for (const e of events) {
    if (!e.scheduled_at) continue;
    const when = dayjs(e.scheduled_at);
    const hours = when.diff(now, 'hour', true);
    if (hours < 0 || hours > INTERVIEW_SOON_HOURS) continue;
    const label = e.company_name ? `${e.company_name} · ${e.position_name ?? ''}` : '面试安排';
    out.push({
      id: `event-${e.id}`,
      kind: 'interview',
      severity: hours < 24 ? 'red' : 'amber',
      title: label.trim(),
      detail: `${when.format('M月D日 HH:mm')} 面试，去准备`,
      appId: e.application_id,
      eventId: e.id,
      target: 'calendar',
      sortKey: hours,
    });
  }

  // 3. Offer 答复期
  for (const o of offers) {
    if (!['pending', 'negotiating'].includes(o.status)) continue;
    if (!o.deadline) continue;
    const dl = dayjs(o.deadline);
    const days = dl.diff(now, 'day');
    if (days < 0 || days > OFFER_SOON_DAYS) continue;
    out.push({
      id: `offer-${o.id}`,
      kind: 'offer_deadline',
      severity: days <= 2 ? 'red' : 'amber',
      title: `${o.company_name} Offer`,
      detail: `还剩 ${days} 天答复期`,
      offerId: o.id,
      appId: o.application_id,
      target: 'offers',
      sortKey: days,
    });
  }

  // 4. 面试中但无未来安排
  const appsWithFutureEvent = new Set(
    events
      .filter((e) => e.scheduled_at && dayjs(e.scheduled_at).isAfter(now))
      .map((e) => e.application_id)
  );
  for (const a of apps) {
    if (a.status !== 'interview' || appsWithFutureEvent.has(a.id)) continue;
    out.push({
      id: `nonext-${a.id}`,
      kind: 'no_next',
      severity: 'amber',
      title: `${a.company_name} · ${a.position_name}`,
      detail: '面试进行中，未安排下一场',
      appId: a.id,
      target: 'calendar',
      sortKey: 5000,
    });
  }

  const rank: Record<ReminderSeverity, number> = { red: 0, amber: 1, green: 2 };
  return out.sort((x, y) => rank[x.severity] - rank[y.severity] || x.sortKey - y.sortKey);
}

export function reminderBadgeCount(reminders: Reminder[]): number {
  return reminders.filter((r) => r.severity === 'red' || r.severity === 'amber').length;
}
