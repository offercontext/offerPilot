// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setLocale } from '@/i18n';
import type { Application } from '@/types/application';
import type { ScheduleEvent } from '@/types/event';
import AppShell from './AppShell';

const mock = vi.hoisted(() => ({
  events: {} as { data: unknown; isLoading?: boolean; isError?: boolean },
  client: { getQueryState: () => ({ dataUpdateCount: 1 }), invalidateQueries: vi.fn() },
  noData: { data: undefined },
  listEvents: vi.fn(),
  eventReaders: [] as Array<() => unknown>,
}));
const empty: ScheduleEvent[] = [];
const timestamp = '2026-11-26T04:00:00Z';
const application = (id: number, position_name: string): Application => ({
  id, company_name: '同一家公司', position_name, status: 'interview', job_url: '', source: '', notes: '',
  applied_at: '2026-11-20T04:00:00Z', created_at: timestamp, updated_at: timestamp,
});
const apps = [application(11, '岗位 A'), application(22, '岗位 B')];
const event = (application_id: number, scheduled_at: string, status = 'todo'): ScheduleEvent => ({
  id: application_id + 100, application_id, scheduled_at, status, event_type: 'interview', subtype: '', tags: [],
  round: 2, duration_minutes: 60, location: '', notes: '', created_at: timestamp,
});
const withinWindow = event(22, '2026-11-29T04:00:00Z');
const outsideWindow = event(22, '2026-12-04T04:00:00Z');
const due = { due: 12 };

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => mock.client,
  useMutation: () => ({ isPending: false, mutate: vi.fn() }),
  useQuery: ({ queryKey, queryFn }: { queryKey: unknown[]; queryFn: () => unknown }) => {
    const [key, segment] = queryKey;
    if (key === 'events' && queryKey.length === 1) {
      mock.eventReaders.push(queryFn);
      return mock.events;
    }
    if (key === 'applications' && queryKey.length === 1) return { data: apps };
    if (key === 'offers' || key === 'mission-control') return { data: [] };
    if (key === 'questions' && segment === 'stats') return { data: due };
    if (key === 'job-mail' && segment === 'count') return { data: 2 };
    return mock.noData;
  },
}));
vi.mock('@/services/events', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/services/events')>(),
  listEvents: mock.listEvents,
}));
vi.mock('@/services/persona', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/services/persona')>(),
  getPersona: async () => ({ spec: { display_name: 'Haru' } }),
}));
vi.mock('@/services/companion', () => ({ getCompanion: () => new Promise(() => {}), sendCompanionCommand: vi.fn(), reportCompanionPresence: vi.fn(), claimCompanionBid: vi.fn() }));
vi.mock('@/lib/useReminderCandidates', () => ({ useReminderCandidates: () => ({ data: [], isPending: false, isError: false }) }));
vi.mock('@/components/ProactiveInbox', () => ({ default: () => null }));
vi.mock('@/features/jobMail/JobMailInbox', () => ({ default: () => null }));
vi.mock('@/features/dashboard/TodayInboxSummary', () => ({ default: () => null }));
vi.mock('@/features/pipeline/ActionDetailDrawer', () => ({
  default: ({ insight, onClose }: { insight: { id: string }; onClose: () => void }) => (
    <div role="dialog"><output>{insight.id}</output><button onClick={onClose}>关闭详情</button></div>
  ),
}));
vi.mock('@/components/AddApplicationForm', () => ({ default: () => null }));
vi.mock('@/components/ResumeUploadModal', () => ({ default: () => null }));
vi.mock('@/components/AISettingsDrawer', () => ({ default: () => null }));
vi.mock('./TopBar', () => ({ default: () => null }));

let container: HTMLDivElement;
let root: Root;
const title = '同一家公司 缺少下一步日程';
const html = () => container.textContent ?? '';
const quickBadge = () => container.querySelector('.ant-badge-count')?.getAttribute('title');
const schedule = () => container.querySelector('section[aria-label="未来 7 天日程"]')!;
const weeklyInterview = () => container.querySelector('button[aria-label^="本周面试，"]')!.getAttribute('aria-label');
const optionIds = () => [...document.querySelectorAll('[role=option][id^="command-palette-option-"]')].map(item => item.id);
const caseIds = (ids: number[]) => ids.map(id => `command-palette-option-pipeline-no-next-${id}`);
const cards = () => [...container.querySelectorAll('button')].filter(button => button.textContent?.includes(title));
const renderState = async (state: typeof mock.events) => {
  mock.events = state;
  await act(async () => root.render(<AppShell />));
};
const show = async (state: typeof mock.events) => {
  await renderState(state);
  await vi.waitFor(() => expect(schedule()).not.toBeNull());
};
const clickText = async (label: string) => {
  const button = [...container.querySelectorAll('button')].find(item => item.textContent?.trim() === label);
  expect(button, label).toBeDefined();
  await act(async () => button!.click());
};
const openPalette = async () => {
  await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true })));
  const input = document.querySelector<HTMLInputElement>('[aria-controls="command-palette-listbox"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '缺少下一步日程');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
};

beforeEach(async () => {
  await Promise.all([import('@/features/dashboard/DashboardView'), import('@/features/reminders/RemindersView')]);
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const computedStyle = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => computedStyle(element));
  mock.eventReaders.length = 0;
  mock.listEvents.mockReset();
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(timestamp));
  setLocale('zh-CN');
  window.history.replaceState(null, '', '/?view=dashboard');
  window.scrollTo = vi.fn();
  window.matchMedia = vi.fn().mockImplementation(() => ({
    matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }));
  HTMLElement.prototype.scrollIntoView = vi.fn();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('AppShell unfiltered calendar evidence', () => {
  it.each([
    ['pending first read', { data: undefined, isLoading: true }, '正在加载日程'],
    ['failed first read', { data: undefined, isError: true }, '加载日程失败'],
    ['malformed successful response', { data: { events: [] } }, '加载日程失败'],
  ] as const)('does not infer empty or absence on %s', async (_case, state, copy) => {
    await show(state);
    expect.soft(schedule().textContent).not.toContain('暂无安排');
    expect.soft(schedule().textContent).toContain(copy);
    expect.soft(weeklyInterview()).toContain('数据暂不可用');
    expect.soft(html()).not.toContain(title);
    expect.soft(html()).toContain('12 道题待复习');
    expect.soft(quickBadge()).toBe('3'); // One independent practice reminder + two mail items.
    await clickText('查看全部');
    expect.soft(cards()).toHaveLength(0);
    expect.soft(html()).toContain('12 道题待复习');
    await openPalette();
    expect.soft(optionIds()).toEqual([]);
  });

  it('treats success [] as proof, withdraws both on failed refresh with stale [], and re-derives by ID on recovery', async () => {
    await show({ data: empty });
    expect.soft(schedule().textContent).toContain('暂无安排');
    expect.soft(weeklyInterview()).not.toContain('数据暂不可用');
    expect.soft(cards()).toHaveLength(2);
    expect.soft(quickBadge()).toBe('5');
    await openPalette();
    expect.soft(optionIds()).toEqual(caseIds([11, 22]));

    await show({ data: empty, isError: true });
    expect.soft(optionIds()).toEqual([]);
    expect.soft(quickBadge()).toBe('3');
    expect.soft(schedule().textContent).not.toContain('暂无安排');
    expect.soft(weeklyInterview()).toContain('数据暂不可用');

    await show({ data: [outsideWindow] });
    expect.soft(optionIds()).toEqual(caseIds([11]));
    expect.soft(quickBadge()).toBe('4');
    expect.soft(schedule().textContent).toContain('暂无安排');
    expect.soft(cards()).toHaveLength(1);

    await show({ data: [withinWindow] });
    expect.soft(optionIds()).toEqual(caseIds([11]));
    expect.soft(schedule().textContent).not.toContain('暂无安排');
    expect.soft(schedule().textContent).toContain('面试');
    expect.soft(html()).toContain('12 道题待复习');

    await show({ data: [event(99, withinWindow.scheduled_at), event(22, withinWindow.scheduled_at, 'done')] });
    expect.soft(optionIds()).toEqual(caseIds([11, 22]));
  });

  it('withdraws stale Reminders cards and opens the exact A card after B is scheduled', async () => {
    await show({ data: empty });
    await clickText('查看全部');
    expect(cards()).toHaveLength(2);
    await renderState({ data: empty, isError: true });
    expect(cards()).toHaveLength(0);
    expect(html()).toContain('12 道题待复习');
    await renderState({ data: [outsideWindow] });
    expect(cards()).toHaveLength(1);
    await act(async () => cards()[0].click());
    expect(container.querySelector('[role="dialog"] output')?.textContent).toBe('no-next-11');
    expect(mock.eventReaders.length).toBeGreaterThan(1); // Shell and mounted Reminders share the full collection key.
    mock.eventReaders.forEach(read => read());
    expect(mock.listEvents.mock.calls).toEqual(mock.eventReaders.map(() => []));
  });
});
