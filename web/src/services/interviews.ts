import type { InterviewIndexResponse } from '@/types/interviewIndex';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api' });

export async function listInterviews(limit = 50, cursor = ''): Promise<InterviewIndexResponse> {
  const { data } = await http.get<InterviewIndexResponse>('/interviews', {
    params: { limit, ...(cursor ? { cursor } : {}) },
  });
  return data;
}
