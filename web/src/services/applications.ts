import type { Application, ApplicationInput, DashboardSummary } from '@/types/application';
import { createApiClient } from './http';

const http = createApiClient({
  baseURL: '/api',
  timeout: 10000,
});

export async function listApplications(status?: string): Promise<Application[]> {
  const { data } = await http.get<Application[]>('/applications', {
    params: status ? { status } : undefined,
  });
  return data;
}

export async function createApplication(input: ApplicationInput): Promise<Application> {
  const { data } = await http.post<Application>('/applications', input);
  return data;
}

export async function updateApplication(id: number, input: Partial<ApplicationInput>): Promise<Application> {
  // The Go handler expects a full object; merge isn't supported server-side,
  // so callers pass the complete desired state.
  const { data } = await http.put<Application>(`/applications/${id}`, input);
  return data;
}

export async function deleteApplication(id: number): Promise<void> {
  await http.delete(`/applications/${id}`);
}

export async function getDashboard(): Promise<DashboardSummary> {
  const { data } = await http.get<DashboardSummary>('/dashboard');
  return data;
}
