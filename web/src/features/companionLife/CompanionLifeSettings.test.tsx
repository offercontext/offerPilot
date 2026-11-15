// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { getCompanion, sendCompanionCommand, reportCompanionPresence, claimCompanionBid } from '@/services/companion';
import { publishPersona, publishCompanionSnapshot, setCompanionDisabledPending, useCompanionName } from '@/features/companionPersona/useCompanionName';
import type { PersonaView } from '@/services/persona';
import type { PilotConversationController } from '@/features/assistantSurface/usePilotConversationController';
import { CompanionLifeContext, useCompanionLifeController, type CompanionLifeController } from './useCompanionLife';
import CompanionLifeSettings from './CompanionLifeSettings';
import { enabledCompanionSnapshot } from './testFixtures';
import type { CompanionSnapshot } from './contracts';
vi.mock('@/services/companion', () => ({ getCompanion: vi.fn(), sendCompanionCommand: vi.fn(), reportCompanionPresence: vi.fn(), claimCompanionBid: vi.fn() }));
vi.mock('@/services/persona', () => ({ getPersona: async () => ({ spec: { display_name: 'Nova' } }) }));
let root: Root, host: HTMLDivElement, life: CompanionLifeController, state: CompanionSnapshot;
let revision = 100;
const controller = { composerDraft: '', loading: false, pending: null, activePendingRef: { current: null }, executionControl: { execution: null }, taskState: 'idle', lastError: '', confirmError: '' } as unknown as PilotConversationController;
function Harness() { life = useCompanionLifeController(controller); return <CompanionLifeContext.Provider value={life}><output>{useCompanionName()}</output><CompanionLifeSettings /></CompanionLifeContext.Provider>; }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
async function mount() { await act(async () => root.render(<Harness />)); vi.mocked(sendCompanionCommand).mockClear(); }
async function toggle() { await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="开启陪伴功能"]')!.click()); }
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }) });
  state = { ...enabledCompanionSnapshot, revision: ++revision };
  setCompanionDisabledPending(false); publishCompanionSnapshot(state);
  vi.mocked(getCompanion).mockResolvedValue(state);
  vi.mocked(sendCompanionCommand).mockResolvedValue(state);
  vi.mocked(reportCompanionPresence).mockResolvedValue(state.presence); vi.mocked(claimCompanionBid).mockResolvedValue(null);
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
it('switches immediately to Pilot, blocks repeat clicks, preserves persona edits and restores them on re-enable', async () => {
  await mount(); expect(host.querySelector('output')?.textContent).toBe('Nova');
  expect(host.textContent).toContain('人设设置会保留，仍可编辑');
  const pending = deferred<CompanionSnapshot>(); vi.mocked(sendCompanionCommand).mockReturnValueOnce(pending.promise);
  await toggle(); await toggle();
  expect(host.querySelector('output')?.textContent).toBe('Pilot'); expect(sendCompanionCommand).toHaveBeenCalledTimes(1);
  expect(sendCompanionCommand).toHaveBeenCalledWith(expect.objectContaining({ kind: 'settings', body: expect.objectContaining({ settings: expect.objectContaining({ enabled: false }) }) }));
  state = { ...state, revision: ++revision, settings: { ...state.settings, enabled: false } };
  await act(async () => pending.resolve(state));
  act(() => publishPersona({ spec: { display_name: 'Edited' }, profile_id: 'fixture', revision: 2 } as PersonaView));
  expect(host.querySelector('output')?.textContent).toBe('Pilot');
  vi.mocked(sendCompanionCommand).mockResolvedValue({ ...state, revision: ++revision, settings: { ...state.settings, enabled: true } });
  await toggle(); expect(host.querySelector('output')?.textContent).toBe('Edited');
});
it('keeps an uncertain opt-out ordinary Pilot and retries the same command before restoring accepted state', async () => {
  await mount(); vi.mocked(sendCompanionCommand).mockRejectedValueOnce(new Error('lost response'));
  await toggle(); expect(life.uncertain).toBe(true); expect(host.querySelector('output')?.textContent).toBe('Pilot');
  const original = vi.mocked(sendCompanionCommand).mock.calls[0][0];
  vi.mocked(sendCompanionCommand).mockResolvedValue({ ...state, revision: ++revision, settings: { ...state.settings, enabled: false } });
  await act(async () => life.retry());
  expect(sendCompanionCommand).toHaveBeenLastCalledWith(original); expect(life.uncertain).toBe(false);
  expect(host.querySelector('output')?.textContent).toBe('Pilot');
});
it('restores the accepted enabled state after a rejected toggle', async () => {
  await mount(); vi.mocked(sendCompanionCommand).mockRejectedValueOnce({ response: { status: 409 } });
  await toggle(); expect(life.uncertain).toBe(false); expect(host.querySelector('output')?.textContent).toBe('Nova');
  expect(host.querySelector('[aria-label="开启陪伴功能"]')?.getAttribute('aria-checked')).toBe('true');
});
it('reconciles a pending opt-out against the fresh authoritative snapshot after remount', async () => {
  await mount(); vi.mocked(sendCompanionCommand).mockRejectedValueOnce(new Error('lost response'));
  await toggle(); expect(host.querySelector('output')?.textContent).toBe('Pilot');
  await act(async () => root.render(<Harness key="fresh-controller" />));
  expect(host.querySelector('output')?.textContent).toBe('Nova');
});
