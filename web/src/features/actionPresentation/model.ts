import type { UITurn } from '@/components/ChatPanel/model';
import { parseTurnPresentation } from '@/lib/pilotReplyPresentation';
import type { ActionPresentationV1, PilotPresentationSnapshot } from './contracts';

/** Unacknowledged transport can obscure progress but cannot erase a verified commit. */
export function withTransportUncertainty(action: ActionPresentationV1, executionUnknown: boolean, undoUnknown = false): ActionPresentationV1 {
  return {
    ...action,
    ...(executionUnknown && action.execution !== 'committed'
      ? { execution: 'unknown', evidence: 'incomplete' } as const : {}),
    ...(undoUnknown && action.undo !== 'undone' ? { undo: 'unknown' } as const : {}),
  };
}

/** Merge by persisted identities, never by text, tool name, or hash ordering. */
export function mergePresentationTurns(turns: UITurn[], snapshot: PilotPresentationSnapshot | null): UITurn[] {
  if (!snapshot || snapshot.schema_version !== 1) return turns;
  // Rebuild from the authorized message projection. Old in-memory tool results
  // and evidence are not authority to reveal a source the server has hidden.
  const seen = new Set<string>();
  const seenOperations = new Set<string>();
  const result: UITurn[] = [];
  for (const item of snapshot.items) {
    if (item.schema_version !== 1 || seen.has(item.item_id)) continue;
    seen.add(item.item_id);
    if (item.kind === 'action' && item.action) {
      if (item.action.source_kind !== 'agent' || item.operation_id !== item.action.operation_id
        || item.item_id !== `agent_operation:${item.operation_id}` || seenOperations.has(item.operation_id)) continue;
      seenOperations.add(item.operation_id);
      result.push({ id: item.item_id, role: 'assistant', content: '', action: item.action });
    } else if (item.kind === 'user_message' || item.kind === 'assistant_message') {
      if (!Number.isSafeInteger(item.message_id) || item.message_id === null || item.message_id <= 0
        || item.item_id !== `message:${item.message_id}`) continue;
      const presentation = item.kind === 'assistant_message' ? parseTurnPresentation(item.content) : undefined;
      result.push({ id: item.item_id, role: item.kind === 'user_message' ? 'user' : 'assistant',
        content: presentation?.detailMarkdown ?? item.content, presentation });
    } else if (item.kind === 'error_info' || item.kind === 'run_boundary') {
      result.push({ id: item.item_id, role: 'assistant', content: item.content });
    }
  }
  return result;
}
