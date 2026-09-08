import type { Application, ApplicationInput, ApplicationCreationInput, ApplicationCreationResult, ApplicationDuplicates, DashboardSummary } from '@/types/application';
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

export async function createApplicationWithJd(input: ApplicationCreationInput): Promise<ApplicationCreationResult> {
  const { data, status } = await http.post<ApplicationCreationResult>('/applications', input);
  if (![200, 201].includes(status) || !data || !Number.isSafeInteger(data.id) || data.id <= 0
      || typeof data.company_name !== 'string' || typeof data.position_name !== 'string'
      || (data.jd_version_id !== null && (!Number.isSafeInteger(data.jd_version_id) || data.jd_version_id <= 0))) {
    throw new Error('创建回执不完整，请使用原请求恢复结果');
  }
  return data;
}
export async function getApplicationCreationScope(): Promise<string> {
  return (await http.get<{ scope_id: string }>('/applications/creation-context')).data.scope_id;
}
export async function checkApplicationDuplicates(input: ApplicationInput): Promise<ApplicationDuplicates> {
  return (await http.get<ApplicationDuplicates>('/applications/duplicates', {
    params: { company_name: input.company_name, position_name: input.position_name, job_url: input.job_url },
  })).data;
}
