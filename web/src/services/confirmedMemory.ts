import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api' });
export interface ConfirmedMemory {
  id: string;
  current_version: number;
  state: 'active' | 'withdrawn' | 'deleted';
  content: string;
  confirmed_at: string | null;
  versions?: Array<{ version: number; content: string; confirmed_at: string }>;
}
export interface MemoryMutation {
  mutation_id: string;
  action: 'confirm' | 'withdraw' | 'delete';
  expected_version: number;
  confirmed: true;
  content: string;
}
export async function listConfirmedMemory(): Promise<ConfirmedMemory[]> {
  return (await http.get<ConfirmedMemory[]>('/confirmed-memory')).data;
}
export async function getConfirmedMemory(id: string): Promise<ConfirmedMemory> {
  return (await http.get<ConfirmedMemory>(`/confirmed-memory/${encodeURIComponent(id)}`)).data;
}
export async function changeConfirmedMemory(id: string | undefined, command: MemoryMutation): Promise<ConfirmedMemory> {
  return (await http.post<ConfirmedMemory>(`/confirmed-memory${id ? `/${encodeURIComponent(id)}` : ''}`, command)).data;
}
