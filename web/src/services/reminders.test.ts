import { beforeEach, describe, expect, it, vi } from 'vitest';
const { get, createApiClient } = vi.hoisted(() => ({ get: vi.fn(), createApiClient: vi.fn() }));
vi.mock('./http', () => ({ createApiClient }));
createApiClient.mockReturnValue({ get });
const { listReminderCandidates } = await import('./reminders');
beforeEach(() => { get.mockReset(); });

describe('reminder candidate service', () => {
  it('passes the display timezone and unwraps facts', async () => {
    get.mockResolvedValue({ data: { items: [] } });
    await expect(listReminderCandidates('America/New_York')).resolves.toEqual([]);
    expect(get).toHaveBeenCalledWith('/reminders/candidates', { params: { timezone: 'America/New_York' } });
  });
  it('does not convert a missing response into an empty day', async () => {
    get.mockResolvedValue({ data: {} });
    await expect(listReminderCandidates('UTC')).rejects.toThrow('reminder_candidates_unavailable');
    get.mockRejectedValue({ code: 'ERR_NETWORK' });
    await expect(listReminderCandidates('UTC')).rejects.toEqual({ code: 'ERR_NETWORK' });
  });
});
