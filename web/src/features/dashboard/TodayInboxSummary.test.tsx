// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProactiveJob } from '@/types/proactive';
const query = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
vi.mock('@tanstack/react-query', () => ({ useQuery: () => query.current }));
vi.mock('@/features/jobMail/JobMailPendingSummary', () => ({ default: ({ onOpen }: { onOpen: () => void }) => <button onClick={onOpen}>邮件待审</button> }));
vi.mock('antd', () => ({
  Button: ({ children, onClick }: { children: React.ReactNode; onClick: () => void }) => <button onClick={onClick}>{children}</button>,
  Alert: ({ message }: { message: string }) => <div role="alert">{message}</div>,
}));
import TodayInboxSummary, { summarizeTodayJobs } from './TodayInboxSummary';
const job = (state: ProactiveJob['state'], overrides: Partial<ProactiveJob> = {}): ProactiveJob => ({
  id: state, kind: 'deadline', application_id: 1, event_id: 2, state, reason: '', due_at: 1,
  created_at: 1, result_text: '', turn_id: '', execution_generation: 1, error_code: '', ...overrides,
});
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  (globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
describe('today inbox summary', () => {
  it('separates active work, failures/unknown and completed drafts without counting reminders as drafts', () => {
    expect(summarizeTodayJobs([
      job('pending'), job('leased'), job('running'), job('failed'), job('result_unknown'), job('cancelled'),
      job('succeeded', { result_text: 'published reminder' }),
      job('succeeded', { kind: 'interview_draft', result_text: 'draft' }),
      job('succeeded', { kind: 'interview_draft', result_text: '   ' }),
    ])).toEqual({ active: 3, attention: 2, drafts: 1 });
  });
  it('excludes every companion state from job counts without dropping job work', async () => {
    const companionJobs = (['pending', 'leased', 'running', 'succeeded', 'failed', 'result_unknown', 'cancelled'] as const)
      .map((state) => job(state, { kind: 'companion_life', application_id: 0, result_text: 'private virtual activity' }));
    expect(summarizeTodayJobs(companionJobs)).toEqual({ active: 0, attention: 0, drafts: 0 });
    query.current = { data: [...companionJobs, job('pending'), job('failed'), job('succeeded', { kind: 'interview_draft', result_text: 'work draft' })] };
    await act(async () => root.render(<TodayInboxSummary onOpen={vi.fn()} />));
    expect(host.textContent).toContain('进行中 1 · 需要检查 1 · 准备草稿 1');
    expect(host.textContent).not.toContain('private virtual activity');
  });
  it('does not show cached success counts after a read failure and keeps one destination', async () => {
    const onOpen = vi.fn();
    query.current = { isPending: true };
    await act(async () => root.render(<TodayInboxSummary onOpen={onOpen} />));
    expect(host.querySelector('[role="status"]')).not.toBeNull();
    query.current = { isError: true, data: [job('running')] };
    await act(async () => root.render(<TodayInboxSummary onOpen={onOpen} />));
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(host.textContent).not.toContain('进行中 1');
    await act(async () => host.querySelectorAll('button').forEach((button) => button.click()));
    expect(onOpen).toHaveBeenCalledTimes(2);
  });
});
