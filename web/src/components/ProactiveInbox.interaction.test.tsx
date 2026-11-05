// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setLocale } from '@/i18n';
import type { ProactiveJob } from '@/types/proactive';
import { cancelProactiveJob } from '@/services/proactive';
import ProactiveInbox from './ProactiveInbox';

const fixture = vi.hoisted(() => ({ jobs: [] as ProactiveJob[], invalidate: vi.fn(), refetch: vi.fn() }));
vi.mock('@/services/proactive', () => ({ cancelProactiveJob: vi.fn(), listProactiveJobs: vi.fn(), getProactiveSettings: vi.fn() }));
vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[0] === 'proactive-jobs' ? fixture.jobs : queryKey[0] === 'applications' ? [] : { settings: { timezone: 'UTC' } },
    isPending: false, isError: false, isFetching: false, refetch: fixture.refetch,
  }),
  useQueryClient: () => ({ invalidateQueries: fixture.invalidate }),
}));
const job = (id: string, state: ProactiveJob['state'], kind: ProactiveJob['kind'] = 'deadline'): ProactiveJob => ({
  id, kind, state, application_id: kind === 'companion_life' ? 0 : 101, event_id: null,
  due_at: 1, created_at: 1, reason: `reason-${id}`, result_text: state === 'succeeded' ? `result-${id}` : '',
  turn_id: '', execution_generation: 1, error_code: '',
});
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.clearAllMocks(); setLocale('zh-CN');
  fixture.jobs = [];
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }) });
  host = document.body.appendChild(document.createElement('div')); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); setLocale('zh-CN'); });
const render = async (representedCandidateIds: string[] = []) => { await act(async () => root.render(<ProactiveInbox representedCandidateIds={representedCandidateIds} />)); };

it('does not expose any life state, history or false merged-reminder hint in the job inbox', async () => {
  fixture.jobs = (['pending', 'leased', 'running', 'succeeded', 'failed', 'result_unknown', 'cancelled'] as const)
    .map((state) => job(`life-${state}`, state, 'companion_life'));
  await render();
  expect(host.querySelectorAll('article')).toHaveLength(0);
  expect(host.querySelector('details')).toBeNull();
  expect(host.textContent).toContain('还没有主动任务');
  expect(host.textContent).not.toContain('相关提醒已合并到待办列表');
  expect(host.textContent).not.toContain('投递 #0');
  expect(host.textContent).not.toContain('Haru');
  await act(async () => { await setLocale('en-US'); });
  expect(host.textContent).not.toMatch(/\p{Script=Han}/u);
  expect(cancelProactiveJob).not.toHaveBeenCalled();
});

it('keeps job failures and drafts current, with one collapsed history and no represented reminder duplicate', async () => {
  const represented = { ...job('represented', 'succeeded'), candidate_id: 'application_event:201:deadline' };
  fixture.jobs = [represented, job('history', 'succeeded'), job('unknown', 'result_unknown'), job('draft', 'succeeded', 'interview_draft'), job('life', 'succeeded', 'companion_life')];
  await render([represented.candidate_id]);
  expect(host.querySelectorAll('[aria-label="主动任务列表"] article')).toHaveLength(2);
  expect(host.querySelectorAll('[aria-label="历史提醒列表"] article')).toHaveLength(1);
  expect(host.querySelector('details')?.open).toBe(false);
  expect(host.textContent).toContain('result-draft');
  expect(host.textContent).toContain('reason-unknown');
  expect(host.textContent).not.toContain('result-represented');
  expect(host.textContent).not.toContain('result-life');
  const history = host.querySelector('details')!;
  await act(async () => { history.open = true; history.open = false; });
  expect(cancelProactiveJob).not.toHaveBeenCalled();
});

it('submits a repeated cancellation once while pending and permits retry after a confirmed failure', async () => {
  fixture.jobs = [job('job-to-cancel', 'pending')];
  let reject!: (error: Error) => void;
  vi.mocked(cancelProactiveJob).mockReturnValueOnce(new Promise<void>((_resolve, fail) => { reject = fail; }));
  await render();
  const cancel = [...host.querySelectorAll('button')].find(button => button.textContent?.replace(/\s/g, '') === '取消任务')!;
  await act(async () => { cancel.click(); cancel.click(); });
  expect(cancelProactiveJob).toHaveBeenCalledTimes(1);
  await act(async () => reject(new Error('confirmed cancellation failure')));
  expect(host.textContent).toContain('任务取消失败');
  vi.mocked(cancelProactiveJob).mockResolvedValueOnce(undefined);
  await act(async () => cancel.click());
  expect(cancelProactiveJob).toHaveBeenCalledTimes(2);
  expect(cancelProactiveJob).toHaveBeenLastCalledWith('job-to-cancel');
  expect(fixture.invalidate).toHaveBeenCalledWith({ queryKey: ['proactive-jobs'] });
});
