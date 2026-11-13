import { createApiClient } from './http';

export interface RestoreReceipt {
  state: 'none' | 'validated' | 'queued' | 'switching' | 'installed' | 'completed' | 'cancelled' | 'failed' | 'rolled_back';
  id?: string;
  sha256?: string;
  legacy?: boolean;
  counts?: Record<string, number>;
}
const api = createApiClient({ baseURL: '/api' });
export const getRestoreStatus = async () => (await api.get<RestoreReceipt>('/backups/restore')).data;
export const previewRestore = async (file: File) => (await api.post<RestoreReceipt>('/backups/restore/preview', file, { headers: { 'Content-Type': 'application/zip' } })).data;
export const confirmRestore = async (id: string, sha256: string, confirmation: string) => (await api.post<RestoreReceipt>('/backups/restore/confirm', { id, sha256, confirmation })).data;
export const cancelRestore = async (id: string) => (await api.post<RestoreReceipt>('/backups/restore/cancel', { id })).data;
