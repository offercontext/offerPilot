import { describe, expect, it, vi } from 'vitest';
import { desktopUpdateSafetySnapshot, readDesktopUpdateSafety, type DesktopUpdateSafetyInput } from './installSafety';
import type { Conversation, PilotExecution } from '@/types/chat';

function input(): DesktopUpdateSafetyInput {
  return {
    pilot: {
      composerDraft: '', attachments: [], lastFailedText: '', loading: false,
      activeRequestRef: { current: null }, streamingAssistantActiveRef: { current: false },
      executionControl: {
        execution: null, stopping: false, retryingStop: false, canStop: false,
        stopMessage: '', stop: async () => undefined, acceptExecution: () => undefined,
        settleExecution: () => false,
      },
      backgroundExecution: null, pending: null, activePendingRef: { current: null }, confirmPhase: 'idle',
      conversationId: 2, isActionOwnerReady: () => true,
    },
    taskGuard: { pending: false, unsaved: false },
    hasAttachmentDrafts: false, hasRetainedDrafts: false,
  };
}
const conversation = { id: 2 } as Conversation;
const execution = (state: PilotExecution['state']): PilotExecution => ({
  conversation_id: 2, turn_id: 'turn-2', execution_generation: 1, state,
});
const readers = () => ({ executions: vi.fn().mockResolvedValue(null), conversations: vi.fn().mockResolvedValue([conversation]), background: vi.fn().mockResolvedValue(false) });

describe('desktop install safety', () => {
  it('requires fresh persisted observations, including a second independent request', async () => {
    const value = input();
    const source = readers();
    expect(await readDesktopUpdateSafety(() => value, source)).toEqual({ ready: true, hasDraft: false, activeRun: false, pendingApproval: false });
    source.executions.mockResolvedValue(execution('running'));
    expect((await readDesktopUpdateSafety(() => value, source)).activeRun).toBe(true);
    expect(source.executions).toHaveBeenCalledTimes(2);
    expect(source.conversations).toHaveBeenCalledTimes(2);
  });

  it.each(['composer', 'failed-text', 'attachment-store', 'retained-draft', 'task-draft'])('blocks unsaved %s without sending contents', async (source) => {
    const value = input();
    if (source === 'composer') value.pilot.composerDraft = 'private draft';
    if (source === 'failed-text') value.pilot.lastFailedText = 'private failed message';
    if (source === 'attachment-store') value.hasAttachmentDrafts = true;
    if (source === 'retained-draft') value.hasRetainedDrafts = true;
    if (source === 'task-draft') value.taskGuard.unsaved = true;
    const snapshot = await readDesktopUpdateSafety(() => value, readers());
    expect(snapshot.hasDraft).toBe(true);
    expect(JSON.stringify(snapshot)).not.toContain('private');
  });

  it.each(['loading', 'streaming', 'saving', 'stopping', 'unknown-stop', 'task-pending'])('blocks active %s', (source) => {
    const value = input();
    if (source === 'loading') value.pilot.loading = true;
    if (source === 'streaming') value.pilot.streamingAssistantActiveRef.current = true;
    if (source === 'saving') value.pilot.confirmPhase = 'saving';
    if (source === 'stopping') value.pilot.executionControl.stopping = true;
    if (source === 'unknown-stop') value.pilot.executionControl.retryingStop = true;
    if (source === 'task-pending') value.taskGuard.pending = true;
    expect(desktopUpdateSafetySnapshot(value).activeRun).toBe(true);
  });

  it('rereads local drafts and ref-owned requests after the network read', async () => {
    const value = input();
    const source = readers();
    source.conversations.mockImplementation(async () => {
      value.pilot.composerDraft = 'new unsaved text';
      value.pilot.activeRequestRef.current = { kind: 'chat', conversationId: 2, controller: new AbortController() };
      return [conversation];
    });
    const snapshot = await readDesktopUpdateSafety(() => value, source);
    expect(snapshot.hasDraft).toBe(true);
    expect(snapshot.activeRun).toBe(true);
  });

  it('blocks persisted approvals and unknown executions even when cached Pilot state is idle', async () => {
    const source = readers();
    source.executions.mockResolvedValue(execution('result_unknown'));
    source.conversations.mockResolvedValue([{ id: 2, pending_action: { confirmation_token: 'secret' } }]);
    const snapshot = await readDesktopUpdateSafety(input, source);
    expect(snapshot.activeRun).toBe(true);
    expect(snapshot.pendingApproval).toBe(true);
    expect(JSON.stringify(snapshot)).not.toContain('secret');
  });

  it('checks the known background execution as well as the selected conversation', async () => {
    const value = input();
    value.pilot.backgroundExecution = { ...execution('completed'), conversation_id: 3 };
    const source = readers();
    source.conversations.mockResolvedValue([conversation, { id: 3 }]);
    source.executions.mockImplementation(async (id: number) => id === 3 ? { ...execution('running'), conversation_id: 3 } : null);
    expect((await readDesktopUpdateSafety(() => value, source)).activeRun).toBe(true);
    expect(source.executions.mock.calls).toEqual([[2], [3]]);
  });

  it.each(['network', 'invalid-execution', 'missing-conversation', 'scope-change', 'owner-not-ready'])('fails closed for %s', async (problem) => {
    const value = input();
    const source = readers();
    if (problem === 'network') source.executions.mockRejectedValue(new Error('private network detail'));
    if (problem === 'invalid-execution') source.executions.mockResolvedValue({ state: 'completed' });
    if (problem === 'missing-conversation') source.conversations.mockResolvedValue([]);
    if (problem === 'scope-change') source.conversations.mockImplementation(async () => { value.pilot.conversationId = 3; return [conversation]; });
    if (problem === 'owner-not-ready') value.pilot.isActionOwnerReady = () => false;
    const snapshot = await readDesktopUpdateSafety(() => value, source);
    expect(snapshot.ready).toBe(false);
    expect(JSON.stringify(snapshot)).not.toContain('private');
  });
});
