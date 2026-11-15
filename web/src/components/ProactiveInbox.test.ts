import { describe, expect, it } from 'vitest';
import source from './ProactiveInbox.tsx?raw';
import type { ProactiveJob } from '@/types/proactive';
import { formatProactiveTimestamp, isRepresentedReminder, partitionProactiveJobs } from './ProactiveInbox';

describe('ProactiveInbox', () => {
  it('interprets backend timestamps as UTC Unix seconds', () => {
    expect(formatProactiveTimestamp(0)).toContain('1970');
    expect(formatProactiveTimestamp(0, 'Asia/Shanghai')).not.toBe(formatProactiveTimestamp(0, 'UTC'));
    expect(formatProactiveTimestamp(Number.NaN)).toBe('时间未知');
  });

  it('makes generated draft boundaries and cancellation visible', () => {
    expect(source).toContain('自动准备草稿 · 未发送');
    expect(source).toContain('不会自动执行业务操作');
    expect(source).toContain('取消任务');
    expect(source).toContain('cancelProactiveJob');
    expect(source).toContain('useId');
    expect(source).toContain('getProactiveSettings');
    expect(source).toContain('timeZone');
  });
});


describe('today reminder deduplication', () => {
  const job = { candidate_id: 'application_event:1:deadline', kind: 'deadline', state: 'succeeded' } as ProactiveJob;
  it('hides only a succeeded ordinary reminder represented by the exact subject', () => {
    expect(isRepresentedReminder(job, ['application_event:1:deadline'])).toBe(true);
    expect(isRepresentedReminder(job, ['offer:1:offer_response_deadline'])).toBe(false);
    expect(isRepresentedReminder({ ...job, candidate_id: undefined }, ['application_event:1:deadline'])).toBe(false);
  });
  it('keeps drafts, pending, failed and unknown outcomes visible', () => {
    expect(isRepresentedReminder({ ...job, kind: 'interview_draft' }, [job.candidate_id!])).toBe(false);
    for (const state of ['pending', 'leased', 'running', 'failed', 'result_unknown', 'cancelled'] as const) {
      expect(isRepresentedReminder({ ...job, state }, [job.candidate_id!])).toBe(false);
    }
  });
});


it('moves unrepresented published reminders to explicit history instead of resurrecting a current task', () => {
  const published = { id: 'past', candidate_id: 'application_event:1:deadline', kind: 'deadline', state: 'succeeded' } as ProactiveJob;
  const unknown = { ...published, id: 'unknown', state: 'result_unknown' } as ProactiveJob;
  const draft = { ...published, id: 'draft', kind: 'interview_draft' } as ProactiveJob;
  expect(partitionProactiveJobs([published, unknown, draft], [published.candidate_id!])).toEqual({ current: [unknown, draft], history: [] });
  expect(partitionProactiveJobs([published, unknown, draft], [])).toEqual({ current: [unknown, draft], history: [published] });
  expect(source).toContain('<details>');
  expect(source).toContain('不代表当前仍需处理');
});

it('never partitions companion life into job tasks or published reminder history', () => {
  const companion = { kind: 'companion_life', application_id: 0, candidate_id: 'companion:one:4' } as ProactiveJob;
  const lifeJobs = (['pending', 'leased', 'running', 'succeeded', 'failed', 'result_unknown', 'cancelled'] as const)
    .map((state) => ({ ...companion, id: `life-${state}`, state }));
  expect(partitionProactiveJobs(lifeJobs, [])).toEqual({ current: [], history: [] });
  expect(isRepresentedReminder({ ...companion, state: 'succeeded' }, [companion.candidate_id!])).toBe(false);
});
