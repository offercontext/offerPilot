import { createApiClient } from './http';
import type { AuthStatus } from '@/components/AuthGate/model';

const http = createApiClient({ baseURL: '/api', timeout: 10000 });

export async function getAuthStatus(): Promise<AuthStatus> {
  const { data } = await http.get<AuthStatus>('/auth/status');
  return data;
}
