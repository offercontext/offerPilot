// Explicitly injected by the offline entrypoint only. This gate never starts,
// arms, cancels, retries, or changes a provider/UI operation. A proof permits
// the caller to consider later independent cases; the failed case stays FAIL.
import { POLICY, CASES, Ledger, usageCost } from './broker-core.cjs';
import { readMirrorObservation, isRunningMirrorProven } from './mirror-observer.mjs';

export { CONTINUATION_CODES } from './ui-diagnostics.mjs';
const CASE_ID = 'pilot-stream';
const PHASE_MS = 15_000;
const DENIED_KEYS = Object.keys(new Ledger().snapshot().denied).sort();
const ROW_KEYS = ['caseId', 'cap', 'envelope', 'status', 'micro', 'outboundStarted', 'upstreamResponded',
  'clientDisconnectObserved', 'promptTokens', 'completionTokens', 'cacheHitTokens', 'cacheMissTokens'].sort();
const positive = value => Number.isSafeInteger(value) && value > 0;
const integer = value => Number.isSafeInteger(value) && value >= 0;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const normalize = value => value.replace(/[\t\n\v\f\r ]+/gu, ' ').replace(/^ | $/gu, '');
const sameKeys = (value, keys) => object(value) && Object.keys(value).sort().join('|') === keys.join('|');

function mockBoundary(broker, value) {
  return broker.mode === 'MOCK' && value?.mode === 'MOCK' && value.mock?.transport === 'IN_PROCESS_SYNTHETIC_HTTPS'
    && value.mock.externalNetwork === 'DENIED' && value.mock.realProviderCalls === 0
    && value.mock.usageIsSynthetic === true && value.mock.fakeRequests === value.sentRequests;
}
function safeLedger(value, previousCount) {
  if (previousCount !== 1 || value?.model !== POLICY.model || value.active !== false || value.closed !== false
    || value.journalFailed !== false || value.budgetMicroCny !== POLICY.budgetMicroCny
    || value.reserveMicroCny !== POLICY.reserveMicroCny || !Array.isArray(value.requests)
    || value.sentRequests !== previousCount + 1 || value.requests.length !== value.sentRequests
    || value.sentRequests > POLICY.maxRequests || !sameKeys(value.denied, DENIED_KEYS)
    || DENIED_KEYS.some(key => value.denied[key] !== 0) || value.retainedMicroCny !== 0) return false;
  let total = 0;
  for (const [index, row] of value.requests.entries()) {
    if (!sameKeys(row, ROW_KEYS) || row.caseId !== ['connection', CASE_ID][index]
      || row.status !== 'SETTLED' || row.outboundStarted !== true || row.upstreamResponded !== true
      || row.clientDisconnectObserved !== false || !['INSERTED', 'TIGHTENED', 'UNCHANGED'].includes(row.envelope)
      || !positive(row.cap) || row.cap > CASES[row.caseId].maxTokens || !integer(row.micro)) return false;
    try {
      const cost = usageCost({ prompt_tokens: row.promptTokens, completion_tokens: row.completionTokens,
        prompt_cache_hit_tokens: row.cacheHitTokens, prompt_cache_miss_tokens: row.cacheMissTokens,
        total_tokens: row.promptTokens + row.completionTokens }, row.cap);
      if (cost.micro !== row.micro) return false;
    } catch { return false; }
    total += row.micro;
  }
  return integer(total) && value.settledMicroCny === total
    && total + value.retainedMicroCny + value.reserveMicroCny <= value.budgetMicroCny;
}
// Keep only reviewed scalar ledger fields in memory for the before/after check.
function ledgerFingerprint(value) {
  return JSON.stringify([value.sentRequests, value.settledMicroCny, value.retainedMicroCny,
    value.active, value.closed, value.journalFailed, value.mock.fakeRequests,
    DENIED_KEYS.map(key => value.denied[key]), value.requests.map(row => ROW_KEYS.map(key => row[key]))]);
}
function finalMirror(pair) {
  return isRunningMirrorProven(pair) && [pair.owner, pair.haru].every(value => value.caseId === CASE_ID
    && value.currentTaskState === 'idle' && value.loading === false && value.hasPending === false);
}

// Serialized into the actual renderer. Content and current identity leave it
// only for in-memory comparison; they are never included in a returned proof.
async function readTerminalRenderer({ role, deadline }) {
  if (Date.now() >= deadline) return { state: 'DEADLINE' };
  const bridge = window.offerpilotDesktop;
  if (bridge?.role !== role || typeof bridge.getState !== 'function') return { state: 'INVALID' };
  const value = await bridge.getState();
  if (Date.now() >= deadline) return { state: 'DEADLINE' };
  const snapshot = value?.snapshot;
  if (value?.connected !== true || !Number.isSafeInteger(value.generation) || value.generation < 0
    || !snapshot || !Number.isSafeInteger(snapshot.conversationId) || snapshot.conversationId <= 0
    || snapshot.taskState !== 'idle' || snapshot.loading !== false || snapshot.hasPending !== false
    || snapshot.stopping !== false || snapshot.canStop !== false || snapshot.canSend !== true
    || snapshot.error !== '' || snapshot.stopMessage !== '' || !Array.isArray(snapshot.messages)) return { state: 'INVALID' };
  const visible = node => {
    if (document.visibilityState !== 'visible' || !node || node.isConnected !== true
      || ![...node.getClientRects()].some(rect => rect.width > 0 && rect.height > 0)) return false;
    for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      if (ancestor.hidden || style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility)
        || style.contentVisibility === 'hidden' || Number(style.opacity) === 0) return false;
    }
    return true;
  };
  const roots = [...document.querySelectorAll(role === 'owner'
    ? '[data-onboarding-target="pilot"]' : 'main[aria-label="Haru 桌面小窗"]')];
  if (roots.length !== 1 || !visible(roots[0])) return { state: 'INVALID' };
  const root = roots[0];
  // Structured/task cards are outside this narrowly reviewed plain-text case.
  if (role === 'owner' && root.querySelectorAll('article[aria-label^="本轮任务："]').length) return { state: 'INVALID' };
  const nodes = [...root.querySelectorAll(role === 'owner' ? '[class*="bubbleAssistant"]'
    : '.desktop-haru-messages article[data-role="assistant"] p')];
  const latest = nodes.at(-1);
  if (!visible(latest) || typeof latest.textContent !== 'string' || latest.textContent.length > 12000)
    return { state: 'INVALID' };
  const messages = snapshot.messages.filter(message => message?.role === 'assistant');
  const content = messages.at(-1)?.content;
  if (typeof content !== 'string' || content.length > 12000) return { state: 'INVALID' };
  return { state: 'READY', content: latest.textContent, bridgeContent: content,
    conversationId: snapshot.conversationId, generation: value.generation };
}

export async function proveMockTerminalContinuation({ page, haru, broker, result, previousCount, deadlineMs } = {}) {
  const proof = { status: 'BLOCKED', code: 'NOT_ELIGIBLE', eligible: false, ledgerSafe: false,
    mirrorProven: false, domEqual: false, noNewRequests: false };
  const blocked = code => Object.freeze({ ...proof, code });
  // Failure text never creates this capability. The offline caller explicitly
  // injects the function, and both eligibility and offline broker are rechecked.
  if (result?.id !== CASE_ID || result.status !== 'FAIL' || result.code !== 'STREAM_NOT_OBSERVED'
    || result.diagnostic?.stage !== 'PILOT_STREAM_READBACK') return blocked('NOT_ELIGIBLE');
  proof.eligible = true;
  if (!page || !haru || page === haru || typeof page.evaluate !== 'function' || typeof haru.evaluate !== 'function'
    || typeof broker?.snapshot !== 'function' || !Number.isFinite(deadlineMs)) return blocked('READ_FAILED');
  const deadline = Math.min(deadlineMs, Date.now() + PHASE_MS);
  const within = async action => {
    const left = deadline - Date.now();
    if (left <= 0) throw Object.assign(new Error('DEADLINE'), { gateDeadline: true });
    let timer;
    try {
      const value = await Promise.race([Promise.resolve().then(action), new Promise((_, reject) => {
        timer = setTimeout(() => reject(Object.assign(new Error('DEADLINE'), { gateDeadline: true })), left);
      })]);
      if (Date.now() >= deadline) throw Object.assign(new Error('DEADLINE'), { gateDeadline: true });
      return value;
    } finally { clearTimeout(timer); }
  };
  try {
    if (Date.now() >= deadline) return blocked('DEADLINE');
    const before = broker.snapshot();
    if (!mockBoundary(broker, before)) return blocked('MOCK_BOUNDARY_REQUIRED');
    if (!safeLedger(before, previousCount)) return blocked('LEDGER_UNSAFE');
    const fingerprint = ledgerFingerprint(before);
    proof.ledgerSafe = true;
    const first = await within(() => readMirrorObservation(page, haru));
    if ([first.owner, first.haru].some(value => value?.readTimedOut)) return blocked('READ_FAILED');
    if (!finalMirror(first)) return blocked('MIRROR_UNPROVEN');
    const id = first.owner.conversationId;
    proof.mirrorProven = true;
    const [owner, companion] = await within(() => Promise.all([['owner', page], ['haru', haru]].map(([role, surface]) =>
      surface.evaluate(readTerminalRenderer, { role, deadline }))));
    if ([owner, companion].some(value => value?.state === 'DEADLINE')) return blocked('DEADLINE');
    if (![owner, companion].every(value => value?.state === 'READY' && positive(value.conversationId)
      && value.conversationId === id && integer(value.generation)) || owner.generation !== companion.generation)
      return blocked('TERMINAL_UNPROVEN');
    const content = normalize(owner.content);
    if (!content || ![companion.content, owner.bridgeContent, companion.bridgeContent]
      .every(value => typeof value === 'string' && normalize(value) === content)) return blocked('DOM_MISMATCH');
    proof.domEqual = true;
    // Fresh observer reads guard identity/generation transitions while reading
    // the real DOM. The original null baseline and running proof stay required.
    const after = await within(() => readMirrorObservation(page, haru));
    if ([after.owner, after.haru].some(value => value?.readTimedOut)) return blocked('READ_FAILED');
    if (!finalMirror(after) || after.owner.conversationId !== id) return blocked('MIRROR_UNPROVEN');
    const ledger = broker.snapshot();
    if (!mockBoundary(broker, ledger) || !safeLedger(ledger, previousCount)
      || ledgerFingerprint(ledger) !== fingerprint) return blocked('LEDGER_CHANGED');
    if (Date.now() >= deadline) return blocked('DEADLINE');
    proof.noNewRequests = true;
    return Object.freeze({ ...proof, status: 'PROVEN', code: 'MOCK_CONTINUATION_PROVEN' });
  } catch (error) { return blocked(error?.gateDeadline === true ? 'DEADLINE' : 'READ_FAILED'); }
}
