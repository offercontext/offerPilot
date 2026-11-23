// @vitest-environment jsdom
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AppShell from './AppShell';

const mailReads = vi.hoisted(() => ({ apps: vi.fn(), events: vi.fn(), revision: 1, hideApps: false, cancelledB: false }));
vi.mock('@/services/applications', () => ({ listApplications: mailReads.apps }));
vi.mock('@/services/events', () => ({ listEvents: mailReads.events }));
vi.mock('@/features/reminders/RemindersView', async () => {
  const { useEffect, useRef } = await import('react');
  return { default: (props: any) => {
    const live = useRef(true);
    useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
    return <button data-testid="mail-prepare" onClick={() => void props.onOpenInterviewPreparation(7, 42, () => live.current)}>Mail B</button>;
  } };
});

const app = {
  id: 7,
  company_name: 'ByteDance',
  position_name: 'Backend',
  applied_at: '2026-07-10T09:00:00Z',
};

const queryClientState = vi.hoisted(() => ({
  getQueryState: () => ({ dataUpdateCount: mailReads.revision }),
  getQueryCache: () => ({ subscribe: () => () => undefined }),
  invalidateQueries: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({
  useMutation: () => ({ isPending: false, mutate: vi.fn() }),
  useQuery: (options: any) => {
    const key = options.queryKey?.[0];
    const dataByKey: Record<string, unknown> = {
      applications: mailReads.hideApps ? [] : [app],
      events: [{
        id: 11,
        application_id: 7,
        event_type: 'interview',
        subtype: 'technical',
        tags: [],
        round: 1,
        // Keep the fixture executable under the canonical lifecycle/card
        // gate used by exact task launches.
        scheduled_at: '2099-01-01T09:00:00Z',
        duration_minutes: 60,
        location: '线上',
        notes: '',
        status: 'todo',
        created_at: '2026-01-01T00:00:00Z',
      }],
      offers: [],
      questions: undefined,
    };
    if (key === 'events' && mailReads.cancelledB) dataByKey[key] = [{ id: 42, application_id: 7, event_type: 'interview', status: 'done', scheduled_at: '2099-02-01T09:00:00Z', duration_minutes: 60 }];
    return { data: dataByKey[key], isError: false, isLoading: false, dataUpdatedAt: mailReads.revision };
  },
  useQueryClient: () => queryClientState,
}));

vi.mock('@dnd-kit/core', () => ({
  DndContext: (props: any) => <div>{props.children}</div>,
  PointerSensor: class PointerSensor {},
  useSensor: () => ({}),
  useSensors: () => ({}),
}));

vi.mock('antd', () => {
  const Layout = Object.assign(
    (props: any) => <div>{props.children}</div>,
    { Content: (props: any) => <main>{props.children}</main> },
  );
  const Typography = {
    Paragraph: (props: any) => <p>{props.children}</p>,
    Text: (props: any) => <span>{props.children}</span>,
    Title: (props: any) => <h2>{props.children}</h2>,
  };
  return {
    Button: (props: any) => <button type="button" onClick={props.onClick}>{props.children}</button>,
    Layout,
    Spin: () => <div>loading</div>,
    Tabs: () => <div />,
    Typography,
    message: { warning: vi.fn(), success: vi.fn(), error: vi.fn() },
  };
});

vi.mock('./Sidebar', () => ({
  default: (props: any) => (
    <nav>
      <button data-testid="nav-reminders" onClick={() => props.onChange('reminders')}>Mail</button>
      <button type="button" data-testid="nav-pilot" onClick={() => props.onChange('pilot')}>Pilot</button>
      <button type="button" data-testid="nav-board" onClick={() => props.onChange('board')}>Board</button>
      <button type="button" data-testid="nav-offers" onClick={() => props.onChange('offers')}>Offers</button>
      <button type="button" data-testid="nav-interview" onClick={() => props.onChange('interview')}>Interview</button>
    </nav>
  ),
}));
vi.mock('./TopBar', () => ({ default: () => <div /> }));
vi.mock('./CommandPalette', () => ({ default: () => <div /> }));
vi.mock('@/components/AddApplicationForm', () => ({ default: () => <div /> }));
vi.mock('@/components/ResumeUploadModal', () => ({ default: () => <div /> }));
vi.mock('@/components/AISettingsDrawer', () => ({ default: () => <div /> }));
vi.mock('@/components/ApplicationDetail', () => ({
  default: (props: any) => (
    <section data-testid="application-detail" data-task-event-id={props.interviewPreparationSelection?.eventId ?? 'none'}>
      {props.application.id}
      {props.pilotInterviewPreparationApplicationId ? (
        <section
          data-testid="interview-preparation-drawer"
          data-application-id={props.pilotInterviewPreparationApplicationId}
          data-event-id={props.pilotInterviewPreparationEventId ?? 'none'}
        />
      ) : null}
      <button type="button" data-testid="close-application" onClick={props.onClose}>Close</button>
      <button type="button" data-testid="open-pilot-opportunity-fit" onClick={() => props.onOpenPilotOpportunityFit?.(props.application)}>Evaluate</button>
    </section>
  ),
}));
vi.mock('@/components/InterviewV01View', () => ({
  default: (props: any) => (
    <section data-testid="interview-index">
      <button type="button" data-testid="open-interview-preparation" onClick={() => props.onOpenPreparation?.(7, 11)}>
        Prepare interview
      </button>
    </section>
  ),
}));
vi.mock('@/features/pilot/PilotOpportunityFitV2Card', () => ({
  default: (props: any) => (
    <section
      data-testid="pilot-opportunity-fit-v2-card"
      data-status={props.status}
      data-summary={props.summary ?? ''}
    >
      <button type="button" data-testid="open-pilot-owner" onClick={props.onOpenTask}>打开岗位判断</button>
    </section>
  ),
}));
vi.mock('@/features/interviewReadiness/InterviewReadinessCenter', () => ({
  default: (props: any) => (
    <section
      data-testid="interview-readiness-center"
      data-application-id={props.lockedEvent?.applicationId ?? 'none'}
      data-event-id={props.lockedEvent?.eventId ?? 'none'}
    ><button data-testid="choose-mail-resume" onClick={() => props.onOpenTask({ ref: { taskId: 'application.interview_prepare', applicationId: props.lockedEvent.applicationId, eventId: props.lockedEvent.eventId }, source: 'interview_event_card', hints: { suggestedResumeId: 3 } })}>Choose resume</button></section>
  ),
}));
vi.mock('@/components/ChatPanel', () => ({
  default: (props: any) => (
    <section data-testid={`chat-${props.variant ?? 'drawer'}`}>
      <button
        type="button"
        data-testid={`open-offer-${props.variant ?? 'drawer'}`}
        onClick={() => props.onOpenEvidence?.({ kind: 'offer', id: 9 })}
      >
        Open offer
      </button>
      <button
        type="button"
        data-testid={`open-application-${props.variant ?? 'drawer'}`}
        onClick={() => props.onOpenEvidence?.({ kind: 'application', id: 7 })}
      >
        Open application
      </button>
      <button
        type="button"
        data-testid={`refresh-pilot-${props.variant ?? 'drawer'}`}
        onClick={() => props.onDataChanged?.()}
      >
        Refresh Pilot data
      </button>
    </section>
  ),
}));
vi.mock('@/components/KanbanBoard', () => ({ default: () => <div data-testid="board" /> }));
vi.mock('@/components/OfferCenterView', () => ({
  default: (props: any) => <output data-testid="offer-focus">{props.focusOfferId ?? 'none'}</output>,
}));
vi.mock('@/features/dashboard/DashboardView', () => ({
  default: (props: any) => (
    <DashboardTestHarness props={props} />
  ),
}));

function DashboardTestHarness({ props }: { props: any }) {
  useEffect(() => {
    if (props.suggestionSessionStates?.['7:application_detail']) {
      props.onPruneDisposition?.(7, 'application_detail', 'current-state');
    }
  }, [props]);
  return (
    <div data-testid="dashboard">
      <button
        type="button"
        data-testid="open-dashboard-application"
        onClick={() => props.onOpenDetailById?.(7)}
      />
      <button
        type="button"
        data-testid="readonly-opportunity-history"
        onClick={() => props.onNextStepReadonlyNavigate?.({ kind: 'opportunity_fit_history', applicationId: 7, reviewId: 12 })}
      />
      <button
        type="button"
        data-testid="set-stale-disposition"
        onClick={() => props.onSetDisposition?.(7, 'application_detail', { stateKey: 'stale-state', disposition: 'ignored' })}
      />
      <output data-testid="suggestion-session-state">
        {props.suggestionSessionStates?.['7:application_detail']?.stateKey ?? ''}
      </output>
    </div>
  );
}
vi.mock('@/features/pilot/PilotAttachmentContext', () => ({
  PilotAttachmentProvider: (props: any) => <>{props.children}</>,
  usePilotAttachmentStore: () => ({ addAttachment: vi.fn(), createNewDraftWithAttachment: vi.fn() }),
}));
vi.mock('@/features/pilot/attachmentHandoff', () => ({ retainPilotAttachmentKey: (_current: any, next: any) => next }));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | undefined;
let root: Root | undefined;

function render(ui: React.ReactNode) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root?.render(ui));
  return container;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  mailReads.revision = 1; mailReads.hideApps = false; mailReads.cancelledB = false;
  window.history.replaceState(null, '', '/');
  window.matchMedia = () => ({
    addEventListener: () => undefined,
    matches: false,
    removeEventListener: () => undefined,
  }) as unknown as MediaQueryList;
  window.scrollTo = vi.fn();
});

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = undefined;
  container = undefined;
  vi.clearAllMocks();
});

describe('AppShell evidence navigation', () => {
  it('opens one Application-scoped Pilot owner and keeps the bounded projection across view changes', async () => {
    const view = render(<AppShell />);
    await flush();

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="open-dashboard-application"]')?.click());
    await flush();
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="open-pilot-opportunity-fit"]')?.click());
    await flush();
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-pilot"]')?.click());
    await flush();

    const card = view.querySelector('[data-testid="pilot-opportunity-fit-v2-card"]');
    expect(card).not.toBeNull();
    expect(card?.getAttribute('data-status')).toBe('idle');
    expect(card?.getAttribute('data-summary')).toBe('');

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-board"]')?.click());
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-pilot"]')?.click());
    await flush();
    expect(view.querySelector('[data-testid="pilot-opportunity-fit-v2-card"]')?.getAttribute('data-status')).toBe('idle');
  });

  it('invalidates the same-month calendar query after Pilot data changes', async () => {
    const view = render(<AppShell />);
    await flush();

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-pilot"]')?.click());
    await flush();
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="refresh-pilot-page"]')?.click());

    expect(queryClientState.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['calendar'] });
  });

  it('cancels unresolved non-application focus when a later application opens from narrow Pilot', async () => {
    const view = render(<AppShell />);
    await flush();

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-pilot"]')?.click());
    await flush();
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="open-offer-page"]')?.click());
    await flush();

    expect(view.querySelector('[data-testid="offer-focus"]')?.textContent).toBe('9');
    const applicationButton = view.querySelector<HTMLButtonElement>('[data-testid="open-application-drawer"]');
    expect(applicationButton).toBeInstanceOf(HTMLButtonElement);
    act(() => applicationButton?.click());
    await flush();

    expect(view.querySelector('[data-testid="application-detail"]')?.textContent).toContain('7');
    expect(view.querySelector('[data-testid="chat-drawer"]')).not.toBeNull();

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="close-application"]')?.click());
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-offers"]')?.click());
    await flush();

    expect(view.querySelector('[data-testid="offer-focus"]')?.textContent).toBe('none');
  });

  it('opens the canonical preparation center for the selected interview event from the top-level index', async () => {
    const view = render(<AppShell />);
    await flush();

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-interview"]')?.click());
    await flush();
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="open-interview-preparation"]')?.click());
    await flush();

    const preparation = view.querySelector('[data-testid="interview-readiness-center"]');
    expect(preparation?.getAttribute('data-application-id')).toBe('7');
    expect(preparation?.getAttribute('data-event-id')).toBe('11');
  });

  it('clears an errored evidence target when the user leaves its destination', async () => {
    const view = render(<AppShell />);
    await flush();

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-pilot"]')?.click());
    await flush();
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="open-offer-page"]')?.click());
    await flush();

    expect(view.querySelector('[data-testid="offer-focus"]')?.textContent).toBe('9');
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-board"]')?.click());
    await flush();
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-offers"]')?.click());
    await flush();

    expect(view.querySelector('[data-testid="offer-focus"]')?.textContent).toBe('none');
  });

  it('keeps application drill-down available without turning unsupported source-risk navigation into a write flow', async () => {
    const view = render(<AppShell />);
    await flush();

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="open-dashboard-application"]')?.click());
    await flush();
    expect(view.querySelector('[data-testid="application-detail"]')?.textContent).toContain('7');

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="close-application"]')?.click());
    await flush();
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="readonly-opportunity-history"]')?.click());
    await flush();
    expect(view.querySelector('[data-testid="application-detail"]')).toBeNull();
  });

  it('removes a stale disposition from the mounted AppShell state table', async () => {
    const view = render(<AppShell />);
    await flush();

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="set-stale-disposition"]')?.click());
    await flush();

    expect(view.querySelector('[data-testid="suggestion-session-state"]')?.textContent).toBe('');
  });
});


describe('JobMail exact preparation in mounted shell', () => {
  const eventB = { id: 42, application_id: 7, event_type: 'interview', scheduled_at: '2099-02-01T09:00:00Z', duration_minutes: 60, status: 'todo' };
  async function openMail() { const view = render(<AppShell />); await flush(); act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-reminders"]')!.click()); await flush(); return view; }
  it('carries freshly read B into locked readiness despite shell cache containing only A', async () => {
    mailReads.apps.mockResolvedValue([app]); mailReads.events.mockResolvedValue([{ ...eventB, id: 11 }, eventB]);
    const view = await openMail(); act(() => view.querySelector<HTMLButtonElement>('[data-testid="mail-prepare"]')!.click()); await flush(); await flush();
    const center = view.querySelector('[data-testid="interview-readiness-center"]');
    expect(center?.getAttribute('data-event-id')).toBe('42'); expect(center?.getAttribute('data-application-id')).toBe('7');
    expect(mailReads.events).toHaveBeenCalledWith({ application_id: 7 });
  });
  it('keeps exact B through explicit resume selection with a newly visible uncached application', async () => {
    mailReads.hideApps = true; mailReads.apps.mockResolvedValue([app]); mailReads.events.mockResolvedValue([eventB]);
    const view = await openMail(); act(() => view.querySelector<HTMLButtonElement>('[data-testid="mail-prepare"]')!.click()); await flush(); await flush();
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="choose-mail-resume"]')!.click()); await flush();
    expect(view.querySelector('[data-testid="application-detail"]')?.getAttribute('data-task-event-id')).toBe('42');
  });
  it('rejects a completed scoped read if a newer global refresh arrived while applications were pending', async () => {
    let resolve!: (apps: unknown[]) => void;
    mailReads.apps.mockReturnValue(new Promise(r => { resolve = r; })); mailReads.events.mockResolvedValue([eventB]);
    const view = await openMail(); act(() => view.querySelector<HTMLButtonElement>('[data-testid="mail-prepare"]')!.click()); await flush();
    // Simulate QueryClient updating before React renders the newer source.
    mailReads.revision += 1;
    await act(async () => resolve([app])); await flush();
    expect(view.querySelector('[data-testid="interview-readiness-center"]')).toBeNull();
  });
  it('revokes the locked mail target when a later refresh cancels B', async () => {
    mailReads.apps.mockResolvedValue([app]); mailReads.events.mockResolvedValue([eventB]);
    const view = await openMail(); act(() => view.querySelector<HTMLButtonElement>('[data-testid="mail-prepare"]')!.click()); await flush(); await flush();
    mailReads.cancelledB = true; mailReads.revision += 1; act(() => root!.render(<AppShell />)); await flush();
    expect(view.querySelector('[data-testid="interview-readiness-center"]')?.getAttribute('data-event-id')).toBe('none');
  });
  it('does not let its fresh seed override a later authoritative cache refresh', async () => {
    mailReads.apps.mockResolvedValue([app]); mailReads.events.mockResolvedValue([eventB]);
    const view = await openMail(); act(() => view.querySelector<HTMLButtonElement>('[data-testid="mail-prepare"]')!.click()); await flush(); await flush();
    expect(view.querySelector('[data-testid="interview-readiness-center"]')?.getAttribute('data-event-id')).toBe('42');
    mailReads.revision += 1;
    act(() => root!.render(<AppShell />)); await flush();
    expect(view.querySelector('[data-testid="interview-readiness-center"]')?.getAttribute('data-event-id')).toBe('none');
  });
  it.each([[], [{ ...eventB, application_id: 8 }], [{ ...eventB, status: 'done' }], [eventB, eventB]].map(events => ({ events })))('does not fall back to cached A for unusable fresh B %j', async ({ events }) => {
    mailReads.apps.mockResolvedValue([app]); mailReads.events.mockResolvedValue(events);
    const view = await openMail(); act(() => view.querySelector<HTMLButtonElement>('[data-testid="mail-prepare"]')!.click()); await flush();
    expect(view.querySelector('[data-testid="interview-readiness-center"]')).toBeNull();
    expect(view.querySelector('[data-testid="mail-prepare"]')).not.toBeNull();
  });
  it('ignores late reads after navigation even if source still reports current', async () => {
    let resolve!: (events: unknown[]) => void;
    mailReads.apps.mockResolvedValue([app]); mailReads.events.mockReturnValue(new Promise((r) => { resolve = r; }));
    const view = await openMail(); act(() => view.querySelector<HTMLButtonElement>('[data-testid="mail-prepare"]')!.click());
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="nav-board"]')!.click());
    await act(async () => resolve([eventB])); await flush();
    expect(view.querySelector('[data-testid="interview-readiness-center"]')).toBeNull();
  });
  it('cannot win over Back when reads resolve before unmount commit', async () => {
    let resolve!: (events: unknown[]) => void;
    mailReads.apps.mockResolvedValue([app]); mailReads.events.mockReturnValue(new Promise((r) => { resolve = r; }));
    const view = await openMail();
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="mail-prepare"]')!.click());
    await act(async () => {
      window.history.replaceState(null, '', '/?view=board');
      window.dispatchEvent(new PopStateEvent('popstate'));
      expect(view.querySelector('[data-testid="mail-prepare"]')).not.toBeNull();
      resolve([eventB]);
      for (let i = 0; i < 3; i++) await Promise.resolve();
    });
    await flush();
    expect(view.querySelector('[data-testid="interview-readiness-center"]')).toBeNull();
    expect(view.querySelector('[data-testid="board"]')).not.toBeNull();
    expect(new URL(window.location.href).searchParams.get('view')).toBe('board');
  });
});
