import { describe, expect, it } from 'vitest';
import type { PendingAction } from '@/types/chat';
import {
  actionIdentity,
  buildEditableDraft,
  changedEditableArgs,
  createProposalReviewState,
  editableFieldsForAction,
  syncProposalReviewState,
} from './proposalDraft';

function action(overrides: Partial<PendingAction> = {}): PendingAction {
  return {
    tool_name: 'update_application',
    human: '更新投递',
    confirmation_token: 'token-1',
    args: {
      id: 42,
      application_id: 42,
      status: 'applied',
      priority: 2,
      remote: false,
      scheduled_at: '2026-07-10T01:30:00.000Z',
      notes: '原始备注',
      unknown: 'private',
    },
    editable_fields: [
      { field: 'id', type: 'number' },
      { field: 'application_id', type: 'number' },
      { field: 'index', type: 'number' },
      { field: 'status', type: 'enum', options: ['applied', 'interview'] },
      { field: 'priority', type: 'number' },
      { field: 'remote', type: 'boolean' },
      { field: 'scheduled_at', type: 'datetime' },
      { field: 'notes', type: 'long_text' },
    ],
    ...overrides,
  };
}

describe('proposal draft helpers', () => {
  it('builds a draft only from declared editable fields', () => {
    expect(buildEditableDraft(action())).toEqual({
      status: 'applied',
      priority: 2,
      remote: false,
      scheduled_at: '2026-07-10T01:30:00.000Z',
      notes: '原始备注',
    });
  });

  it('submits only valid changed editable values with type-sensitive comparison', () => {
    expect(
      changedEditableArgs(action(), {
        status: 'interview',
        priority: 2,
        remote: true,
        scheduled_at: '2026-07-11T01:30:00.000Z',
        notes: '原始备注',
        id: 99,
        application_id: 99,
        unknown: 'leak',
      }),
    ).toEqual({
      status: 'interview',
      remote: true,
      scheduled_at: '2026-07-11T01:30:00.000Z',
    });
  });

  it('omits unchanged and invalid temporary values', () => {
    expect(
      changedEditableArgs(action(), {
        status: 'not-an-option',
        priority: Number.NaN,
        remote: undefined,
        scheduled_at: '',
        notes: '原始备注',
      }),
    ).toBeUndefined();
  });

  it('omits decimal numbers because current editable number fields are integral', () => {
    expect(changedEditableArgs(action(), { priority: 2.5 })).toBeUndefined();
  });

  it('normalizes valid changed datetimes to RFC3339 output', () => {
    expect(
      changedEditableArgs(action(), { scheduled_at: '2026-07-11T09:30:00+08:00' }),
    ).toEqual({ scheduled_at: '2026-07-11T01:30:00.000Z' });
  });

  it('preserves exact declared clear sentinels as changed editable values', () => {
    const clearable = action({
      args: {
        id: 7,
        remind_at: '2026-07-10T01:30:00.000Z',
        deadline: '2026-07-20T10:00:00.000Z',
        round: 2,
        base_monthly: 30000,
        signing_bonus: 50000,
      },
      editable_fields: [
        { field: 'remind_at', type: 'datetime', clearable: true, clear_value: '' },
        { field: 'deadline', type: 'datetime', clearable: true, clear_value: '' },
        { field: 'round', type: 'number', clearable: true, clear_value: 0 },
        { field: 'base_monthly', type: 'number', clearable: true, clear_value: 0 },
        { field: 'signing_bonus', type: 'number', clearable: true, clear_value: 0 },
      ],
    });

    expect(
      changedEditableArgs(clearable, {
        remind_at: '',
        deadline: '',
        round: 0,
        base_monthly: 0,
        signing_bonus: 0,
        id: 99,
      }),
    ).toEqual({
      remind_at: '',
      deadline: '',
      round: 0,
      base_monthly: 0,
      signing_bonus: 0,
    });
    expect(changedEditableArgs(clearable, { remind_at: null, round: false })).toBeUndefined();
  });

  it('rejects malformed non-scalar clear metadata defensively', () => {
    const sentinel = {};
    const malformed = action({
      args: { remind_at: '2026-07-10T01:30:00.000Z' },
      editable_fields: [
        {
          field: 'remind_at',
          type: 'datetime',
          clearable: true,
          clear_value: sentinel,
        } as never,
      ],
    });

    expect(changedEditableArgs(malformed, { remind_at: sentinel })).toBeUndefined();
  });

  it('preserves value types when deciding whether a field changed', () => {
    const typed = action({
      args: { count: 1 },
      editable_fields: [{ field: 'count', type: 'number' }],
    });
    expect(changedEditableArgs(typed, { count: '1' })).toBeUndefined();
    expect(changedEditableArgs(typed, { count: 1 })).toBeUndefined();
    expect(changedEditableArgs(typed, { count: 2 })).toEqual({ count: 2 });
  });

  it('uses confirmation token as identity and a stable action fallback without one', () => {
    expect(actionIdentity(action())).toBe('token:token-1');
    const first = action({ confirmation_token: '', args: { b: 2, a: { y: 2, x: 1 } } });
    const reordered = action({ confirmation_token: '', args: { a: { x: 1, y: 2 }, b: 2 } });
    expect(actionIdentity(first)).toBe(actionIdentity(reordered));
  });

  it('preserves in-progress review state for the same action and resets for a new action', () => {
    const current = {
      ...createProposalReviewState(action()),
      draft: { status: 'interview' },
      editorOpen: true,
      rejectOpen: true,
      feedback: ' 先等等 ',
    };

    expect(syncProposalReviewState(current, action({ human: 'provider rerender' }))).toBe(current);
    expect(
      syncProposalReviewState(current, action({ confirmation_token: 'token-2' })),
    ).toEqual({
      identity: 'token:token-2',
      draft: {
        status: 'applied',
        priority: 2,
        remote: false,
        scheduled_at: '2026-07-10T01:30:00.000Z',
        notes: '原始备注',
      },
      editorOpen: false,
      rejectOpen: false,
      feedback: '',
    });
  });

  it('does not expose an editor for deletes or empty descriptors', () => {
    expect(editableFieldsForAction(action({ tool_name: 'delete_application' }))).toEqual([]);
    expect(editableFieldsForAction(action({ editable_fields: [] }))).toEqual([]);
    expect(editableFieldsForAction(action({ editable_fields: undefined }))).toEqual([]);
  });
});
