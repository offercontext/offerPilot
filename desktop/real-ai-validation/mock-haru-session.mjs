// Only the offline entrypoint injects this capability. It never sends a prompt,
// publishes a mirror, writes storage, or substitutes a controller or response.
import { demand } from './contract.mjs';
import { sameWindowsPath } from '../installed-ui/contract.mjs';
import { Ledger, POLICY } from './broker-core.cjs';

export const HARU_SESSION_CHECKS = Object.freeze([
  'haruBrokerHistoryVisible', 'haruIndependentDraftsRetained', 'haruThemeMirrored',
  'haruNativeWindowRestore', 'haruSessionNoExtraRequests',
  'haruProductionTrayExit', 'haruProcessAndPortsReleased', 'haruSameProfileRestart',
  'haruRestartedHistoryVisible', 'haruRestartedThemeRetained',
]);
export const HARU_SESSION_STAGES = Object.freeze(['OPEN_PILOT', 'STORED_CONVERSATION', 'UNIQUE_ROW',
  'UNIQUE_TITLE', 'HISTORY_GET', 'BODY_PAIR', 'THEME', 'SCREENSHOT', 'COMPLETE']);
export const HARU_SESSION_DIAGNOSTIC_FLAGS = Object.freeze(['productionTrayExit', 'processAndPortsReleased', 'sameProfileAndExe', 'stored', 'storedTitleUnique', 'rowFound',
  'rowUnique', 'titleUnique', 'historyGet', 'ownerReady', 'haruReady', 'identityEqual', 'bodyEqual', 'themeEqual']);
const OWNER_DRAFT = '【MOCK 主窗草稿】保留此处未发送输入。';
const HARU_DRAFT = '【MOCK Haru 草稿】这是独立的未发送输入。';
const normalize = value => typeof value === 'string' ? value.replace(/\s+/gu, ' ').trim() : '';
const reject = () => demand(false, 'HARU_SYNC_FAILED');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const exact = name => ({ name, exact: true });
const ownerInput = page => page.locator('[data-onboarding-target="pilot"]').getByPlaceholder('问问领航员，或输入 / 唤起能力', { exact: true });
const DENIED_KEYS = Object.keys(new Ledger().snapshot().denied).sort();

export function assertHaruRestartFacts(first, second, exited, portsClosed) {
  demand(exited?.processes?.length === 0 && portsClosed === true, 'OWNED_PROCESS_CLEANUP_FAILED');
  demand(sameWindowsPath(first.identity.exe, second.identity.exe)
    && sameWindowsPath(first.identity.profile, second.identity.profile)
    && first.owned.main.pid !== second.owned.main.pid && first.owned.backend.pid !== second.owned.backend.pid
    && first.owned.main.created !== second.owned.main.created && first.owned.backend.created !== second.owned.backend.created
    && first.port === second.port, 'INSTALLED_PROCESS_IDENTITY_MISMATCH');
  return { productionTrayExit: true, processAndPortsReleased: true, sameProfileAndExe: true };
}

export function mockSessionLedger(broker) {
  const value = broker?.snapshot?.();
  demand(broker?.mode === 'MOCK' && value?.mode === 'MOCK'
    && value.mock?.transport === 'IN_PROCESS_SYNTHETIC_HTTPS' && value.mock.externalNetwork === 'DENIED'
    && value.mock.realProviderCalls === 0 && value.mock.usageIsSynthetic === true
    && value.mock.fakeRequests === value.sentRequests && value.active === false && value.closed === false
    && value.journalFailed === false && Array.isArray(value.requests)
    && value.model === POLICY.model && value.budgetMicroCny === POLICY.budgetMicroCny
    && value.reserveMicroCny === POLICY.reserveMicroCny
    && value.requests.length === value.sentRequests && value.sentRequests <= 8
    && Object.keys(value.denied || {}).sort().join('|') === DENIED_KEYS.join('|')
    && DENIED_KEYS.every(key => value.denied[key] === 0), 'INVALID_HARNESS');
  const stream = value.requests.filter(row => row.caseId === 'pilot-stream');
  demand(stream.length === 1 && stream[0].status === 'SETTLED'
    && stream[0].outboundStarted === true && stream[0].upstreamResponded === true
    && stream[0].clientDisconnectObserved === false, 'HARU_SYNC_FAILED');
  // Keep the full local ledger only in memory; no tokens or bodies enter proof.
  return JSON.stringify(value);
}

// Serialized into the real renderer. Raw synthetic text stays in memory and
// never enters report.json. Every proof requires this window's visible body.
export async function readHaruSessionRenderer({ role, origin }) {
  const bridge = window.offerpilotDesktop;
  const surfaceQueryValid = role === 'haru' ? location.search === '?desktopSurface=haru'
    : !new URLSearchParams(location.search).has('desktopSurface');
  if (!['owner', 'haru'].includes(role) || location.origin !== origin || location.pathname !== '/'
    || !surfaceQueryValid || bridge?.role !== role || typeof bridge.getState !== 'function') return { ready: false };
  const value = await bridge.getState();
  const snapshot = value?.snapshot;
  if (document.visibilityState !== 'visible' || value?.connected !== true
    || !Number.isSafeInteger(value.generation) || value.generation < 0
    || !Number.isSafeInteger(snapshot?.conversationId) || snapshot.conversationId <= 0
    || snapshot.loading !== false || snapshot.hasPending !== false || snapshot.canStop !== false
    || snapshot.stopping !== false || snapshot.canSend !== true || snapshot.error || snapshot.stopMessage
    || !['idle', 'completed'].includes(snapshot.taskState) || !Array.isArray(snapshot.messages)) return { ready: false };
  const visible = node => {
    if (!node?.isConnected || !node.getClientRects().length) return false;
    let box = node.getBoundingClientRect();
    let left = Math.max(0, box.left), top = Math.max(0, box.top);
    let right = Math.min(innerWidth, box.right), bottom = Math.min(innerHeight, box.bottom);
    for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      if (ancestor.hidden || style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility)
        || style.contentVisibility === 'hidden' || Number(style.opacity) === 0) return false;
      if (ancestor !== node) {
        box = ancestor.getBoundingClientRect();
        if (/^(auto|scroll|hidden|clip)$/.test(style.overflowX)) { left = Math.max(left, box.left); right = Math.min(right, box.right); }
        if (/^(auto|scroll|hidden|clip)$/.test(style.overflowY)) { top = Math.max(top, box.top); bottom = Math.min(bottom, box.bottom); }
      }
    }
    return right - left >= 8 && bottom - top >= 8;
  };
  const roots = [...document.querySelectorAll(role === 'owner'
    ? '[data-onboarding-target="pilot"]' : 'main[aria-label="Haru 桌面小窗"]')];
  if (roots.length !== 1 || !visible(roots[0])) return { ready: false };
  const root = roots[0];
  if (root.querySelector('article[aria-label^="本轮任务："]')) return { ready: false };
  const body = [...root.querySelectorAll(role === 'owner' ? '[class*="bubbleAssistant"]'
    : '.desktop-haru-messages article[data-role="assistant"] p')].at(-1);
  const text = body?.textContent;
  const bridgeText = snapshot.messages.filter(item => item?.role === 'assistant').at(-1)?.content;
  const input = role === 'owner' ? root.querySelector('textarea[placeholder="问问领航员，或输入 / 唤起能力"]')
    : root.querySelector('#desktop-haru-input');
  const theme = role === 'owner' ? document.documentElement.getAttribute('data-theme') : root.getAttribute('data-theme');
  if (!visible(body) || !visible(input) || typeof text !== 'string' || text.length > 12000
    || typeof bridgeText !== 'string' || bridgeText.length > 12000 || !['light', 'dark'].includes(theme)) return { ready: false };
  return { ready: true, conversationId: snapshot.conversationId, generation: value.generation,
    text, bridgeText, theme, draft: input.value };
}

export function assertHaruSessionPair(pair, expected) {
  const owner = pair?.owner, haru = pair?.haru;
  demand(owner?.ready === true && haru?.ready === true
    && owner.conversationId === haru.conversationId && owner.generation === haru.generation
    && Number.isSafeInteger(owner.conversationId) && owner.conversationId > 0
    && ['light', 'dark'].includes(owner.theme) && owner.theme === haru.theme, 'HARU_SYNC_FAILED');
  const text = normalize(owner.text);
  demand(text && [owner.bridgeText, haru.text, haru.bridgeText].every(value => normalize(value) === text), 'HARU_SYNC_FAILED');
  if (expected) demand(owner.conversationId === expected.conversationId && text === expected.text, 'HARU_SYNC_FAILED');
  return { conversationId: owner.conversationId, text, theme: owner.theme };
}

export async function readStoredHaruConversation(conversationId) {
  const response = await fetch('/api/chat/conversations');
  if (!response.ok) return null;
  const rows = await response.json();
  if (!Array.isArray(rows)) return null;
  const matches = rows.filter(row => row.id === conversationId);
  return matches.length === 1 && typeof matches[0].title === 'string' && matches[0].title.length <= 400
    ? { id: matches[0].id, title: matches[0].title, titleUnique: rows.filter(row => row.title === matches[0].title).length === 1 } : null;
}

export function createMockHaruSession({ mode, broker } = {}) {
  demand(mode === 'mock' && broker?.mode === 'MOCK', 'INVALID_HARNESS');
  let retained;
  const proof = {};
  const diagnostic = { stage: 'OPEN_PILOT', ...Object.fromEntries(HARU_SESSION_DIAGNOSTIC_FLAGS.map(key => [key, false])) };
  const mark = stage => { diagnostic.stage = stage; };
  const captured = new Set();
  const capture = async (args, id, surface) => {
    if (captured.has(id)) return;
    demand(typeof args.capture === 'function', 'INVALID_HARNESS');
    const result = await args.capture(id, surface, 'pilot-stream');
    demand(result?.status === 'captured', 'UNSAFE_SCREENSHOT');
    captured.add(id);
  };
  const within = (deadlineMs, maximum = 15000) => {
    demand(Number.isFinite(deadlineMs) && deadlineMs > Date.now(), 'SUITE_DEADLINE');
    return Math.min(maximum, deadlineMs - Date.now());
  };
  const until = async (predicate, deadlineMs) => {
    const stop = Date.now() + within(deadlineMs);
    do { if (await predicate()) return; await pause(100); } while (Date.now() < stop);
    reject();
  };
  const read = async ({ page, haru, native, deadlineMs }, expected, drafts, diagnose = false) => {
    await native.assertIdentity();
    for (const [surface, selector] of [[page, '[data-onboarding-target="pilot"] [class*="bubbleAssistant"]'],
      [haru, '.desktop-haru-messages article[data-role="assistant"] p']]) {
      await surface.locator(selector).last().scrollIntoViewIfNeeded({ timeout: within(deadlineMs) });
    }
    let pair;
    await until(async () => {
      pair = Object.fromEntries(await Promise.all([['owner', page], ['haru', haru]].map(async ([role, surface]) =>
        [role, await surface.evaluate(readHaruSessionRenderer, { role, origin: native.origin })])));
      if (diagnose) {
        diagnostic.ownerReady = pair.owner?.ready === true; diagnostic.haruReady = pair.haru?.ready === true;
        diagnostic.identityEqual = diagnostic.ownerReady && diagnostic.haruReady
          && pair.owner.conversationId === expected.conversationId && pair.haru.conversationId === expected.conversationId
          && pair.owner.generation === pair.haru.generation;
        diagnostic.bodyEqual = diagnostic.ownerReady && diagnostic.haruReady
          && [pair.owner.text, pair.owner.bridgeText, pair.haru.text, pair.haru.bridgeText].every(text => normalize(text) === expected.text);
        diagnostic.themeEqual = diagnostic.ownerReady && diagnostic.haruReady
          && pair.owner.theme === expected.theme && pair.haru.theme === expected.theme;
      }
      try { assertHaruSessionPair(pair, expected); return true; } catch { return false; }
    }, deadlineMs);
    const identity = assertHaruSessionPair(pair, expected);
    if (drafts) demand(pair.owner.draft === OWNER_DRAFT && pair.haru.draft === HARU_DRAFT, 'HARU_SYNC_FAILED');
    await native.assertIdentity();
    return identity;
  };
  return {
    diagnostic: () => ({ ...diagnostic }),
    async afterPilot(args) {
      demand(!retained && args.current?.id === 'pilot-stream', 'INVALID_HARNESS');
      const before = mockSessionLedger(broker);
      demand(broker.snapshot().sentRequests === 2, 'UNEXPECTED_PROVIDER_REQUESTS');
      retained = await read(args);
      proof.haruBrokerHistoryVisible = true;
      await capture(args, `haru-session-${retained.theme}`, args.haru);
      const { page, haru, native, deadlineMs } = args;
      await native.prepareTray();
      demand(await ownerInput(page).inputValue() === '' && await haru.locator('#desktop-haru-input').inputValue() === '', 'HARU_SYNC_FAILED');
      await ownerInput(page).fill(OWNER_DRAFT, { timeout: within(deadlineMs) });
      await haru.locator('#desktop-haru-input').fill(HARU_DRAFT, { timeout: within(deadlineMs) });
      const switchTheme = async () => {
        const next = retained.theme === 'light' ? 'dark' : 'light';
        await page.getByRole('button', exact('退出沉浸模式，返回原页面')).click({ timeout: within(deadlineMs) });
        await page.getByRole('navigation', exact('主导航')).getByRole('button', exact('今日')).click({ timeout: within(deadlineMs) });
        await page.getByRole('button', exact('切换明暗模式')).click({ timeout: within(deadlineMs) });
        await until(async () => await page.locator('html').getAttribute('data-theme') === next
          && await haru.locator('main.desktop-haru').getAttribute('data-theme') === next, deadlineMs);
        await haru.getByRole('button', exact('打开 OfferPilot 主窗口')).click({ timeout: within(deadlineMs) });
        await page.locator('[data-onboarding-target="pilot"]').waitFor({ timeout: within(deadlineMs) });
        retained.theme = (await read(args, retained, true)).theme;
        await capture(args, `haru-session-${retained.theme}`, haru);
      };
      for (let iteration = 0; iteration < 2; iteration++) {
        await haru.getByRole('button', exact('收起 Haru 对话')).click({ timeout: within(deadlineMs) });
        await haru.locator('.desktop-haru.collapsed .desktop-haru-portrait[data-runtime-state="ready"]').waitFor({ timeout: within(deadlineMs) });
        await haru.getByRole('button', exact('展开 Haru 对话')).click({ timeout: within(deadlineMs) });
        await read(args, retained, true);
        await switchTheme();
      }
      await switchTheme(); // Persist a changed preference, not the initial default.
      proof.haruIndependentDraftsRetained = true; proof.haruThemeMirrored = true;
      await native.hideOwner();
      await haru.getByRole('button', exact('打开 OfferPilot 主窗口')).click({ timeout: within(deadlineMs) });
      await native.waitVisible('owner');
      await read(args, retained, true);
      await haru.getByRole('button', exact('关闭 Haru 对话')).click({ timeout: within(deadlineMs) });
      await haru.locator('.desktop-haru.collapsed').waitFor({ timeout: within(deadlineMs) });
      await native.waitVisible('haru');
      await haru.getByRole('button', exact('展开 Haru 对话')).click({ timeout: within(deadlineMs) });
      await native.hideHaru();
      await native.waitHidden('haru');
      await native.restoreHaru();
      await read(args, retained, true);
      proof.haruNativeWindowRestore = true;
      demand(mockSessionLedger(broker) === before, 'UNEXPECTED_PROVIDER_REQUESTS');
      proof.haruSessionNoExtraRequests = true;
      // These two inputs were created by this probe, not user drafts. Restore
      // the clean UI before the next independently budgeted existing case.
      await ownerInput(page).fill('', { timeout: within(deadlineMs) });
      await haru.locator('#desktop-haru-input').fill('', { timeout: within(deadlineMs) });
      return { ...proof };
    },
    async afterRestart(args) {
      demand(retained && proof.haruSessionNoExtraRequests === true, 'INVALID_HARNESS');
      const before = mockSessionLedger(broker);
      const { page, haru, native, deadlineMs, restart } = args;
      demand(restart?.productionTrayExit === true && restart.processAndPortsReleased === true && restart.sameProfileAndExe === true,
        'INSTALLED_PROCESS_IDENTITY_MISMATCH');
      Object.assign(diagnostic, { productionTrayExit: true, processAndPortsReleased: true, sameProfileAndExe: true });
      mark('OPEN_PILOT');
      await haru.getByRole('button', exact('展开 Haru 对话')).click({ timeout: within(deadlineMs) });
      await haru.getByRole('button', exact('打开 OfferPilot 主窗口')).click({ timeout: within(deadlineMs) });
      await page.locator('[data-onboarding-target="pilot"]').waitFor({ timeout: within(deadlineMs) });
      // Read the persisted title by the original ID from the actual backend,
      // then select its unique real row. No synthetic response or state write.
      mark('STORED_CONVERSATION');
      const stored = await page.evaluate(readStoredHaruConversation, retained.conversationId);
      diagnostic.stored = stored?.id === retained.conversationId && typeof stored.title === 'string' && Boolean(stored.title.trim());
      diagnostic.storedTitleUnique = stored?.titleUnique === true;
      demand(diagnostic.stored && diagnostic.storedTitleUnique, 'HARU_SYNC_FAILED');
      mark('UNIQUE_ROW');
      const conversation = page.getByRole('button', exact(`打开对话：${stored.title}`));
      await until(async () => {
        const count = await conversation.count(); diagnostic.rowFound = count > 0; diagnostic.rowUnique = count === 1;
        return diagnostic.rowUnique;
      }, deadlineMs);
      mark('UNIQUE_TITLE');
      const title = conversation.getByText(stored.title, { exact: true });
      await until(async () => { diagnostic.titleUnique = await title.count() === 1; return diagnostic.titleUnique; }, deadlineMs);
      mark('HISTORY_GET');
      await Promise.all([page.waitForResponse(value => {
        const url = new URL(value.url());
        return url.origin === native.origin && url.pathname === `/api/chat/conversations/${retained.conversationId}`
          && value.request().method() === 'GET' && value.status() === 200;
      }, { timeout: within(deadlineMs) }), title.click({ timeout: within(deadlineMs) })]);
      diagnostic.historyGet = true;
      mark('BODY_PAIR');
      const restored = await read(args, retained, false, true);
      mark('THEME');
      demand(restored.theme === retained.theme, 'HARU_SYNC_FAILED');
      mark('SCREENSHOT');
      await capture(args, 'pilot-session-restored', page);
      demand(mockSessionLedger(broker) === before, 'UNEXPECTED_PROVIDER_REQUESTS');
      Object.assign(proof, { haruProductionTrayExit: true, haruProcessAndPortsReleased: true,
        haruSameProfileRestart: true, haruRestartedHistoryVisible: true, haruRestartedThemeRetained: true });
      mark('COMPLETE');
      return { ...proof };
    },
  };
}
