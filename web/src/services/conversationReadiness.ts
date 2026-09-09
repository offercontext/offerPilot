import { createApiClient } from './http';
import { listEvents } from './events';
import { listResumes } from './resumes';
import { getEventReadinessFeedback } from '@/features/reviewReadiness/service';
import type { ReadinessFeedbackItem } from '@/features/reviewReadiness/contracts';
import type { ScheduleEvent } from '@/types/event';
import type { Resume } from '@/types/resume';

const http = createApiClient({ baseURL: '/api' });

export type ConversationReadinessState = 'confirmed' | 'withdrawn' | 'not_applicable';

export interface ConversationReadinessContext {
  schema_version: 1;
  state: ConversationReadinessState;
  conversation_id: number;
  application_id: number;
  target_event_id: number | null;
  resume_id: number | null;
  ordered_version_ids: number[];
  selection_fingerprint: string;
  scope_revision: number;
  revision: number;
}

export interface ConversationReadinessMutation {
  mutation_id: string;
  expected_revision: number;
  confirmed: true;
}

export interface ConfirmConversationReadiness extends ConversationReadinessMutation {
  target_event_id: number;
  resume_id: number;
  ordered_version_ids: number[];
}

export interface ConversationReadinessOptions {
  events: ScheduleEvent[];
  resumes: Resume[];
  readiness: ReadinessFeedbackItem[];
}

/**
 * Load target and Resume choices.  Readiness feedback is fetched only after an
 * explicit target event is supplied, so opening the control never scans an
 * Application's Signals or guesses a target.
 */
export async function getConversationReadinessOptions(
  applicationId: number,
  targetEventId?: number,
): Promise<ConversationReadinessOptions> {
  const [events, resumes] = await Promise.all([
    listEvents({ application_id: applicationId, event_type: 'interview' }),
    listResumes(),
  ]);
  if (!targetEventId) return { events, resumes, readiness: [] };
  const readiness = await getEventReadinessFeedback(applicationId, targetEventId);
  return { events, resumes, readiness: readiness.items };
}

export async function getConversationReadinessContext(
  conversationId: number,
): Promise<ConversationReadinessContext> {
  const { data } = await http.get<ConversationReadinessContext>(
    `/chat/conversations/${conversationId}/readiness-context`,
  );
  return data;
}

export async function confirmConversationReadiness(
  conversationId: number,
  command: ConfirmConversationReadiness,
): Promise<ConversationReadinessContext> {
  const { data } = await http.put<ConversationReadinessContext>(
    `/chat/conversations/${conversationId}/readiness-context`,
    command,
  );
  return data;
}

export async function clearConversationReadiness(
  conversationId: number,
  command: ConversationReadinessMutation,
): Promise<ConversationReadinessContext> {
  const { data } = await http.post<ConversationReadinessContext>(
    `/chat/conversations/${conversationId}/readiness-context/clear`,
    command,
  );
  return data;
}

// Names kept as small aliases for consumers that phrase the action as bind or
// withdraw.  Both aliases use the same explicit command and CAS contract.
export const readConversationReadiness = getConversationReadinessContext;
export const bindConversationReadiness = confirmConversationReadiness;
export const withdrawConversationReadiness = clearConversationReadiness;
