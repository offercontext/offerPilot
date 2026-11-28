// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setLocale } from '@/i18n';
import type { Application } from '@/types/application';
import type { ReminderCandidate } from '@/types/reminders';
import type { InterviewNote } from '@/types/note';
import type { ScheduleEvent } from '@/types/event';
import type { CoreTaskSurfaceController } from '@/features/coreTaskSurface/controller';
import AppShell from './AppShell';

const mock = vi.hoisted(() => ({
  apps: vi.fn(), events: vi.fn(), notes: vi.fn(), save: vi.fn(), warn: vi.fn(), invalidate: vi.fn(),
  noteCache: [] as InterviewNote[], eventCache: [] as ScheduleEvent[], pending: false,
  controller: null as CoreTaskSurfaceController | null,
  noteFetching: false, noteError: false, eventFetching: false,
}));
const timestamp = '2026-11-27T08:30:00Z';
const start = '2026-11-27T06:00:00.123Z';
const startSeconds = Date.parse(start) / 1000;
const makeApp = (id: number, position_name: string): Application => ({
  id, company_name: '同一家公司', position_name, status: 'interview', job_url: '', source: '', notes: '',
  applied_at: '2026-11-20T04:00:00Z', created_at: timestamp, updated_at: timestamp,
});
const apps = [makeApp(11, '岗位 A'), makeApp(22, '岗位 B')];
const event = (application_id: number, id: number, round: number): ScheduleEvent => ({
  id, application_id, scheduled_at: start, status: 'done', event_type: 'interview', subtype: '', tags: [],
  round, duration_minutes: 60, location: '', notes: '', created_at: timestamp,
});
const eventA = event(11, 111, 1);
const firstB = event(22, 221, 1);
const secondB = event(22, 222, 2);
const events = [eventA, firstB, secondB];
const note = (id: number, application_event_id: number | null, application_id = 22): InterviewNote => ({
  id, application_id, application_event_id, company: '同一家公司', position: '岗位 B', round: '', date: '',
  questions: '', self_reflection: 'synthetic', difficulty_points: '', mood: '', revision: 1, created_at: timestamp,
});
const candidate = (ev: ScheduleEvent): ReminderCandidate => ({
  id: `application_event:${ev.id}:review`, subject_type: 'application_event', subject_id: ev.id,
  reason_code: 'review', source_revision: 'v1', application_id: ev.application_id,
  event_id: ev.id, event_type: 'interview', offer_id: null, company: '同一家公司',
  position: ev.application_id === 22 ? '岗位 B' : '岗位 A',
  due_at: startSeconds, display_from: startSeconds, display_until: startSeconds + 86400,
  delivery_due_at: startSeconds, delivery_expires_at: startSeconds + 86400, updated_at: startSeconds, deadline_label: null,
});
let facts: ReminderCandidate[];
const client = {
  getQueryState: ([key, id]: [string, number?]) =>
    key === 'notes' && id === 22 ? { data: mock.noteCache, fetchStatus: mock.noteFetching ? 'fetching' : 'idle', status: mock.noteError ? 'error' : 'success' }
    : key === 'events' && id === 22 ? { data: mock.eventCache, fetchStatus: mock.eventFetching ? 'fetching' : 'idle', status: 'success' } : {},
  setQueryData: ([key]: [string, number], data: ScheduleEvent[] | InterviewNote[]) => {
    if (key === 'events') mock.eventCache = data as ScheduleEvent[];
    if (key === 'notes') mock.noteCache = data as InterviewNote[];
  },
  invalidateQueries: (options: {queryKey: unknown[]}) => {
    mock.invalidate(options);
    if (options.queryKey[0] === 'notes' && options.queryKey[1] === 22) {
      facts = facts.filter(item => !mock.noteCache.some(note => note.application_event_id === item.event_id));
    }
    return Promise.resolve();
  },
};
vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => client,
  useMutation: (options: { mutationFn?: (input: unknown) => Promise<unknown>; onSuccess?: (data: unknown) => void }) => ({
    isPending: false,
    mutate: async (input: unknown, callbacks?: { onSuccess?: () => void }) => {
      const data = await options.mutationFn?.(input);
      options.onSuccess?.(data);
      callbacks?.onSuccess?.();
    },
  }),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const [key, segment] = queryKey;
    if (key === 'events') return { data: segment ? mock.eventCache : events, isFetching: Boolean(segment) && mock.eventFetching };
    if (key === 'applications' && queryKey.length === 1) return { data: apps };
    if (key === 'notes') return { data: mock.noteCache, isFetching: mock.noteFetching, isError: mock.noteError };
    if (key === 'offers' || key === 'mission-control') return { data: [] };
    return { data: undefined };
  },
}));
vi.mock('@/services/applications', async (original) => ({
  ...await original<typeof import('@/services/applications')>(), listApplications: mock.apps,
}));
vi.mock('@/services/events', async (original) => ({
  ...await original<typeof import('@/services/events')>(), listEvents: mock.events,
}));
vi.mock('@/services/notes', async (original) => ({
  ...await original<typeof import('@/services/notes')>(), listNotesByApp: mock.notes, createNote: mock.save,
}));
vi.mock('@/features/coreTaskSurface/controller', async (original) => {
  const api = await original<typeof import('@/features/coreTaskSurface/controller')>();
  return {
    ...api,
    createCoreTaskSurfaceController: (options: Parameters<typeof api.createCoreTaskSurfaceController>[0]) => {
      const controller = api.createCoreTaskSurfaceController({
        ...options, hasPending: active => mock.pending || options?.hasPending?.(active) === true,
      });
      if (!mock.controller) mock.controller = controller;
      return controller;
    },
  };
});
vi.mock('antd', async (original) => ({
  ...await original<typeof import('antd')>(),
  message: { warning: mock.warn, success: vi.fn(), error: vi.fn() },
}));
vi.mock('@/services/persona', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/services/persona')>(),
  getPersona: async () => ({ spec: { display_name: 'Haru' } }),
}));
vi.mock('@/services/companion', () => ({ getCompanion: () => new Promise(() => {}), sendCompanionCommand: vi.fn(), reportCompanionPresence: vi.fn(), claimCompanionBid: vi.fn() }));
vi.mock('@/lib/useReminderCandidates', () => ({ useReminderCandidates: () => ({ data: facts, isPending: false, isError: false }) }));
vi.mock('@/components/ProactiveInbox', () => ({ default: () => null }));
vi.mock('@/features/jobMail/JobMailInbox', () => ({ default: () => null }));
vi.mock('@/features/dashboard/TodayInboxSummary', () => ({ default: () => null }));
vi.mock('@/components/InterviewReviewProposalDrawer', () => ({
  default: ({ note, eventID }: { note: InterviewNote; eventID: number }) => (
    <output data-testid="exact-review-history">{note.id}/{note.application_id}/{eventID}</output>
  ),
}));
vi.mock('@/components/AddApplicationForm', () => ({ default: () => null }));
vi.mock('@/components/ResumeUploadModal', () => ({ default: () => null }));
vi.mock('@/components/AISettingsDrawer', () => ({ default: () => null }));
vi.mock('./TopBar', () => ({ default: () => null }));

let container: HTMLDivElement;
let root: Root;
const body = () => container.textContent ?? '';
const show = async (view?: 'dashboard' | 'reminders') => {
  if (view) window.history.replaceState(null, '', `/?view=${view}`);
  await act(async () => root.render(<AppShell />));
};
const button = (text: string) => [...container.querySelectorAll('button')]
  .find(b => b.textContent?.replace(/\s+/g, '') === text.replace(/\s+/g, ''));
const click = async (label: string) => {
  const target = await vi.waitFor(() => {
    expect(button(label), label).toBeDefined();
    return button(label)!;
  });
  await act(async () => target.click());
};
const bCard = async (round = 2) => {
  const target = await vi.waitFor(() => {
    const card = [...container.querySelectorAll('button')].find(item => item.textContent?.includes('岗位 B') && item.textContent.includes(`第 ${round} 轮`) && item.textContent.includes('面试复盘提醒'));
    expect(card, body()).toBeDefined();
    return card!;
  });
  await act(async () => target.click());
};
const cta = '记录这场面试的复盘';
const owner = () => container.querySelector('[data-core-task-key="application.interview_review:applicationId=22:eventId=222"]');
const form = () => owner()?.querySelector('section[aria-label="新建面试复盘"]');
beforeEach(async () => {
  await Promise.all([import('@/features/dashboard/DashboardView'), import('@/features/reminders/RemindersView'), import('@/components/ApplicationDetail')]);
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const computedStyle = window.getComputedStyle;
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => computedStyle(element));
  vi.clearAllMocks();
  mock.controller = null;
  mock.pending = false;
  mock.noteFetching = false;
  mock.noteError = false;
  mock.eventFetching = false;
  mock.apps.mockResolvedValue(apps);
  mock.events.mockResolvedValue([firstB, secondB]);
  mock.notes.mockResolvedValue([]);
  mock.eventCache = [firstB, secondB];
  mock.noteCache = [];
  mock.save.mockImplementation(async () => {
    const saved = note(77, secondB.id);
    mock.noteCache = [saved];
    return saved;
  });
  facts = events.map(candidate);
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

describe('exact review reminder across mounted surfaces', () => {
  it.each(['dashboard', 'reminders'] as const)('%s opens B round 2 from the card, not its same-company neighbours', async view => {
    await show(view);
    await bCard();
    expect(body()).toContain('岗位 B · 第 2 轮 · 面试复盘提醒');
    expect(button('打开日历')).toBeDefined();
    await click(cta);
    await vi.waitFor(() => expect(form()).toBeTruthy());
    expect(mock.events).toHaveBeenCalledWith({ application_id: 22 });
    expect(mock.notes).toHaveBeenCalledWith(22);
    expect(form()?.textContent).toContain('第 2 轮');
    expect(form()?.querySelectorAll('[role="combobox"]:disabled').length).toBeGreaterThanOrEqual(2);
    expect(mock.save).not.toHaveBeenCalled();
    expect(window.location.search).toBe('?view=board');
  });

  it('uses only the existing note for the exact same event found on click', async () => {
    await show('reminders');
    await bCard();
    expect(mock.notes).not.toHaveBeenCalled();
    // This note arrives between painting the candidate and pressing its action.
    mock.notes.mockResolvedValue([note(87, 221), note(88, 222)]);
    await click(cta);
    await vi.waitFor(() => expect(owner()?.querySelector('[data-testid="exact-review-history"]')?.textContent, owner()?.textContent ?? body()).toBe('88/22/222'));
    expect(form()).toBeFalsy();
    expect(mock.save).not.toHaveBeenCalled();
  });

  it.each(['missing', 'cancelled', 'event read rejected', 'note read rejected', 'malformed notes', 'moved within the same second', 'expired'] as const)('does not launch or write when %s', async fault => {
    if (fault === 'missing') mock.events.mockResolvedValue([firstB]);
    if (fault === 'cancelled') mock.events.mockResolvedValue([firstB, { ...secondB, status: 'cancelled' }]);
    if (fault === 'moved within the same second') {
      mock.events.mockResolvedValue([firstB, { ...secondB, scheduled_at: start.replace('.123', '.987') }]);
    }
    if (fault === 'event read rejected') mock.events.mockRejectedValue(new Error('event unavailable'));
    if (fault === 'note read rejected') mock.notes.mockRejectedValue(new Error('note unavailable'));
    if (fault === 'malformed notes') mock.notes.mockResolvedValue([note(1, 222, 11)]);
    await show('reminders');
    await bCard();
    if (fault === 'expired') vi.setSystemTime(new Date((startSeconds + 86400) * 1000));
    await click(cta);
    expect(owner()).toBeNull();
    expect(mock.warn).toHaveBeenCalled();
    expect(mock.save).not.toHaveBeenCalled();
    expect(button('打开日历')).toBeDefined();
  });

  it('drops a delayed read when browser history moves to a different surface', async () => {
    let resolve!: (value: InterviewNote[]) => void;
    mock.notes.mockImplementation(() => new Promise<InterviewNote[]>(done => { resolve = done; }));
    await show('reminders');
    await bCard();
    await click(cta);
    await act(async () => {
      window.history.replaceState(null, '', '/?view=dashboard');
      window.dispatchEvent(new PopStateEvent('popstate'));
      resolve([]);
    });
    expect(owner()).toBeNull();
    expect(window.location.search).toBe('?view=dashboard');
    expect(mock.save).not.toHaveBeenCalled();
  });

  const installOtherOwner = () => {
    const controller = mock.controller!;
    const result = controller.launch({ ref: { taskId: 'application.interview_review', applicationId: 11, eventId: 111 }, source: 'application_task_card' });
    expect(result.kind).toBe('launched');
    return controller.getState().active;
  };

  it('honors a guarded active task and never falls through to a new owner', async () => {
    await show('reminders');
    await bCard();
    let existing: ReturnType<typeof installOtherOwner>;
    await act(async () => { existing = installOtherOwner(); mock.pending = true; });
    await click(cta);
    expect(mock.controller?.getState().active).toBe(existing!);
    expect(mock.warn).toHaveBeenCalled();
    expect(owner()).toBeNull();
    expect(mock.save).not.toHaveBeenCalled();
  });

  it('drops a delayed result if another task acquires the owner on the same route', async () => {
    let resolve!: (value: InterviewNote[]) => void;
    mock.notes.mockImplementation(() => new Promise<InterviewNote[]>(done => { resolve = done; }));
    await show('reminders');
    await bCard();
    await click(cta);
    let existing: ReturnType<typeof installOtherOwner>;
    await act(async () => { existing = installOtherOwner(); resolve([]); });
    expect(mock.controller?.getState().active).toBe(existing!);
    expect(window.location.search).toBe('?view=reminders');
    expect(owner()).toBeNull();
    expect(mock.save).not.toHaveBeenCalled();
  });

  const reflection = () => form()?.querySelector<HTMLTextAreaElement>('textarea[id="self_reflection"]');
  const typeReflection = async (value: string) => {
    const input = reflection()!;
    expect(input).toBeDefined();
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  };

  it('preserves the current draft through a scoped recheck, fences saving and denies replacement while dirty', async () => {
    await show('reminders');
    await bCard();
    await click(cta);
    await vi.waitFor(() => expect(reflection()).toBeTruthy());
    await typeReflection('draft stays with B round 2');
    mock.noteFetching = true;
    await click('保存复盘'); // Before the observer renders pending, the write boundary must already know.
    expect(mock.save).not.toHaveBeenCalled();
    await show();
    expect(form()).toBeFalsy();
    mock.noteFetching = false;
    mock.noteError = true;
    await show();
    expect(form()).toBeFalsy();
    mock.noteError = false;
    mock.eventFetching = true;
    await show();
    expect(form()).toBeFalsy();
    mock.eventFetching = false;
    await show();
    expect(reflection()?.value).toBe('draft stays with B round 2');
    const prev = mock.controller?.getState().active;
    await act(async () => {
      expect(mock.controller?.launch({ ref: { taskId: 'application.interview_review', applicationId: 11, eventId: 111 },
        source: 'application_task_card' }).kind).toBe('replacement_denied');
    });
    expect(mock.controller?.getState().active).toBe(prev);
    await click('取消');
    await act(async () => {
      window.history.replaceState(null, '', '/?view=reminders');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await bCard(1);
    await click(cta);
    await vi.waitFor(() => expect(container.querySelector('[data-core-task-key="application.interview_review:applicationId=22:eventId=221"] textarea[id="self_reflection"]')).toBeTruthy());
    expect(container.querySelector<HTMLTextAreaElement>('textarea[id="self_reflection"]')?.value).toBe('');
    expect(mock.save).not.toHaveBeenCalled();
  });

  it('changes a revalidated draft owner to exact history if a note has appeared, never creates another note', async () => {
    await show('reminders');
    await bCard();
    await click(cta);
    await vi.waitFor(() => expect(reflection()).toBeTruthy());
    await typeReflection('draft before external note');
    mock.noteCache = [note(87, 221), note(88, 222)];
    await show();
    expect(owner()?.querySelector('[data-testid="exact-review-history"]')?.textContent).toBe('88/22/222');
    expect(form()).toBeFalsy();
    expect(mock.save).not.toHaveBeenCalled();
  });

  it('saves just the locked review on explicit click and refreshes only the matched reminder', async () => {
    await show('reminders');
    await bCard();
    await click(cta);
    await vi.waitFor(() => expect(form()).toBeTruthy());
    expect(mock.save).not.toHaveBeenCalled();
    await typeReflection('B round 2 explicit note');
    const save = button('保存复盘')!;
    await act(async () => { save.click(); save.click(); });
    await vi.waitFor(() => expect(mock.save).toHaveBeenCalledTimes(1));
    expect(mock.save).toHaveBeenCalledWith(22, expect.objectContaining({ application_id: 22, application_event_id: 222, self_reflection: 'B round 2 explicit note' }));
    expect(mock.invalidate).toHaveBeenCalledWith({ queryKey: ['notes', 22] });
    expect(facts.map(fact => fact.event_id)).toEqual([111, 221]);
  });
});

