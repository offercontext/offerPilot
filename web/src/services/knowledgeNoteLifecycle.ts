import { createApiClient } from './http';
import type { ConfirmedInterviewKnowledgeNote } from '@/types/knowledge';
const http = createApiClient({ baseURL: '/api' });
export type ManagedKnowledgeNote = ConfirmedInterviewKnowledgeNote & { archived: boolean };
export interface KnowledgeNoteMutation {
  mutation_id: string;
  expected_version_id: number;
  expected_archived: boolean;
  confirmed: true;
  action: 'revise' | 'archive' | 'unarchive' | 'delete';
  title?: string;
  blocks?: Array<{ block_id: string; text: string }>;
}
export async function listManagedKnowledgeNotes(): Promise<ManagedKnowledgeNote[]> {
  return (await http.get<{ items: ManagedKnowledgeNote[] }>('/knowledge/note-management')).data.items;
}
export async function mutateKnowledgeNote(id: number, command: KnowledgeNoteMutation): Promise<void> {
  await http.post(`/knowledge/notes/${id}/lifecycle`, command);
}
