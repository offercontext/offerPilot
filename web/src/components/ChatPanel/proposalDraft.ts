import dayjs from 'dayjs';
import type { PendingAction, PendingActionEditableField } from '@/types/chat';

export type ProposalDraft = Record<string, unknown>;

export interface ProposalReviewState {
  identity: string;
  draft: ProposalDraft;
  editorOpen: boolean;
  rejectOpen: boolean;
  feedback: string;
}

const PROTECTED_FIELDS = new Set(['id', 'application_id', 'index']);
const EDITABLE_TYPES = new Set(['string', 'long_text', 'number', 'boolean', 'enum', 'datetime']);

export function editableFieldsForAction(action: PendingAction): PendingActionEditableField[] {
  if (action.tool_name.includes('delete')) return [];
  const seen = new Set<string>();
  return (action.editable_fields ?? []).filter((descriptor) => {
    if (
      !descriptor.field ||
      PROTECTED_FIELDS.has(descriptor.field) ||
      !EDITABLE_TYPES.has(descriptor.type) ||
      seen.has(descriptor.field)
    ) {
      return false;
    }
    seen.add(descriptor.field);
    return true;
  });
}

export function buildEditableDraft(action: PendingAction): ProposalDraft {
  const draft: ProposalDraft = {};
  for (const descriptor of editableFieldsForAction(action)) {
    draft[descriptor.field] = action.args?.[descriptor.field];
  }
  return draft;
}

function isDeclaredClearValue(
  descriptor: PendingActionEditableField,
  value: unknown,
): boolean {
  const clearValue = descriptor.clear_value;
  const clearValueType = typeof clearValue;
  const scalarClearValue =
    clearValue === null ||
    clearValueType === 'string' ||
    clearValueType === 'boolean' ||
    (clearValueType === 'number' && Number.isFinite(clearValue));
  return (
    scalarClearValue &&
    descriptor.clearable === true &&
    Object.prototype.hasOwnProperty.call(descriptor, 'clear_value') &&
    Object.is(value, clearValue)
  );
}

function validDraftValue(descriptor: PendingActionEditableField, value: unknown): boolean {
  if (isDeclaredClearValue(descriptor, value)) return true;
  if (value === undefined || value === null) return false;
  switch (descriptor.type) {
    case 'string':
    case 'long_text':
      return typeof value === 'string';
    case 'number':
      return typeof value === 'number' && Number.isFinite(value) && Number.isInteger(value);
    case 'boolean':
      return typeof value === 'boolean';
    case 'enum':
      return (
        typeof value === 'string' &&
        Array.isArray(descriptor.options) &&
        descriptor.options.includes(value)
      );
    case 'datetime':
      return typeof value === 'string' && value.trim().length > 0 && dayjs(value).isValid();
  }
}

export function changedEditableArgs(
  action: PendingAction,
  draft: ProposalDraft,
): Record<string, unknown> | undefined {
  const changed: Record<string, unknown> = {};
  for (const descriptor of editableFieldsForAction(action)) {
    const value = draft[descriptor.field];
    if (!validDraftValue(descriptor, value)) continue;
    if (Object.is(value, action.args?.[descriptor.field])) continue;
    const declaredClear = isDeclaredClearValue(descriptor, value);
    changed[descriptor.field] =
      descriptor.type === 'datetime' && !declaredClear
        ? dayjs(value as string).toISOString()
        : value;
  }
  return Object.keys(changed).length ? changed : undefined;
}

function stableSerialize(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableSerialize).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableSerialize(item)}`)
      .join(',')}}`;
  }
  return JSON.stringify(value) ?? String(value);
}

export function actionIdentity(action: PendingAction): string {
  const token = action.confirmation_token?.trim();
  return token ? `token:${token}` : `fallback:${action.tool_name}:${stableSerialize(action.args ?? {})}`;
}

export function createProposalReviewState(action: PendingAction): ProposalReviewState {
  return {
    identity: actionIdentity(action),
    draft: buildEditableDraft(action),
    editorOpen: false,
    rejectOpen: false,
    feedback: '',
  };
}

export function syncProposalReviewState(
  current: ProposalReviewState,
  action: PendingAction,
): ProposalReviewState {
  return current.identity === actionIdentity(action) ? current : createProposalReviewState(action);
}
