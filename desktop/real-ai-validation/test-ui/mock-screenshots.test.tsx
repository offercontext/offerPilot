// Exercise the production renderer guard against the real task controller,
// CoreTaskSurfaceHost and generated OfferNegotiationDrawer DOM. jsdom has no
// layout engine: only getClientRects is supplied for visibility; these tests
// create no screenshots and do not claim installed Windows visual evidence.
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import path from 'node:path';
import os from 'node:os';
import { createMockScreenshots, GUARD_REASONS } from '../mock-screenshots.mjs';

const mocks = vi.hoisted(() => ({ preview: vi.fn(), create: vi.fn() }));
vi.mock('@/services/offers', () => ({
  previewOfferNegotiation: mocks.preview, createOfferNegotiationProposal: mocks.create,
  listOfferNegotiationProposals: async () => [], listOfferComparisonDimensions: async () => [],
  listOfferComparisonValues: async () => [], confirmOfferNegotiationProposal: vi.fn(),
  getOfferNegotiationProposal: vi.fn(), OfferNegotiationError: class extends Error {},
}));
import OfferNegotiationDrawer from '../../../web/src/components/OfferNegotiationDrawer';
import { CoreTaskSurfaceHost } from '../../../web/src/features/coreTaskSurface/CoreTaskSurfaceHost';
import { createCoreTaskSurfaceController } from '../../../web/src/features/coreTaskSurface/controller';

const token = 'synthetic-screenshot-test-token-1234567890';
const offer = { id: 4, application_id: 1, company_name: '合成验收公司', position_name: '合成工程师',
  status: 'pending', base_monthly: 20000, months_per_year: 12, signing_bonus: 0, total_cash: 240000 } as any;
let host: HTMLDivElement, root: ReturnType<typeof createRoot>, guard: (options: object) => Promise<string>;
let owner: HTMLElement, drawer: HTMLElement, nav: HTMLElement;
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const checkGuard = async (screenId = 'offer-negotiation') => {
  const reason = await guard({ screenId, stage: 'offer-negotiation', tokens: [token] });
  expect(GUARD_REASONS).toContain(reason);
  return reason;
};
beforeEach(async () => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubEnv('NODE_ENV', 'development');
  window.matchMedia = vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(function (this: HTMLElement) {
    return this.isConnected && !this.closest('[hidden], [style*="display: none"]') ? [{}] as any : [] as any;
  });
  // Capture the function that the collector actually sends to the renderer,
  // rather than exporting or reimplementing a test-only copy of the guard.
  const collector = createMockScreenshots({ directory: path.join(os.tmpdir(), 'unused-offer-guard-test'), mode: 'mock' });
  collector.registerToken(token);
  await collector.capture('offer-negotiation', {
    async evaluate(fn: Function) { guard = window.eval(`(${fn.toString()})`); return 'BUSINESS_SURFACE'; },
    async screenshot() { throw new Error('guard-only fixture must never take a screenshot'); },
  }, { stage: 'offer-negotiation' });
  window.offerpilotDesktop = { role: 'owner' } as any;
  const snapshot = { snapshot_version: 1, offer_snapshot: { ...offer, dimensions: [] }, user_brief: { goal: 'goal', concerns: 'concerns', scenario: 'scenario' } };
  mocks.preview.mockResolvedValue({ source_fingerprint: 'synthetic', snapshot });
  mocks.create.mockResolvedValue({ id: 7, offer_id: 4, application_id: 1, attempt_status: 'ready', proposal_status: 'normal', source_changed: false, input_snapshot: snapshot,
    proposal: { proposal_status: 'normal', communication_goals: [], clarification_questions: [],
      talking_points: [{ id: 'talk-1', text: '请确认合成 Offer 的固定薪资构成。', evidence_refs: [] }], preparation_checks: [] } });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  const controller = createCoreTaskSurfaceController();
  controller.launch({ ref: { taskId: 'application.offer_review', applicationId: 1 }, source: 'application_header', hints: { suggestedOfferId: 4 } });
  // AppShell.openOfferNegotiation selects this bound application and switches
  // to board (投递); ApplicationDetail renders the drawer inside this host.
  await act(async () => root.render(<>
    <nav aria-label="主导航"><button aria-current="page" aria-label="投递">投递</button></nav>
    <CoreTaskSurfaceHost controller={controller}>
      <OfferNegotiationDrawer open offer={offer} onClose={() => {}} />
    </CoreTaskSurfaceHost>
  </>));
  await flush();
  owner = host.querySelector('[data-core-task-owner]')!;
  drawer = owner.querySelector('[data-testid="offer-negotiation-drawer"]')!;
  nav = host.querySelector('nav [aria-current="page"]')!;
  await act(async () => owner.dispatchEvent(new Event('animationend', { bubbles: true })));
  for (const field of ['goal', 'concerns', 'scenario']) {
    const node = drawer.querySelector(`[id$="-${field}"]`) as HTMLInputElement | HTMLTextAreaElement;
    expect(node).not.toBeNull();
    await act(async () => {
      Object.getOwnPropertyDescriptor(node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(node, field);
      node.dispatchEvent(new Event('input', { bubbles: true }));
      node.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }
  await act(async () => (drawer.querySelector('[data-testid="offer-negotiation-generate"]') as HTMLButtonElement).click());
  await flush();
  await act(async () => (drawer.querySelector('[data-action="confirm-generate"]') as HTMLButtonElement).click());
  await flush();
  expect(drawer.querySelector('[aria-label="谈薪准备草稿"]')?.textContent).toContain('请确认合成 Offer');
  expect(drawer.querySelectorAll('[data-testid="offer-negotiation-confirm"]')).toHaveLength(1);
});
afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host?.remove(); delete window.offerpilotDesktop;
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});

it('accepts the generated draft inside the real unique bound Offer task on the board', async () => {
  expect(owner.getAttribute('data-core-task-owner')).toBe('application-offer-review');
  expect(owner.getAttribute('data-core-task-key')).toBe('application.offer_review:applicationId=1');
  expect(await checkGuard()).toBe('PASSED');
  expect(await checkGuard('failure-owner')).toBe('PASSED');
  nav.setAttribute('aria-label', 'Offer');
  expect(await checkGuard()).toBe('PASSED'); // Existing fixed Offer route remains valid.
});

it('rejects unknown navigation, missing/wrong task identity and a drawer outside its task', async () => {
  nav.setAttribute('aria-label', '日历');
  expect(await checkGuard()).toBe('BUSINESS_SURFACE');
  nav.setAttribute('aria-label', '投递');
  owner.removeAttribute('data-core-task-owner');
  expect(await checkGuard()).toBe('BUSINESS_SURFACE');
  owner.setAttribute('data-core-task-owner', 'application-interview-prepare');
  expect(await checkGuard()).toBe('BUSINESS_SURFACE');
  owner.setAttribute('data-core-task-owner', 'application-offer-review');
  for (const key of ['', 'application.offer_review', 'application.offer_review:applicationId=0',
    'application.offer_review:applicationId=-1', 'application.offer_review:applicationId=9007199254740992',
    'application.interview_prepare:applicationId=1', 'application.offer_review:applicationId=1:extra=2']) {
    owner.setAttribute('data-core-task-key', key);
    expect(await checkGuard(), key).toBe('BUSINESS_SURFACE');
  }
  owner.setAttribute('data-core-task-key', 'application.offer_review:applicationId=1');
  const parent = drawer.parentElement!;
  host.appendChild(drawer);
  expect(await checkGuard()).toBe('BUSINESS_SURFACE');
  parent.appendChild(drawer);
  expect(await checkGuard()).toBe('PASSED');
});

it('rejects duplicate or hidden task owners and duplicate result roots', async () => {
  const extraNav = nav.cloneNode(true) as HTMLElement;
  extraNav.setAttribute('aria-label', 'Offer'); nav.parentElement!.appendChild(extraNav);
  expect(await checkGuard()).toBe('BUSINESS_SURFACE');
  extraNav.remove();
  const other = owner.cloneNode(true) as HTMLElement;
  host.appendChild(other);
  expect(await checkGuard()).toBe('BUSINESS_SURFACE');
  other.replaceChildren(); // An unrelated visible owner is still ambiguous.
  other.setAttribute('data-core-task-owner', 'application-interview-prepare');
  expect(await checkGuard()).toBe('BUSINESS_SURFACE');
  other.remove();
  owner.hidden = true;
  expect(await checkGuard()).toBe('BUSINESS_SURFACE');
  owner.hidden = false;
  const duplicate = drawer.cloneNode(true);
  owner.appendChild(duplicate);
  expect(await checkGuard()).toBe('BUSINESS_SURFACE');
  duplicate.remove();
  expect(await checkGuard()).toBe('PASSED');
});

it('retains credential, token and embedded-surface refusals on the new board route, including failure capture', async () => {
  for (const [html, reason] of [
    ['<input type="password" hidden>', 'CREDENTIAL_SURFACE'],
    ['<input aria-label="API key">', 'CREDENTIAL_CONTROL'],
    [`<p>${token}</p>`, 'TOKEN_VISIBLE'],
    ['<iframe></iframe>', 'UNINSPECTABLE_CONTENT'],
  ]) {
    const unsafe = document.createElement('div'); unsafe.innerHTML = html; host.appendChild(unsafe);
    expect(await checkGuard()).toBe(reason);
    expect(await checkGuard('failure-owner')).toBe(reason);
    unsafe.remove();
  }
  expect(await checkGuard()).toBe('PASSED');
});
