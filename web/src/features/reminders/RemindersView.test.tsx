// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setLocale } from '@/i18n';
import rawCorpus from '../../../../contracts/reminder-candidates.v1.json?raw';
import type { ReminderCandidate } from '@/types/reminders';

const { query, retry } = vi.hoisted(() => ({ query: { current: {} as Record<string, unknown> }, retry: vi.fn() }));
vi.mock('@/lib/useReminderCandidates', () => ({ useReminderCandidates: () => query.current }));
vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({ data: [] }) }));
vi.mock('@/features/jobMail/JobMailInbox', () => ({ default: () => <div data-testid="mail" /> }));
vi.mock('@/components/ProactiveInbox', () => ({ default: () => <div data-testid="proactive" /> }));
vi.mock('@/features/pipeline/ActionDetailDrawer', () => ({ default: ({ insight }: { insight: { id: string } }) => <div role="dialog">{insight.id}</div> }));
vi.mock('antd', () => ({ Input: { Search: () => <input /> },
  Select: ({ value, options, onChange }: { value: string; options: Array<{ value: string; label: string }>; onChange: (value: string) => void }) =>
    <select value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>,
}));
import RemindersView from './RemindersView';

let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const fact: ReminderCandidate = { id: 'application:1:stale_application', subject_type: 'application',
  subject_id: 1, reason_code: 'stale_application', source_revision: 'v1', application_id: 1,
  event_id: null, event_type: null, offer_id: null, company: 'Acme', position: 'Engineer', due_at: 1,
  display_from: 1, display_until: null, delivery_due_at: 1, delivery_expires_at: 2,
  updated_at: 0, deadline_label: null };
const render = async () => { await act(async () => root.render(<RemindersView onNavigate={vi.fn()} onOpenDetailById={vi.fn()} />)); };
beforeEach(() => {
  (globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-09T04:00:00Z'));
  setLocale('zh-CN');
  query.current = { data: undefined, isPending: true, isError: false, refetch: retry };
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); setLocale('zh-CN'); });

describe('today reminder facts', () => {
  it('distinguishes loading, failure with old cache, and an empty successful response', async () => {
    await render();
    expect(container.querySelector('[role="status"]')).not.toBeNull();
    expect(container.textContent).not.toContain('暂无匹配');
    query.current = { data: [fact], isPending: false, isError: true, refetch: retry };
    await render();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.textContent).not.toContain('Acme');
    expect(container.textContent).not.toContain('暂无匹配');
    await act(async () => container.querySelector('button')!.click());
    expect(retry).toHaveBeenCalledTimes(1);
    query.current = { data: [], isPending: false, isError: false, refetch: retry };
    await render();
    expect(container.textContent).toContain('暂无匹配');
    expect(container.querySelector('[data-testid="proactive"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="mail"]')).not.toBeNull();
  });

  it('keeps ordinary events filterable and translated separately from interviews', async () => {
    const corpus = JSON.parse(rawCorpus) as { cases: Array<{ name: string; expected: ReminderCandidate[] }> };
    const facts = corpus.cases.find((item) => item.name === 'display_events_24h')!.expected;
    query.current = { data: [fact, ...facts], isPending: false, isError: false, refetch: retry };
    await render();
    const select = container.querySelector('select')!;
    await act(async () => { select.value = 'event_upcoming'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(container.textContent).toContain('笔试 将在 24 小时后开始。');
    expect(container.textContent).not.toContain('需要跟进');
    expect(container.querySelectorAll('button')).toHaveLength(3);
    await act(async () => setLocale('en-US'));
    expect(container.querySelector('option[value="event_upcoming"]')?.textContent).toBe('Upcoming events');
    expect(container.textContent).not.toContain('面试临近，可提前准备');
    await act(async () => container.querySelector('button')!.click());
    expect(container.querySelector('[role="dialog"]')?.textContent).toBe(facts[0].id);
  });

  it('closes the selected action when its source is withdrawn', async () => {
    query.current = { data: [fact], isPending: false, isError: false, refetch: retry };
    await render();
    await act(async () => container.querySelector('button')!.click());
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    query.current = { data: [], isPending: false, isError: false, refetch: retry };
    await render();
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });
});
