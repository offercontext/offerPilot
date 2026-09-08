import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('./http', () => ({ createApiClient: () => http }));

const {
  createApplicationOutcome,
  createSubmissionSnapshot,
  getApplicationOutcomeSummary,
  listApplicationOutcomes,
  listSubmissionSnapshots,
} = await import('./applicationOutcomes');

beforeEach(() => {
  http.get.mockReset();
  http.post.mockReset();
});

describe('application outcome service', () => {
  it('uses application-scoped read routes', async () => {
    http.get.mockResolvedValue({ data: [] });

    await listSubmissionSnapshots(7);
    await listApplicationOutcomes(7);
    await getApplicationOutcomeSummary(7);

    expect(http.get.mock.calls.map(([path]) => path)).toEqual([
      '/applications/7/submission-snapshots',
      '/applications/7/outcomes',
      '/applications/7/outcome-summary',
    ]);
  });

  it('sends only the explicit immutable snapshot and append-only outcome payloads', async () => {
    http.post.mockResolvedValue({ data: { id: 1 } });
    const snapshot = {
      resume_id: 2,
      jd_version_id: 3,
      material_kit_id: null,
      submitted_at: '2026-08-12T09:00:00Z',
      note: '官网投递',
      idempotency_key: 'snapshot-key-0001',
    };
    const outcome = {
      submission_snapshot_id: 1,
      application_event_id: null,
      stage: 'interview' as const,
      result: 'advanced' as const,
      feedback_text: '表达清晰，系统设计需要更深入。',
      reflection_text: '容量估算不够完整。',
      next_action_text: '补一轮容量估算练习。',
      feedback_tags: ['system_design' as const],
      occurred_at: '2026-08-12T11:00:00Z',
      idempotency_key: 'outcome-key-00001',
    };

    await createSubmissionSnapshot(7, snapshot);
    await createApplicationOutcome(7, outcome);

    expect(http.post).toHaveBeenNthCalledWith(1, '/applications/7/submission-snapshots', snapshot);
    expect(http.post).toHaveBeenNthCalledWith(2, '/applications/7/outcomes', outcome);
    expect(JSON.stringify(http.post.mock.calls)).not.toContain('source_kind');
  });
});
