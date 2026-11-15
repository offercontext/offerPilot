import { createApiClient } from './http';
export const ACTIVITY_TEMPLATES = ['paper_boat', 'cloud_catalog', 'paper_stars'] as const;
export type ActivityTemplate = typeof ACTIVITY_TEMPLATES[number];
export interface PersonaSpec {
  display_name: string;
  traits: string[];
  speaking_style: string;
  verbosity: 'brief' | 'balanced' | 'detailed';
  interests_text: string;
  fictional_background: string;
  additional_style_notes: string;
  enabled_activity_templates: ActivityTemplate[];
}
export interface PersonaRevision {
  profile_id: string; revision: number; content_hash: string; spec: PersonaSpec;
  created_at: number; operation: string; restored_from_revision: number | null;
}
export interface PersonaView extends Pick<PersonaRevision, 'profile_id' | 'revision' | 'content_hash' | 'spec'> {
  policy_version: string;
  presets: { id: 'official' | 'gentle' | 'concise'; spec: PersonaSpec }[];
}
interface MutationBase { profile_id: string; expected_revision: number; command_id: string; confirmed: true }
export type PersonaMutation = MutationBase & ({ operation: 'update'; spec: PersonaSpec } | { operation: 'restore'; target_revision: number });
const http = createApiClient({ baseURL: '/api', timeout: 10000 });
export async function getPersona(): Promise<PersonaView> { return (await http.get<PersonaView>('/companion/persona')).data; }
export async function getPersonaRevisions(): Promise<{ revisions: PersonaRevision[] }> { return (await http.get<{ revisions: PersonaRevision[] }>('/companion/persona/revisions')).data; }
export async function mutatePersona(command: PersonaMutation): Promise<PersonaView> { return (await http.post<PersonaView>('/companion/persona/mutations', command)).data; }
