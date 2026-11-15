// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { getPersona, type PersonaView } from '@/services/persona';
import { getCompanion } from '@/services/companion';
import type { CompanionSnapshot } from '@/features/companionLife/contracts';
import { COMPANION_CHANGED_KEY, publishCompanionSnapshot, publishPersona, setCompanionDisabledPending, useCompanionName } from './useCompanionName';
vi.mock('@/services/persona', () => ({ getPersona: vi.fn() }));
vi.mock('@/services/companion', () => ({ getCompanion: vi.fn() }));
let root: Root, host: HTMLDivElement;
let revision = 10;
const persona = (name = 'Nova') => ({ profile_id: 'test', revision: 1, spec: { display_name: name } } as PersonaView);
const snapshot = (enabled: boolean, version = ++revision) => ({ identity: 'test', revision: version, settings: { enabled } } as CompanionSnapshot);
function Surface() { return <span>{useCompanionName()}</span>; }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
async function mount() { await act(async () => root.render(<><Surface /><Surface /></>)); }
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks(); setCompanionDisabledPending(false);
  publishCompanionSnapshot(snapshot(false)); publishPersona(persona());
  vi.mocked(getPersona).mockResolvedValue(persona());
  vi.mocked(getCompanion).mockResolvedValue(snapshot(false));
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
it('keeps saved persona editable while off and restores its latest name only when enabled', async () => {
  await mount(); expect(host.textContent).toBe('PilotPilot');
  act(() => publishPersona(persona('Edited while off')));
  expect(host.textContent).toBe('PilotPilot');
  act(() => publishCompanionSnapshot(snapshot(true)));
  expect(host.textContent).toBe('Edited while offEdited while off');
  act(() => publishCompanionSnapshot(snapshot(false)));
  expect(host.textContent).toBe('PilotPilot');
});
it('does not let an older persona or enabled read override a newly accepted opt-out', async () => {
  const oldPersona = deferred<PersonaView>(); const oldLife = deferred<CompanionSnapshot>();
  const enabled = snapshot(true);
  vi.mocked(getPersona).mockReturnValue(oldPersona.promise); vi.mocked(getCompanion).mockReturnValue(oldLife.promise);
  await mount();
  act(() => { publishCompanionSnapshot(snapshot(false)); publishPersona(persona('New name')); });
  await act(async () => { oldPersona.resolve(persona('Old name')); oldLife.resolve(enabled); });
  expect(host.textContent).toBe('PilotPilot');
  act(() => publishCompanionSnapshot(snapshot(true)));
  expect(host.textContent).toBe('New nameNew name');
});
it('hides persona immediately for a pending opt-out, including unrelated server refreshes', async () => {
  vi.mocked(getCompanion).mockResolvedValue(snapshot(true)); await mount();
  expect(host.textContent).toBe('NovaNova');
  act(() => setCompanionDisabledPending(true));
  act(() => publishCompanionSnapshot(snapshot(true)));
  expect(host.textContent).toBe('PilotPilot');
  act(() => { setCompanionDisabledPending(false); publishCompanionSnapshot(snapshot(false)); });
  expect(host.textContent).toBe('PilotPilot');
});
it('invalidates cross-window state and coalesces repeated focus refreshes without replaying stale reads', async () => {
  vi.mocked(getCompanion).mockResolvedValue(snapshot(true)); await mount();
  expect(host.textContent).toBe('NovaNova');
  const stale = deferred<CompanionSnapshot>(); const off = snapshot(false);
  vi.mocked(getCompanion).mockReturnValueOnce(stale.promise).mockResolvedValue(off);
  act(() => window.dispatchEvent(new Event('focus')));
  act(() => window.dispatchEvent(new StorageEvent('storage', { key: COMPANION_CHANGED_KEY })));
  expect(host.textContent).toBe('PilotPilot');
  await act(async () => stale.resolve(snapshot(true, off.revision - 1)));
  expect(host.textContent).toBe('PilotPilot');
  expect(getCompanion).toHaveBeenCalledTimes(3);
  act(() => publishCompanionSnapshot(snapshot(true)));
  expect(host.textContent).toBe('NovaNova');
});

it('does not let a same-revision heartbeat clear storage invalidation or discard its fresh GET', async () => {
  const enabled = snapshot(true);
  vi.mocked(getCompanion).mockResolvedValue(enabled); await mount();
  expect(host.textContent).toBe('NovaNova');
  const fresh = deferred<CompanionSnapshot>();
  vi.mocked(getCompanion).mockReturnValueOnce(fresh.promise);
  act(() => window.dispatchEvent(new StorageEvent('storage', { key: COMPANION_CHANGED_KEY })));
  act(() => publishCompanionSnapshot({ ...enabled }));
  expect(host.textContent).toBe('PilotPilot');
  await act(async () => fresh.resolve(snapshot(false)));
  // If the heartbeat incorrectly fenced the GET, this old enabled revision
  // would still be accepted and expose Nova again.
  act(() => publishCompanionSnapshot(enabled));
  expect(host.textContent).toBe('PilotPilot');
  expect(getCompanion).toHaveBeenCalledTimes(2);
});
it('allows a fresh same-revision GET to confirm state after cross-window invalidation', async () => {
  vi.mocked(getCompanion).mockResolvedValue(snapshot(true)); await mount();
  await act(async () => window.dispatchEvent(new StorageEvent('storage', { key: COMPANION_CHANGED_KEY })));
  expect(host.textContent).toBe('NovaNova');
});
