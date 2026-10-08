// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useDesktopUpdateSafety } from './useDesktopUpdateSafety';
import type { DesktopUpdatesBridge, PrepareInstallRequest, UpdateSafetySnapshot } from './types';

let root: Root;
let container: HTMLDivElement;
let prepare: (request: PrepareInstallRequest) => void;
let bridge: DesktopUpdatesBridge;
const off = vi.fn();
const safe: UpdateSafetySnapshot = { ready: true, hasDraft: false, activeRun: false, pendingApproval: false };

function Owner({ read }: { read: () => UpdateSafetySnapshot | Promise<UpdateSafetySnapshot> }) {
  useDesktopUpdateSafety(read);
  return null;
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  root = createRoot(container);
  bridge = {
    getState: vi.fn(), check: vi.fn(), download: vi.fn(), install: vi.fn(), onState: vi.fn(),
    onPrepareInstall: vi.fn((callback) => { prepare = callback; return off; }),
    replyPrepareInstall: vi.fn(),
  };
  window.offerpilotUpdates = bridge;
});

afterEach(() => {
  act(() => root.unmount());
  delete window.offerpilotUpdates;
  vi.clearAllMocks();
});

describe('owner update safety handshake', () => {
  it('reads the latest owner state for both nonces and cleans up its listener', async () => {
    act(() => root.render(<Owner read={async () => safe} />));
    await act(async () => prepare({ id: 'before-confirmation' }));
    expect(bridge.replyPrepareInstall).toHaveBeenLastCalledWith('before-confirmation', safe);
    act(() => root.render(<Owner read={() => ({ ...safe, hasDraft: true })} />));
    await act(async () => prepare({ id: 'after-confirmation' }));
    expect(bridge.replyPrepareInstall).toHaveBeenLastCalledWith('after-confirmation', { ...safe, hasDraft: true });
    expect(bridge.onPrepareInstall).toHaveBeenCalledTimes(1);
    act(() => root.unmount());
    expect(off).toHaveBeenCalledTimes(1);
    root = createRoot(container);
  });

  it('fails closed if reading the owner throws and does not disclose the error', async () => {
    act(() => root.render(<Owner read={async () => { throw new Error('private draft'); }} />));
    await act(async () => prepare({ id: 'nonce' }));
    const snapshot = vi.mocked(bridge.replyPrepareInstall).mock.calls[0][1];
    expect(snapshot).toMatchObject({ ready: false, hasDraft: true, activeRun: true, pendingApproval: true });
    expect(snapshot.reason).not.toContain('private');
  });

  it('does not reply after its owner unmounts while checking', async () => {
    let resolve!: (snapshot: UpdateSafetySnapshot) => void;
    const pending = new Promise<UpdateSafetySnapshot>((done) => { resolve = done; });
    act(() => root.render(<Owner read={() => pending} />));
    act(() => prepare({ id: 'nonce' }));
    act(() => root.unmount());
    await act(async () => resolve(safe));
    expect(bridge.replyPrepareInstall).not.toHaveBeenCalled();
    root = createRoot(container);
  });

  it('ignores malformed request IDs and remains absent on the web', async () => {
    act(() => root.render(<Owner read={() => safe} />));
    await act(async () => prepare({ id: '' }));
    expect(bridge.replyPrepareInstall).not.toHaveBeenCalled();
    act(() => root.unmount());
    root = createRoot(container);
    delete window.offerpilotUpdates;
    act(() => root.render(<Owner read={() => safe} />));
    expect(bridge.onPrepareInstall).toHaveBeenCalledTimes(1);
  });
});
