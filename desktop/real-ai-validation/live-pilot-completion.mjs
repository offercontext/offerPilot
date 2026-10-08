// LIVE pilot-stream only. Observe the user's one UI submission and bounded,
// same-origin readbacks. No extra POST, credentials, provider calls or retries.
import { POLICY, CASES, Ledger, usageCost } from './broker-core.cjs';
import { readMirrorObservation, isRunningMirrorProven } from './mirror-observer.mjs';

const CASE = 'pilot-stream';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const positive = n => Number.isSafeInteger(n) && n > 0;
const integer = n => Number.isSafeInteger(n) && n >= 0;
const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const IDS = ['request_id', 'turn_id', 'conversation_id', 'execution_generation'];
const DENIED = Object.keys(new Ledger().snapshot().denied).sort();
const ROW = ['caseId', 'cap', 'envelope', 'status', 'micro', 'outboundStarted', 'upstreamResponded',
  'clientDisconnectObserved', 'promptTokens', 'completionTokens', 'cacheHitTokens', 'cacheMissTokens'].sort();
const sameKeys = (v, keys) => object(v) && Object.keys(v).sort().join('|') === keys.join('|');
const fail = () => { throw Object.assign(new Error('LIVE_COMPLETION_UNPROVEN'), { code: 'LIVE_COMPLETION_UNPROVEN' }); };
const requireProof = v => { if (!v) fail(); };
export const normalizePlainText = text => text.replace(/[\t\n\v\f\r ]+/gu, ' ').replace(/^ | $/gu, '');
// Exact plain-message branch of PIN assistantPresentation.compactMessageText.
// The owner bridge already carries this compact projection, not the full reply.
export function compactPlainMessage(content) {
  const text = content.trim(), characters = Array.from(text);
  return characters.length > 520 ? `${characters.slice(0, 520).join('')}…` : text;
}
function identity(value) {
  requireProof(object(value) && UUID.test(value.request_id) && typeof value.turn_id === 'string'
    && /^[A-Za-z0-9_-]{1,128}$/.test(value.turn_id) && positive(value.conversation_id) && positive(value.execution_generation));
  return Object.fromEntries(IDS.map(key => [key, value[key]]));
}
function matches(value, admitted, optionalRequest = false) {
  return object(value) && IDS.every(key => optionalRequest && key === 'request_id' && !(key in value)
    || value[key] === admitted[key]);
}
function unsafe(value) {
  if (!object(value) && !Array.isArray(value)) return false;
  return Object.entries(value).some(([key, child]) => ['operation_id', 'undo', 'write_status', 'write_error', 'pending_action', 'confirmation_token'].includes(key)
    || (['degraded', 'replayed'].includes(key) && child !== false) || (key === 'type' && ['turn_recovered', 'confirmation_required'].includes(child))
    || unsafe(child));
}
export function projectAdmission(value, status) {
  requireProof(status === 202 && object(value) && value.protocol_version === 'pilot-runtime-v1' && value.replayed === false && !unsafe(value));
  const admitted = identity(value);
  requireProof(matches(value.execution, admitted) && value.execution.protocol_version === 'pilot-runtime-v1'
    && ['queued', 'running', 'completed'].includes(value.state) && value.execution.state === value.state);
  return Object.freeze(admitted);
}
export function projectTerminal(value, admitted) {
  requireProof(matches(value, admitted) && matches(value.execution, admitted)
    && value.protocol_version === 'pilot-runtime-v1' && value.execution.protocol_version === 'pilot-runtime-v1' && !unsafe(value)
    && value.recovery?.requires_resync === false && value.recovery?.auto_resume === false);
  if (['queued', 'running'].includes(value.state) && value.execution.state === value.state
    && !value.terminal?.response) return null;
  // Logical completion can precede the manager's finally block. Wait for both
  // envelopes to report the actual worker exit; never infer it from prose.
  if (value.state === 'completed' && value.execution.state === 'completed'
    && [value, value.execution].every(v => v.worker_done === false && v.actual_worker_alive === true)) return null;
  requireProof(value.state === 'completed' && value.execution.state === 'completed'
    && [value, value.execution].every(v => v.worker_done === true && v.actual_worker_alive === false));
  const response = value.terminal?.response;
  // PIN guarantees three terminal IDs; request_id is mandatory in both outer
  // envelopes and, if present in terminal.response, must also agree.
  requireProof(matches(response, admitted, true) && response.type === 'message'
    && typeof response.message === 'string' && response.message.length <= 12000 && response.message.trim().length > 0);
  return response.message;
}

// Exact allowlist, fetched by the owner's existing application session. No
// authentication inspection and no expansion of syntheticApi's write routes.
export async function readLivePilotJson({ origin, requestId, deadline }) {
  if (window.offerpilotDesktop?.role !== 'owner' || location.origin !== origin || Date.now() >= deadline) return null;
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  if (requestId !== null && !uuid.test(requestId)) return null;
  const route = requestId === null ? '/api/chat/conversations?include_archived=true' : `/api/pilot/runtime/v1/requests/${requestId}`;
  const limit = requestId === null ? 8192 : 131072;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(1, Math.min(5000, deadline - Date.now())));
  let reader;
  try {
    const response = await fetch(route, { method: 'GET', credentials: 'same-origin', redirect: 'error', cache: 'no-store', signal: controller.signal });
    if (response.status !== 200 || response.redirected || response.url !== `${origin}${route}` || !response.body) return null;
    reader = response.body.getReader(); let size = 0, text = '';
    const decoder = new TextDecoder('utf-8', { fatal: true });
    while (true) {
      const part = await reader.read(); if (Date.now() >= deadline) return null;
      if (part.done) break;
      size += part.value.byteLength; if (size > limit) return null;
      text += decoder.decode(part.value, { stream: true });
    }
    text += decoder.decode();
    return JSON.parse(text);
  } catch { return null; }
  finally { clearTimeout(timer); controller.abort(); try { await reader?.cancel(); } catch { /* No raw errors. */ } }
}

export function observeLiveAdmission(page, origin, within) {
  let closed = false, count = 0, admitted = null, faulted = false;
  const invalidate = () => {
    faulted = true; closed = true; admitted = null;
    try { page.off('response', listener); } catch { /* dispose retries cleanup and still cannot pass. */ }
  };
  const listener = response => {
    if (closed) return;
    let request;
    try {
      request = response.request();
      if (response.url() !== `${origin}/api/pilot/runtime/v1/turns` || request.method() !== 'POST'
        || request.frame() !== page.mainFrame()) return;
      if (++count !== 1 || request.redirectedFrom() !== null) { invalidate(); return; }
    } catch { invalidate(); return; }
    // No request headers or postData are read. Project the bounded admission
    // response immediately; a late callback can never revive a disposed proof.
    void within(async () => {
      const bytes = await response.body();
      requireProof(bytes.byteLength <= 65536);
      return projectAdmission(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)), response.status());
    }).then(value => { if (!closed && !faulted && count === 1) admitted = value; }, () => { if (!closed) invalidate(); });
  };
  page.on('response', listener);
  return { read() { requireProof(!closed && !faulted && count <= 1); return admitted; },
    dispose() { closed = true; admitted = null; page.off('response', listener); } };
}

function healthyMirror(pair) {
  return pair?.owner?.role === 'owner' && pair?.haru?.role === 'haru'
    && [pair.owner, pair.haru].every(v => v.caseId === CASE && v.installed === true && v.baselineReady === true
      && v.baselineEmpty === true && v.baselineControlsValid === true && v.healthy === true && v.connected === true && v.historyClean === true
      && v.identityChanged === false && v.generationChanged === false && v.expired === false && v.readTimedOut === false
      && integer(v.generation) && v.generation === v.baselineGeneration)
    && pair.owner.generation === pair.haru.generation;
}
export function isLiveTerminalMirrorProven(pair, admitted) {
  return healthyMirror(pair) && positive(admitted?.conversation_id) && [pair.owner, pair.haru].every(v =>
    v.conversationId === admitted.conversation_id && v.currentTaskState === 'idle' && v.loading === false && v.hasPending === false);
}

export function liveLedgerFingerprint(value) {
  const reviewedShape = sameKeys(value.denied, DENIED) && Array.isArray(value.requests)
    && value.requests.every(row => sameKeys(row, ROW));
  return JSON.stringify([reviewedShape, value.model, value.budgetMicroCny, value.reserveMicroCny, value.sentRequests,
    value.settledMicroCny, value.retainedMicroCny, value.active, value.closed, value.journalFailed,
    DENIED.map(key => value.denied?.[key]), value.requests?.map(row => ROW.map(key => row[key]))]);
}
export function isLiveLedgerProven(value, before, previousCount) {
  if (value?.mode === 'MOCK' || value?.mock !== undefined || !integer(previousCount)
    || before?.sentRequests !== previousCount || before.requests?.length !== previousCount
    || before.requests.some(row => row.caseId === CASE) || value?.model !== POLICY.model
    || value.active !== false || value.closed !== false || value.journalFailed !== false
    || value.budgetMicroCny !== POLICY.budgetMicroCny || value.reserveMicroCny !== POLICY.reserveMicroCny
    || !Array.isArray(value.requests) || value.sentRequests !== previousCount + 1 || value.requests.length !== value.sentRequests
    || value.sentRequests > POLICY.maxRequests || !sameKeys(value.denied, DENIED) || !sameKeys(before.denied, DENIED)
    || DENIED.some(key => !integer(value.denied[key]) || value.denied[key] !== 0 || before.denied[key] !== 0)
    || value.retainedMicroCny !== 0 || value.requests.filter(row => row.caseId === CASE).length !== 1) return false;
  let total = 0;
  for (const [index, row] of value.requests.entries()) {
    if (!sameKeys(row, ROW) || !Object.hasOwn(CASES, row.caseId) || row.status !== 'SETTLED'
      || row.outboundStarted !== true || row.upstreamResponded !== true || row.clientDisconnectObserved !== false
      || !['INSERTED', 'TIGHTENED', 'UNCHANGED'].includes(row.envelope) || !positive(row.cap)
      || row.cap > CASES[row.caseId].maxTokens || !integer(row.micro)
      || (index < previousCount && JSON.stringify(ROW.map(key => row[key])) !== JSON.stringify(ROW.map(key => before.requests[index][key])))) return false;
    try {
      const cost = usageCost({ prompt_tokens: row.promptTokens, completion_tokens: row.completionTokens,
        prompt_cache_hit_tokens: row.cacheHitTokens, prompt_cache_miss_tokens: row.cacheMissTokens,
        total_tokens: row.promptTokens + row.completionTokens }, row.cap);
      if (cost.micro !== row.micro) return false;
    } catch { return false; }
    total += row.micro;
  }
  return value.requests.at(-1).caseId === CASE && integer(total) && value.settledMicroCny === total
    && total + value.reserveMicroCny <= value.budgetMicroCny;
}

// Raw text and IDs exist only for this short in-memory comparison. The proof
// returned by the orchestrator contains booleans and fixed result codes only.
export async function readLiveTerminalRenderer({ role, deadline }) {
  if (Date.now() >= deadline) return null;
  const bridge = window.offerpilotDesktop;
  if (bridge?.role !== role || typeof bridge.getState !== 'function') return null;
  const state = await bridge.getState(), s = state?.snapshot;
  if (Date.now() >= deadline || state?.connected !== true || !Number.isSafeInteger(state.generation)
    || state.generation < 0 || !s || !Number.isSafeInteger(s.conversationId) || s.conversationId <= 0
    || s.taskState !== 'idle' || s.loading !== false || s.hasPending !== false || s.stopping !== false
    || s.canStop !== false || s.canSend !== true || s.error !== '' || s.stopMessage !== '' || !Array.isArray(s.messages)) return null;
  const visible = node => {
    if (document.visibilityState !== 'visible' || !node || !node.isConnected
      || ![...node.getClientRects()].some(rect => rect.width > 0 && rect.height > 0)) return false;
    for (let p = node; p; p = p.parentElement) {
      const style = getComputedStyle(p);
      if (p.hidden || style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility)
        || style.contentVisibility === 'hidden' || Number(style.opacity) === 0) return false;
    }
    return true;
  };
  const roots = [...document.querySelectorAll(role === 'owner' ? '[data-onboarding-target="pilot"]' : 'main[aria-label="Haru 桌面小窗"]')];
  if (roots.length !== 1 || !visible(roots[0])) return null;
  const root = roots[0];
  if (root.querySelectorAll('article[aria-label^="本轮任务："], [role="group"][aria-label="AI 修改提议"]').length) return null;
  const nodes = [...root.querySelectorAll(role === 'owner' ? '[class*="bubbleAssistant"]' : '.desktop-haru-messages article[data-role="assistant"] p')];
  // A new single plain-text turn must have exactly one assistant, including
  // hidden nodes. Never choose an old, hidden, duplicate, or structured reply.
  const messages = s.messages.filter(v => v?.role === 'assistant');
  if (nodes.length !== 1 || messages.length !== 1 || !visible(nodes[0])) return null;
  const content = nodes[0].innerText, bridgeContent = messages[0].content;
  if (typeof content !== 'string' || !content.trim() || content.length > 12000
    || typeof bridgeContent !== 'string' || !bridgeContent.trim() || bridgeContent.length > 12000) return null;
  return { content, bridgeContent, conversationId: s.conversationId, generation: state.generation };
}

export async function proveLivePilotCompletion({ mode, page, haru, broker, requestCountBefore, send, deadlineMs, ctx }) {
  requireProof(mode === 'live' && broker?.mode !== 'MOCK' && page !== haru && typeof send === 'function' && Number.isFinite(deadlineMs));
  let alive = true, admission;
  const within = async action => {
    const left = Math.min(5000, deadlineMs - Date.now()); requireProof(alive && left > 0);
    let timer;
    try {
      const value = await Promise.race([Promise.resolve().then(action), new Promise((_, reject) => {
        timer = setTimeout(() => reject(Object.assign(new Error('LIVE_COMPLETION_UNPROVEN'), { code: 'LIVE_COMPLETION_UNPROVEN' })), left);
      })]);
      requireProof(alive && Date.now() < deadlineMs); return value;
    } finally { clearTimeout(timer); }
  };
  try {
    ctx.mark('LIVE_FRESH_BASELINE');
    const origin = new URL(page.url()).origin;
    requireProof(/^http:\/\/127\.0\.0\.1:\d+$/.test(origin));
    const before = structuredClone(broker.snapshot());
    requireProof(before.mode !== 'MOCK' && before.mock === undefined && before.sentRequests === requestCountBefore
      && before.requests?.length === requestCountBefore && before.active === false && before.closed === false && before.journalFailed === false);
    const baseline = await within(() => readMirrorObservation(page, haru));
    requireProof(healthyMirror(baseline) && [baseline.owner, baseline.haru].every(v => v.conversationId === null
      && v.currentTaskState === 'idle' && v.loading === false && v.hasPending === false));
    const history = await within(() => page.evaluate(readLivePilotJson, { origin, requestId: null, deadline: deadlineMs }));
    requireProof(Array.isArray(history) && history.length === 0);
    admission = observeLiveAdmission(page, origin, within);
    ctx.mark('LIVE_SEND_OBSERVE');
    await within(send);
    ctx.mark('LIVE_TERMINAL_OBSERVE');
    while (Date.now() < deadlineMs) {
      const admitted = admission.read();
      const [pair, stream, message] = await within(() => Promise.all([
        readMirrorObservation(page, haru),
        page.evaluate(() => window.__offerpilotBoundedUiObserver?.summary?.() ?? null),
        admitted ? page.evaluate(readLivePilotJson, { origin, requestId: admitted.request_id, deadline: deadlineMs }) : null,
      ]));
      requireProof(healthyMirror(pair));
      admission.read();
      const content = message === null ? null : projectTerminal(message, admitted);
      if (content !== null && isLiveTerminalMirrorProven(pair, admitted)) {
        const ledger = broker.snapshot();
        if (!isLiveLedgerProven(ledger, before, requestCountBefore)) {
          // Wait only for the one already admitted provider call to settle.
          requireProof(ledger.active === true && ledger.requests?.filter(row => row.caseId === CASE).length === 1
            && ledger.requests.at(-1).status === 'RESERVED');
        } else {
          ctx.mark('LIVE_TERMINAL_COMPARE');
          const fingerprint = liveLedgerFingerprint(ledger);
          const [owner, companion] = await within(() => Promise.all([['owner', page], ['haru', haru]].map(([role, surface]) =>
            surface.evaluate(readLiveTerminalRenderer, { role, deadline: deadlineMs }))));
          const compact = compactPlainMessage(content);
          requireProof([owner, companion].every(v => v && v.conversationId === admitted.conversation_id && v.generation === baseline.owner.generation)
            && normalizePlainText(owner.content) === normalizePlainText(content)
            && companion.content === compact && owner.bridgeContent === compact && companion.bridgeContent === compact);
          const after = await within(() => readMirrorObservation(page, haru));
          admission.read();
          const afterLedger = broker.snapshot();
          requireProof(isLiveTerminalMirrorProven(after, admitted) && JSON.stringify(pair) === JSON.stringify(after)
            && isLiveLedgerProven(afterLedger, before, requestCountBefore) && liveLedgerFingerprint(afterLedger) === fingerprint);
          const sustained = isRunningMirrorProven(after) && stream?.installed === true && stream.originalTargetConnected === true
            && stream.originalTargetCurrent === true && stream.currentPilotUnique === true && stream.targetReplacementObserved === false
            && stream.readFailed === false && stream.originalGrowthWithStopCount >= 2;
          const running = v => v.bridgeRunningObserved === true && v.domRunningObserved === true && v.runningConversationId === admitted.conversation_id;
          return { code: sustained ? 'LIVE_STREAMING_PROVEN' : 'LIVE_COMPLETION_ONLY', checks: {
            sustainedStreamingObserved: sustained, incrementalAssistantRendering: sustained,
            ownerRunningTransitionObserved: running(after.owner), haruRunningTransitionObserved: running(after.haru),
            positiveRunningConversationMatched: isRunningMirrorProven(after), finalRunningConversationMatched: isRunningMirrorProven(after),
            haruRunningAndIdleMirrored: isRunningMirrorProven(after), haruVisibleAssistantMatchesSnapshot: true,
            freshConversationProven: true, uiAdmissionProven: true, naturalCompletionProven: true,
            terminalIdentityProven: true, terminalMirrorProven: true, terminalTextProven: true,
            providerRequestSettled: true, ledgerUnchanged: true, admissionObserverCleanupPassed: true } };
        }
      }
      await within(() => new Promise(resolve => setTimeout(resolve, 50)));
    }
    fail();
  } catch { fail(); }
  finally { alive = false; try { admission?.dispose(); } catch { fail(); } }
}
