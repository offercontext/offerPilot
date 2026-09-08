import { describe, expect, it, vi } from 'vitest';

const { apiGet, createApiClient } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  createApiClient: vi.fn(),
}));

vi.mock('./http', () => ({ createApiClient }));

createApiClient.mockReturnValue({ get: apiGet });

const { getLogs } = await import('./chat');

describe('getLogs', () => {
  it('returns the requested runtime log page', async () => {
    const page = {
      entries: [{ level: 'WARNING', message: 'retry' }],
      total: 41,
      limit: 20,
      offset: 20,
      has_more: true,
    };
    apiGet.mockResolvedValue({ data: page });

    await expect(getLogs(20, 20)).resolves.toEqual(page);

    expect(apiGet).toHaveBeenCalledWith('/logs', { params: { limit: 20, offset: 20 } });
  });
});
