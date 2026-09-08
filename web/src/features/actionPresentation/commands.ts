import type { ActionCommand, ActionPresentationV1, PilotPresentationSnapshot } from './contracts';
import { supportedAction } from './ActionCard';

export function pendingPresentationActions(snapshot: PilotPresentationSnapshot | null | undefined, operationId: string | undefined): readonly ActionCommand[] | undefined {
  if (!snapshot) return undefined;
  if (snapshot.schema_version !== 1) return [];
  // Legacy clarification-only Pending has no Ledger operation to project.
  if (!operationId) return undefined;
  const action = snapshot.items.find((item) => item.kind === 'action' && item.operation_id === operationId)?.action;
  return action && action.source_kind === 'agent' && action.operation_id === operationId
    && supportedAction(action) ? action.available_actions : [];
}

/** Bind display intents to the existing exact-operation controller. */
export function agentActionCommands(action: ActionPresentationV1, owner: {
  lastUndo: { parent_operation_id?: string } | null;
  undoOperation: (operationId: string) => Promise<void>;
  refreshPresentation?: () => void;
}): Partial<Record<ActionCommand, () => void>> {
  if (action.source_kind !== 'agent' || !action.operation_id) return {};
  return {
    ...(owner.refreshPresentation ? { refresh: owner.refreshPresentation } : {}),
    ...(owner.lastUndo?.parent_operation_id === action.operation_id
      ? { undo: () => { void owner.undoOperation(action.operation_id); } } : {}),
  };
}
