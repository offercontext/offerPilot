import { createApiClient } from './http';
import type { CompanionBid, CompanionCommand, CompanionPresence, CompanionSignals, CompanionSnapshot } from '@/features/companionLife/contracts';
const http = createApiClient({ baseURL: '/api', timeout: 10000 });
export async function getCompanion(): Promise<CompanionSnapshot> {
  return (await http.get<CompanionSnapshot>('/companion')).data;
}
export async function sendCompanionCommand(command: CompanionCommand): Promise<CompanionSnapshot> {
  if (command.kind === 'settings') return (await http.put<CompanionSnapshot>('/companion/settings', command.body)).data;
  if (command.kind === 'reset') return (await http.delete<CompanionSnapshot>('/companion', { data: command.body })).data;
  return (await http.post<CompanionSnapshot>('/companion/events', command.body)).data;
}
export async function reportCompanionPresence(identity: string, client_id: string, sequence: number, signals: CompanionSignals): Promise<CompanionPresence> {
  return (await http.post<CompanionPresence>('/companion/presence', { identity, client_id, sequence, ...signals })).data;
}
export async function claimCompanionBid(identity: string, expected_revision: number, client_id: string): Promise<CompanionBid | null> {
  return (await http.post<{ item: CompanionBid | null }>('/companion/bid', { identity, expected_revision, client_id })).data.item;
}
