import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiGet, apiPost, createApiClient } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  createApiClient: vi.fn(),
}));

vi.mock('./http', () => ({ createApiClient }));
createApiClient.mockReturnValue({ get: apiGet, post: apiPost });

const {
  acceptMaterialRevisionProposal,
  createMaterialRevisionProposal,
  getMaterialRevisionProposal,
  listMaterialRevisionProposals,
  rejectMaterialRevisionProposal,
} = await import('./materialRevisionProposals');

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiGet.mockResolvedValue({ data: {} });
  apiPost.mockResolvedValue({ data: {} });
});

describe('material revision proposal service', () => {
  it('uses the nested proposal routes and preserves the review payload', async () => {
    const input = { instructions: 'Highlight APIs', user_assertions: ['I led migration'] };
    await createMaterialRevisionProposal(7, input);
    await listMaterialRevisionProposals(7);
    await getMaterialRevisionProposal(7, 3);
    await rejectMaterialRevisionProposal(7, 3);

    expect(apiPost).toHaveBeenNthCalledWith(1, '/applications/7/material-revision-proposals', input);
    expect(apiGet).toHaveBeenNthCalledWith(1, '/applications/7/material-revision-proposals');
    expect(apiGet).toHaveBeenNthCalledWith(2, '/applications/7/material-revision-proposals/3');
    expect(apiPost).toHaveBeenNthCalledWith(2, '/applications/7/material-revision-proposals/3/reject');
  });

  it('sends the proposal hash and selected changes on accept', async () => {
    await acceptMaterialRevisionProposal(7, 3, {
      expected_proposal_sha256: 'abc',
      selected_change_ids: ['change-fastapi'],
    });

    expect(apiPost).toHaveBeenCalledWith('/applications/7/material-revision-proposals/3/accept', {
      expected_proposal_sha256: 'abc',
      selected_change_ids: ['change-fastapi'],
    });
  });
});
