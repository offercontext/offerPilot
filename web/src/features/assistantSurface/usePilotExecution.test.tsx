// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { usePilotExecution } from './usePilotExecution';
import type { PilotExecution, PilotInterruptResult } from '@/types/chat';

const { read, interrupt } = vi.hoisted(() => ({ read: vi.fn(), interrupt: vi.fn() }));
vi.mock('@/services/chat', () => ({ getPilotExecution: read, interruptPilotExecution: interrupt }));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let host: HTMLDivElement;
let controller: ReturnType<typeof usePilotExecution>;
const stopped = vi.fn();
const first: PilotExecution = { turn_id: 'turn-one', conversation_id: 1, execution_generation: 1, state: 'running' };
function Consumer({ id }: { id: number }) { controller = usePilotExecution(id, stopped); return null; }
async function render(id = 1) {
  if (!root) { host = document.createElement('div'); root = createRoot(host); }
  await act(async () => { root.render(<Consumer id={id} />); });
}
afterEach(() => {
  act(() => root?.unmount());
  root = undefined as unknown as Root;
  vi.clearAllMocks();
  localStorage.clear();
  vi.useRealTimers();
});

it('discovers an execution owned by another page without a local request', async () => {
  read.mockResolvedValue(first);
  await render();
  expect(controller.execution).toEqual(first);
  expect(controller.canStop).toBe(true);
});

it.each(['completed', 'stopped', 'interrupted'] as const)('notifies the owner when a manually opened conversation already has a %s execution', async (state) => {
  vi.useFakeTimers();
  read.mockResolvedValue({ ...first, state });
  await render();
  expect(stopped).toHaveBeenCalledWith({ ...first, state });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(stopped).toHaveBeenCalledTimes(1);
});

it('retries an uncertain stop with the identical command after remount', async () => {
  read.mockResolvedValue(first);
  interrupt.mockRejectedValueOnce(new Error('lost response')).mockImplementation(async (target, key) => ({
    command_id: key, turn_id: target.turn_id, execution_generation: target.execution_generation, status: 'stopped',
  }));
  await render();
  await act(async () => { await controller.stop(); });
  const original = interrupt.mock.calls[0];
  expect(controller.stopMessage).toContain('未确认');
  act(() => root.unmount()); root = undefined as unknown as Root;
  await render();
  await act(async () => { await controller.stop(); });
  expect(interrupt.mock.calls[1]).toEqual(original);
  expect(stopped).toHaveBeenCalledWith({ ...first, state: 'stopped' });
});

it('passes a terminal target to the owner when the stop command finds an ended task', async () => {
  read.mockResolvedValue(first);
  interrupt.mockImplementation(async (target, commandId) => ({
    command_id: commandId,
    turn_id: target.turn_id,
    execution_generation: target.execution_generation,
    status: 'already_ended',
  }));
  await render();
  await act(async () => { await controller.stop(); });
  expect(stopped).toHaveBeenCalledWith({ ...first, state: 'completed' });
  expect(controller.execution?.state).toBe('completed');
});

it('never applies a late stop result to a different conversation', async () => {
  read.mockImplementation(async (id) => ({ ...first, conversation_id: id, turn_id: `turn-${id}` }));
  let resolve!: (result: PilotInterruptResult) => void;
  interrupt.mockImplementation(() => new Promise<PilotInterruptResult>((done) => { resolve = done; }));
  await render();
  let pending!: Promise<void>;
  act(() => { pending = controller.stop(); });
  await render(2);
  await act(async () => {
    resolve({ command_id: interrupt.mock.calls[0][1], turn_id: 'turn-1', execution_generation: 1, status: 'stopped' });
    await pending;
  });
  expect(controller.execution?.conversation_id).toBe(2);
  expect(controller.stopMessage).toBe('');
  expect(stopped).not.toHaveBeenCalled();
});

it('does not offer stop for Pending and does not turn stop into rejection', async () => {
  read.mockResolvedValue({ ...first, state: 'waiting_confirmation' });
  await render();
  expect(controller.canStop).toBe(false);
  await act(async () => { await controller.stop(); });
  expect(interrupt).not.toHaveBeenCalled();
});

it('binds an accepted execution immediately and drops an older in-flight read', async () => {
  let resolve!: (value: PilotExecution) => void;
  read.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  await render();
  const accepted = { ...first, turn_id: 'new-turn' };
  act(() => controller.acceptExecution(accepted));
  expect(controller.canStop).toBe(true);
  await act(async () => { resolve(first); });
  expect(controller.execution).toEqual(accepted);
});

it('does not abort or show stopped feedback for a newer execution in the same conversation', async () => {
  read.mockResolvedValue(first);
  let resolve!: (value: PilotInterruptResult) => void;
  interrupt.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  await render();
  let pending!: Promise<void>;
  act(() => { pending = controller.stop(); });
  const newer = { ...first, execution_generation: 2 };
  act(() => controller.acceptExecution(newer));
  await act(async () => {
    resolve({ command_id: interrupt.mock.calls[0][1], turn_id: first.turn_id, execution_generation: 1, status: 'stopped' });
    await pending;
  });
  expect(controller.execution).toEqual(newer);
  expect(controller.stopMessage).toBe('');
  expect(stopped).not.toHaveBeenCalled();
});

it('clearing one receipt preserves another tab’s pending stop command', async () => {
  read.mockResolvedValue(first);
  const otherKey = crypto.randomUUID();
  const otherTarget = { ...first, execution_generation: 2 };
  interrupt.mockImplementationOnce(async (target, commandId) => {
    localStorage.setItem(`offerpilot.pending_interrupt.v2.${otherKey}`, JSON.stringify({ target: otherTarget, commandId: otherKey }));
    return { command_id: commandId, turn_id: target.turn_id, execution_generation: target.execution_generation, status: 'stopped' };
  }).mockImplementationOnce(async (target, commandId) => ({
    command_id: commandId, turn_id: target.turn_id, execution_generation: target.execution_generation, status: 'already_ended',
  }));
  await render();
  await act(async () => { await controller.stop(); });
  expect(localStorage.getItem(`offerpilot.pending_interrupt.v2.${otherKey}`)).not.toBeNull();
  await act(async () => { await controller.stop(); });
  expect(interrupt.mock.calls[1]).toEqual([otherTarget, otherKey]);
  expect(localStorage.getItem(`offerpilot.pending_interrupt.v2.${otherKey}`)).toBeNull();
});

it('ends the exact local subscription when another page durably stops it', async () => {
  vi.useFakeTimers();
  read.mockResolvedValueOnce(first).mockResolvedValue({ ...first, state: 'stopped' });
  await render();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(stopped).toHaveBeenCalledWith({ ...first, state: 'stopped' });
  expect(controller.execution?.state).toBe('stopped');
  expect(interrupt).not.toHaveBeenCalled();
});

it.each(['completed', 'waiting_confirmation', 'failed'] as const)('refreshes persisted content when a detached execution becomes %s', async (state) => {
  vi.useFakeTimers();
  read.mockResolvedValueOnce(first).mockResolvedValue({ ...first, state });
  await render();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(stopped).toHaveBeenCalledWith({ ...first, state });
  expect(controller.stopMessage).not.toContain('中断');
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(stopped).toHaveBeenCalledTimes(1);
  expect(interrupt).not.toHaveBeenCalled();
});
