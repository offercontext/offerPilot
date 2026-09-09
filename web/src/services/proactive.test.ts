import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ProactiveSettings } from '@/types/proactive';

const { apiGet, apiPut, apiPost, createApiClient } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(),
  apiPost: vi.fn(),
  createApiClient: vi.fn(),
}));

vi.mock('./http', () => ({ createApiClient }));
createApiClient.mockReturnValue({ get: apiGet, put: apiPut, post: apiPost });

const {
  cancelProactiveJob,
  getProactiveSettings,
  listProactiveJobs,
  updateProactiveSettings,
} = await import('./proactive');

const settings: ProactiveSettings = {
  enabled: true,
  reminders_enabled: true,
  drafts_enabled: false,
  application_ids: [7, 8],
  timezone: 'Asia/Shanghai',
  quiet_start_hour: 22,
  quiet_end_hour: 8,
  max_reminders_per_day: 4,
  max_drafts_per_day: 1,
};

beforeEach(() => {
  apiGet.mockReset();
  apiPut.mockReset();
  apiPost.mockReset();
});
describe('proactive service', () => {
  it('reads settings and unwraps the jobs envelope', async () => {
    const settingsResponse = { revision: 3, settings };
    const jobsResponse = { items: [{ id: 'job-1', kind: 'deadline' }] };
    apiGet.mockResolvedValueOnce({ data: settingsResponse }).mockResolvedValueOnce({ data: jobsResponse });

    await expect(getProactiveSettings()).resolves.toEqual(settingsResponse);
    await expect(listProactiveJobs()).resolves.toEqual(jobsResponse.items);
    expect(apiGet).toHaveBeenNthCalledWith(1, '/proactive/settings');
    expect(apiGet).toHaveBeenNthCalledWith(2, '/proactive/jobs');
  });

  it('sends explicit confirmation and the frozen settings revision', async () => {
    apiPut.mockResolvedValue({ data: { revision: 4, settings } });

    await updateProactiveSettings(3, settings);

    expect(apiPut).toHaveBeenCalledWith('/proactive/settings', {
      expected_revision: 3,
      confirmed: true,
      settings,
    });
  });

  it('encodes a job id before cancelling it', async () => {
    apiPost.mockResolvedValue({ data: { state: 'cancelled' } });

    await cancelProactiveJob('job/with space');

    expect(apiPost).toHaveBeenCalledWith('/proactive/jobs/job%2Fwith%20space/cancel');
  });
});
