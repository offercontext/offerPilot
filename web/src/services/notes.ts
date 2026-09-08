import type { CreateNoteInput, InterviewNote, UpdateNoteInput } from '@/types/note';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api', timeout: 10000 });

export async function listNotes(): Promise<InterviewNote[]> {
  const { data } = await http.get<InterviewNote[]>('/notes');
  return data;
}

export async function listNotesByApp(appID: number): Promise<InterviewNote[]> {
  const { data } = await http.get<InterviewNote[]>(`/applications/${appID}/notes`);
  return data;
}

export async function createNote(appID: number, input: CreateNoteInput): Promise<InterviewNote> {
  const { data } = await http.post<InterviewNote>(`/applications/${appID}/notes`, input);
  return data;
}

export async function createStandaloneNote(input: CreateNoteInput): Promise<InterviewNote> {
  const { data } = await http.post<InterviewNote>('/notes', input);
  return data;
}

export async function updateNote(id: number, input: UpdateNoteInput): Promise<InterviewNote> {
  const { data } = await http.put<InterviewNote>(`/notes/${id}`, input);
  return data;
}

export async function deleteNote(id: number): Promise<void> {
  await http.delete(`/notes/${id}`);
}
