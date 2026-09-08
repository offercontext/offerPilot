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
  timeline_item_id?: string;
  turn_id?: string;
  revision?: number;
}

export interface PilotPresentationSnapshot {
  schema_version: number;
  conversation_id: number;
  items: PilotTurnItemV1[];
  timeline?: PilotTimelineCache;
}

export interface PilotTimelineItem {
  schema_version: number;
  item_id: string;
  turn_id: string;
  conversation_id: number;
  item_type: PilotTurnItemV1['kind'];
  source_refs: string[];
  source_revision: string;
  revision: number;
  display_revision: number;
  ordinal: number;
  change_seq: number;
  payload_digest: string;
  deleted: boolean;
  payload: PilotTurnItemV1 | null;
}

export interface PilotTimelinePage {
  schema_version: number;
  conversation_id: number;
  mode: 'snapshot' | 'changes';
  high_watermark: number;
  items: PilotTimelineItem[];
  next_cursor: string | null;
  cursor: string;
}

export interface PilotTimelineCache {
  conversation_id: number;
  high_watermark: number;
  cursor: string;
  items: Record<string, PilotTimelineItem>;
}
