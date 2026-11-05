// @vitest-environment jsdom
// Naming/transport regression only; pixel layout remains the real-browser gate.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { createMascotFixture } from './mascot-fixture.mjs';
import { publishCompanionSnapshot, publishPersona } from '../../src/features/companionPersona/useCompanionName';
import PilotMascot from '../../src/features/pilotMascot/PilotMascot';
import { setLocale } from '../../src/i18n';

const state = vi.hoisted(() => ({ api: null as any, requests: [] as string[] }));
vi.mock('@/services/http', () => ({
  createApiClient: ({ baseURL = '/api' } = {}) => Object.fromEntries(['get', 'post', 'put', 'patch', 'delete'].map(method => [method, async (path: string) => {
    state.requests.push(`${method.toUpperCase()} ${baseURL}${path}`);
    const response = state.api?.handle(method.toUpperCase(), baseURL + path);
    if (!response) throw new Error('No synthetic response');
    return response;
  }])),
}));
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  state.api = null; state.requests = []; localStorage.clear(); setLocale('zh-CN');
  host = document.body.appendChild(document.createElement('div')); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });
async function mount() {
  await act(async () => root.render(<PilotMascot activity="idle" panelOpen={false} onHide={() => {}} onTogglePilot={() => {}}
    runtime={{ mount: async () => { throw new Error('Simulated model failure'); } }} />));
}

it('requires the layout opt-in read to name a rendered fallback and preserves names across opt-out', async () => {
  await mount();
  expect(host.querySelector('aside[aria-label="Pilot 助手"]')?.getAttribute('data-load-failed')).toBe('true');
  expect(host.querySelector('aside[aria-label="Haru 助手"]')).toBeNull();
  state.requests = [];
  state.api = createMascotFixture();
  await act(async () => window.dispatchEvent(new Event('focus')));
  expect(host.querySelector('aside[aria-label="Haru 助手"]')?.getAttribute('data-load-failed')).toBe('true');
  expect(state.requests.sort()).toEqual(['GET /api/companion', 'GET /api/companion/persona']);
  const snapshot = state.api.handle('GET', '/api/companion').data;
  const persona = state.api.handle('GET', '/api/companion/persona').data;
  act(() => publishCompanionSnapshot({ ...snapshot, revision: snapshot.revision + 1, settings: { ...snapshot.settings, enabled: false } }));
  expect(host.querySelector('aside')?.getAttribute('aria-label')).toBe('Pilot 助手');
  act(() => publishPersona({ ...persona, revision: persona.revision + 1, spec: { ...persona.spec, display_name: 'Nova' } }));
  expect(host.querySelector('aside')?.getAttribute('aria-label')).toBe('Pilot 助手');
  act(() => publishCompanionSnapshot({ ...snapshot, revision: snapshot.revision + 2 }));
  expect(host.querySelector('aside')?.getAttribute('aria-label')).toBe('Nova 助手');
  expect(state.requests).toHaveLength(2);
});
