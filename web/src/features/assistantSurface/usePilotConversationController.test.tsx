// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PilotExecution } from '@/types/chat';
import { usePilotConversationControllerState } from './usePilotConversationController';

const mocks = vi.hoisted(() => ({
  messages: vi.fn(), summaries: vi.fn(), refresh: vi.fn(), accept: vi.fn(),
  onStopped: undefined as ((target: PilotExecution) => void) | undefined,
}));
vi.mock('@/services/chat', () => ({
  getConversation: mocks.messages, listConversations: mocks.summaries,
  streamChat: vi.fn(), streamConfirmAction: vi.fn(),
}));
vi.mock('@/features/actionPresentation/usePilotPresentation', () => ({
  usePilotPresentation: () => ({ displayTurns: [], refreshPresentation: mocks.refresh,
    acceptRuntimeSnapshot: mocks.accept, presentationFailed: false, presentationRefreshing: false }),
}));
vi.mock('./usePilotExecution', async (importOriginal) => ({
  ...await importOriginal<typeof import('./usePilotExecution')>(),
  usePilotExecution: (_id: number | undefined, onStopped: (target: PilotExecution) => void) => {
    mocks.onStopped = onStopped;
    return { execution: null, stop: mocks.accept, acceptExecution: mocks.accept,
      canStop: false, stopping: false, stopMessage: '', retryingStop: false };
  },
}));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let controller: ReturnType<typeof usePilotConversationControllerState>;
const requestId = '12345678-1234-4123-8123-123456789abc';
const target: PilotExecution = { turn_id: 'recovered-turn', conversation_id: 1,
  execution_generation: 1, state: 'completed', protocol: 'pilot-runtime-v1', submission_request_id: requestId };
function Consumer() { controller = usePilotConversationControllerState(false); return null; }

beforeEach(async () => {
  vi.clearAllMocks();
  mocks.messages.mockResolvedValue([]);
  mocks.summaries.mockResolvedValue([{ id: 1 }]);
  root = createRoot(document.createElement('div'));
  await act(async () => { root.render(<Consumer />); });
  await act(async () => {
    controller.setConversationId(1);
    controller.setLastError('Failed to fetch');
    controller.setLastFailedText('原消息');
    controller.setComposerDraft('原消息');
    controller.lastSubmissionRef.current = { requestId, message: '原消息', conversationId: 1, context: {} };
  });
});
afterEach(() => { act(() => root.unmount()); localStorage.clear(); });

it.each(['completed', 'waiting_confirmation'] as const)('clears the matching recovered submission error after reading %s records', async (state) => {
  await act(async () => { mocks.onStopped?.({ ...target, state }); });
  expect(controller.lastError).toBeNull();
  expect(controller.lastFailedText).toBe('');
  expect(controller.composerDraft).toBe('');
  expect(controller.lastSubmissionRef.current).toBeNull();
});

it('preserves a newly typed draft after recovering an older submission', async () => {
  await act(async () => { controller.setComposerDraft('下一条问题草稿'); });
  await act(async () => { mocks.onStopped?.(target); });
  expect(controller.lastError).toBeNull();
  expect(controller.composerDraft).toBe('下一条问题草稿');
});

it.each(['failed', 'result_unknown', 'interrupted'] as const)('does not hide a real %s failure', async (state) => {
  await act(async () => { mocks.onStopped?.({ ...target, state }); });
  expect(controller.lastError).toBe('Failed to fetch');
  expect(controller.lastFailedText).toBe('原消息');
});

it('does not clear a different submission failure in the same conversation', async () => {
  await act(async () => { mocks.onStopped?.({ ...target, submission_request_id: 'another-request' }); });
  expect(controller.lastError).toBe('Failed to fetch');
  expect(controller.lastFailedText).toBe('原消息');
});

it('does not clear an error after the visible request changed during record reload', async () => {
  let resolve!: (value: []) => void;
  mocks.messages.mockImplementationOnce(() => new Promise<[]>((done) => { resolve = done; }));
  await act(async () => { mocks.onStopped?.(target); });
  await act(async () => {
    controller.visibleRequestGenerationRef.current += 1;
    controller.setLastError('新任务失败');
    resolve([]);
  });
  expect(controller.lastError).toBe('新任务失败');
});

it('keeps record-reload failure visible even when the execution completed', async () => {
  mocks.messages.mockRejectedValueOnce(new Error('offline'));
  await act(async () => { mocks.onStopped?.(target); });
  expect(controller.lastError).toContain('暂时无法读取记录');
  expect(controller.lastFailedText).toBe('原消息');
});
