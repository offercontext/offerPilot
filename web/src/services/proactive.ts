import { createApiClient } from './http';
import type {
  ProactiveJob,
  ProactiveJobsResponse,
  ProactiveSettings,
  ProactiveSettingsResponse,
} from '@/types/proactive';

const http = createApiClient({
  baseURL: '/api',
  timeout: 10000,
});

export async function getProactiveSettings(): Promise<ProactiveSettingsResponse> {
  return (await http.get<ProactiveSettingsResponse>('/proactive/settings')).data;
}

export async function updateProactiveSettings(
  expectedRevision: number,
  settings: ProactiveSettings,
): Promise<ProactiveSettingsResponse> {
  return (
    await http.put<ProactiveSettingsResponse>('/proactive/settings', {
      expected_revision: expectedRevision,
      confirmed: true,
      settings,
    })
  ).data;
}

export async function listProactiveJobs(): Promise<ProactiveJob[]> {
  const response = (await http.get<ProactiveJobsResponse>('/proactive/jobs')).data;
  return response.items ?? [];
}

export async function cancelProactiveJob(id: string): Promise<void> {
  await http.post(`/proactive/jobs/${encodeURIComponent(id)}/cancel`);
}
