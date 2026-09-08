export type ActionCommand = 'approve' | 'modify' | 'reject' | 'undo' | 'refresh';

/** Display facts only. Command credentials remain with the original owner. */
export interface ActionPresentationV1 {
  schema_version: number;
  source_kind: 'agent' | 'product_action';
  operation_id: string;
  source_revision: string;
  presentation_revision: string;
  title: string;
  target: string | null;
  summary: string;
  source_label: string;
  decision: 'undecided' | 'approved' | 'modified' | 'rejected' | 'cancelled' | 'expired' | 'not_applicable' | 'unknown';
  execution: 'not_started' | 'running' | 'committed' | 'failed' | 'unknown';
  evidence: 'verified' | 'incomplete' | 'unavailable';
  undo: 'unsupported' | 'available' | 'running' | 'undone' | 'conflict' | 'unknown';
  available_actions: ActionCommand[];
}

export interface PilotTurnItemV1 {
  schema_version: number;
  item_id: string;
  kind: 'user_message' | 'assistant_message' | 'action' | 'error_info' | 'run_boundary';
  message_id: number | null;
  operation_id: string | null;
  content: string;
  action: ActionPresentationV1 | null;
}

export interface PilotPresentationSnapshot {
  schema_version: number;
  conversation_id: number;
  items: PilotTurnItemV1[];
}
