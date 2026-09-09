import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api' });
export type ContextSourceName = 'confirmed_readiness' | 'confirmed_memory' | 'knowledge_context' | 'older_conversation_summary';
export interface ContextPolicy { enabled: boolean; max_units: number; version: 'v1' }
export interface ContextPolicies { revision: number; policies: Record<ContextSourceName, ContextPolicy> }
export async function getContextPolicies(): Promise<ContextPolicies> {
  return (await http.get<ContextPolicies>('/context-policies')).data;
}
export async function updateContextPolicies(current: ContextPolicies, name: ContextSourceName, enabled: boolean): Promise<ContextPolicies> {
  return (await http.put<ContextPolicies>('/context-policies', { expected_revision: current.revision,
    policies: { ...current.policies, [name]: { ...current.policies[name], enabled } } })).data;
}
