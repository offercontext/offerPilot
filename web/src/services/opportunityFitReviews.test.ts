import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  findOpportunityFitV2SourceConflictStage,
  listOpportunityFitReviews,
  listOpportunityFitV2Reviews,
} from './opportunityFitReviews';

const { createClientMock, getMock } = vi.hoisted(() => ({
  createClientMock: vi.fn(),
  getMock: vi.fn(),
}));

vi.mock('./http', () => ({
  createApiClient: (options: unknown) => {
    createClientMock(options);
    return { get: getMock, post: vi.fn() };
  },
}));

afterEach(() => getMock.mockReset());

describe('opportunity fit history schema routing', () => {
  it('allows real Provider requests to outlive the backend lease boundary', () => {
    expect(createClientMock).toHaveBeenCalledWith({ baseURL: '/api', timeout: 180000 });
  });

  it('keeps v1 history out of the v2 list and v2 history out of the v1 list', async () => {
    getMock.mockResolvedValue({
      data: [
        { id: 1, application_id: 9, schema_version: 1, status: 'triage_complete' },
        { id: 2, review_id: 2, application_id: 9, schema_version: 2, stage_count: 1 },
      ],
    });

    await expect(listOpportunityFitReviews(9)).resolves.toEqual([
      { id: 1, application_id: 9, schema_version: 1, status: 'triage_complete' },
    ]);
    await expect(listOpportunityFitV2Reviews(9)).resolves.toEqual([
      { id: 2, review_id: 2, application_id: 9, schema_version: 2, stage_count: 1 },
    ]);
  });
});

describe('opportunity fit source-conflict recovery', () => {
  it('distinguishes a temporary read failure from no matching conflict stage', async () => {
    getMock.mockRejectedValueOnce(new Error('temporary read failure'));

    await expect(
      findOpportunityFitV2SourceConflictStage(9, 'triage', 'triage-key-00000001'),
    ).resolves.toEqual({ status: 'unknown' });
  });

  it('returns not_found when the matching review has no conflict stage', async () => {
    getMock
      .mockResolvedValueOnce({
        data: [{
          review_id: 2,
          schema_version: 2,
          stage_count: 1,
          triage_idempotency_key: 'triage-key-00000001',
        }],
      })
      .mockResolvedValueOnce({ data: { stages: [] } });

    await expect(
      findOpportunityFitV2SourceConflictStage(9, 'triage', 'triage-key-00000001'),
    ).resolves.toEqual({ status: 'not_found' });
  });

  it('classifies a deleted application list as missing', async () => {
    getMock.mockRejectedValueOnce({ response: { status: 404 } });

    await expect(
      findOpportunityFitV2SourceConflictStage(9, 'triage', 'triage-key-00000001'),
    ).resolves.toEqual({ status: 'application_missing' });
  });

  it('classifies a deleted review detail as missing', async () => {
    getMock
      .mockResolvedValueOnce({
        data: [{
          review_id: 2,
          schema_version: 2,
          stage_count: 1,
          triage_idempotency_key: 'triage-key-00000001',
        }],
      })
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockResolvedValueOnce({ data: [] });

    await expect(
      findOpportunityFitV2SourceConflictStage(9, 'triage', 'triage-key-00000001'),
    ).resolves.toEqual({ status: 'review_missing' });
  });

  it('checks application visibility before recovering a review by id', async () => {
    getMock.mockRejectedValueOnce({ response: { status: 404 } });

    await expect(
      findOpportunityFitV2SourceConflictStage(9, 'deep_review', 'deep-key-00000001', 42),
    ).resolves.toEqual({ status: 'application_missing' });
    expect(getMock).toHaveBeenCalledTimes(1);
    expect(getMock).not.toHaveBeenCalledWith('/applications/9/opportunity-fit-reviews/42');
  });

  it('classifies an explicit review detail 404 as review missing after application visibility succeeds', async () => {
    getMock
      .mockResolvedValueOnce({ data: [] })
      .mockRejectedValueOnce({ response: { status: 404 } });
    getMock.mockResolvedValueOnce({ data: [] });

    await expect(
      findOpportunityFitV2SourceConflictStage(9, 'deep_review', 'deep-key-00000001', 42),
    ).resolves.toEqual({ status: 'review_missing' });
    expect(getMock).toHaveBeenCalledTimes(3);
  });

  it('classifies a review detail 404 as application missing when the recheck also returns 404', async () => {
    getMock
      .mockResolvedValueOnce({ data: [] })
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockRejectedValueOnce({ response: { status: 404 } });

    await expect(
      findOpportunityFitV2SourceConflictStage(9, 'deep_review', 'deep-key-00000001', 42),
    ).resolves.toEqual({ status: 'application_missing' });
  });

  it('keeps the recovery unknown when the application recheck is unavailable', async () => {
    getMock
      .mockResolvedValueOnce({ data: [] })
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockRejectedValueOnce(new Error('temporary visibility failure'));

    await expect(
      findOpportunityFitV2SourceConflictStage(9, 'deep_review', 'deep-key-00000001', 42),
    ).resolves.toEqual({ status: 'unknown' });
  });
});
