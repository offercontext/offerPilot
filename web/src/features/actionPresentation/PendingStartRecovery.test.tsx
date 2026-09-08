// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { PendingStartRecovery } from './PendingStartRecovery';
import { rememberPendingStart, listPendingStarts, forgetPendingStart } from '@/services/chatSubmission';
const { read } = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock('@/services/chat', () => ({ getPilotRequest: read }));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
function renderRecovery(open: (id: number) => Promise<void>) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root.render(<PendingStartRecovery busy={false} onOpen={open} />));
}
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  for (const item of listPendingStarts()) forgetPendingStart(item.requestId);
  vi.resetAllMocks();
});

it('restores the original conversation from persisted request identity through a read', async () => {
  const key = crypto.randomUUID();
  rememberPendingStart(key, 0);
  read.mockResolvedValue({ conversation_id: 12, turn_id: 'turn', state: 'completed' });
  const open = vi.fn().mockResolvedValue(undefined);
  renderRecovery(open);
  await act(async () => host.querySelector('button')!.click());
  expect(open).toHaveBeenCalledWith(12, { refresh: true });
  expect(read).toHaveBeenCalledWith(key);
  expect(listPendingStarts()).toEqual([]);
});

it('keeps an unknown result after failed lookup and never guesses completion', async () => {
  rememberPendingStart(crypto.randomUUID(), 0);
  read.mockRejectedValueOnce({ response: { status: 404 } })
    .mockResolvedValueOnce({ conversation_id: 12, state: 'started' });
  const open = vi.fn().mockResolvedValue(undefined);
  renderRecovery(open);
  await act(async () => host.querySelector('button')!.click());
  expect(host.querySelector('[role="status"]')!.textContent).toContain('暂未找到');
  expect(listPendingStarts()).toHaveLength(1);
  await act(async () => host.querySelector('button')!.click());
  expect(host.querySelector('[role="status"]')!.textContent).toContain('尚无最终状态');
  expect(listPendingStarts()).toHaveLength(1);
});

it('does not navigate back when the user switches conversation during a lookup', async () => {
  rememberPendingStart(crypto.randomUUID(), 0);
  let resolve!: (value: unknown) => void;
  read.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const open = vi.fn().mockResolvedValue(undefined);
  renderRecovery(open);
  act(() => host.querySelector('button')!.click());
  act(() => root.render(<PendingStartRecovery busy={false} conversationId={99} onOpen={open} />));
  await act(async () => resolve({ conversation_id: 12, state: 'completed' }));
  expect(open).not.toHaveBeenCalled();
  expect(listPendingStarts()).toHaveLength(1);
});

it('does not navigate to an old task when a new submission starts during lookup', async () => {
  rememberPendingStart(crypto.randomUUID(), 0);
  let resolve!: (value: unknown) => void;
  read.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const open = vi.fn().mockResolvedValue(undefined);
  renderRecovery(open);
  act(() => host.querySelector('button')!.click());
  act(() => root.render(<PendingStartRecovery busy onOpen={open} />));
  await act(async () => resolve({ conversation_id: 12, state: 'completed' }));
  expect(open).not.toHaveBeenCalled();
  expect(listPendingStarts()).toHaveLength(1);
});
