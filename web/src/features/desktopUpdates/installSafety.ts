import type { PilotConversationController } from '@/features/assistantSurface/usePilotConversationController';
import { createApiClient } from '@/services/http';
import type { Conversation, PilotExecution } from '@/types/chat';
import type { UpdateSafetySnapshot } from './types';
import { readKnownBackgroundWork } from './backgroundSafety';

const http = createApiClient({ baseURL: '/api', timeout: 4000 });

export interface DesktopUpdateSafetyInput {
  pilot: Pick<PilotConversationController,
    | 'composerDraft' | 'attachments' | 'lastFailedText' | 'loading'
    | 'activeRequestRef' | 'streamingAssistantActiveRef' | 'executionControl'
    | 'backgroundExecution' | 'pending' | 'activePendingRef' | 'confirmPhase'
    | 'conversationId' | 'isActionOwnerReady'
  >;
  taskGuard: { pending: boolean; unsaved: boolean };
  hasAttachmentDrafts: boolean;
  hasRetainedDrafts: boolean;
  retainedActiveRun?: boolean;
  retainedPendingApproval?: boolean;
}

/** Only bounded flags cross IPC; ready describes known-owner readiness, not global coverage. */
export function desktopUpdateSafetySnapshot(input: DesktopUpdateSafetyInput): UpdateSafetySnapshot {
  const { pilot } = input;
  const hasDraft = Boolean(
    pilot.composerDraft || pilot.lastFailedText || pilot.attachments.length
    || input.hasAttachmentDrafts || input.hasRetainedDrafts || input.taskGuard.unsaved,
  );
  const executions = [pilot.executionControl.execution, pilot.backgroundExecution];
  const activeRun = Boolean(
    pilot.loading || pilot.activeRequestRef.current || pilot.streamingAssistantActiveRef.current
    || pilot.confirmPhase === 'saving' || pilot.executionControl.stopping || pilot.executionControl.retryingStop
    || executions.some((execution) => execution?.state === 'running' || execution?.state === 'result_unknown')
    || input.taskGuard.pending || input.retainedActiveRun,
  );
  const pendingApproval = Boolean(input.retainedPendingApproval || pilot.pending || pilot.activePendingRef.current
    || executions.some((execution) => execution?.state === 'waiting_confirmation'));
  return { ready: pilot.isActionOwnerReady(), hasDraft, activeRun, pendingApproval };
}

function knownConversationIds({ pilot }: DesktopUpdateSafetyInput): number[] {
  const values = [pilot.conversationId, pilot.activeRequestRef.current?.conversationId,
    pilot.activeRequestRef.current?.execution?.conversation_id,
    pilot.executionControl.execution?.conversation_id, pilot.backgroundExecution?.conversation_id];
  if (values.some((id) => id !== undefined && (!Number.isSafeInteger(id) || id <= 0))) {
    throw new Error('invalid_update_safety_scope');
  }
  return [...new Set(values.filter((id): id is number => id !== undefined))].sort((a, b) => a - b);
}

const executionStates = new Set(['running', 'waiting_confirmation', 'completed', 'failed', 'interrupted', 'stopped', 'result_unknown']);
const unavailable = (): UpdateSafetySnapshot => ({
  ready: false, hasDraft: true, activeRun: true, pendingApproval: true, reason: '无法确认当前任务状态，请稍后重试。',
});

/**
 * Refresh the known executions and persisted approvals on every nonce. Other
 * editors do not have a global dirty registry: main must disclose that gap and
 * require the user's explicit save-and-exit confirmation, then request again.
 */
export async function readDesktopUpdateSafety(
  readInput: () => DesktopUpdateSafetyInput,
  readers: {
    executions: (id: number) => Promise<PilotExecution | null>;
    conversations: () => Promise<Conversation[]>;
    background: () => Promise<boolean>;
  } = {
    executions: async (id) => (await http.get<{ execution: PilotExecution | null }>(`/chat/conversations/${id}/execution`)).data.execution,
    // Preserve malformed/null responses for fail-closed validation instead of
    // the ordinary display services' empty-list fallback.
    conversations: async () => (await http.get<Conversation[]>('/chat/conversations', { params: { include_archived: true } })).data,
    background: readKnownBackgroundWork,
  },
): Promise<UpdateSafetySnapshot> {
  try {
    const before = readInput();
    const initial = desktopUpdateSafetySnapshot(before);
    if (!initial.ready || initial.hasDraft || initial.activeRun || initial.pendingApproval) return initial;
    const ids = knownConversationIds(before);
    const [conversations, executions, backgroundActive] = await Promise.all([
      readers.conversations(), Promise.all(ids.map((id) => readers.executions(id))), readers.background(),
    ]);
    if (!Array.isArray(conversations) || conversations.some((item) => !item || !Number.isSafeInteger(item.id) || item.id <= 0)) return unavailable();
    if (ids.some((id) => !conversations.some((item) => item.id === id))) return unavailable();
    if (executions.some((execution, index) => execution !== null && (
      !execution || execution.conversation_id !== ids[index] || !executionStates.has(execution.state)
      || typeof execution.turn_id !== 'string' || !execution.turn_id
      || !Number.isSafeInteger(execution.execution_generation) || execution.execution_generation <= 0
    ))) return unavailable();
    const latest = readInput();
    if (JSON.stringify(knownConversationIds(latest)) !== JSON.stringify(ids)) return unavailable();
    const snapshot = desktopUpdateSafetySnapshot(latest);
    return {
      ...snapshot,
      activeRun: snapshot.activeRun || backgroundActive || executions.some((item) => item?.state === 'running' || item?.state === 'result_unknown'),
      pendingApproval: snapshot.pendingApproval
        || conversations.some((item) => Boolean(item.pending_action || item.pending_clarification))
        || executions.some((item) => item?.state === 'waiting_confirmation'),
    };
  } catch {
    return unavailable();
  }
}
