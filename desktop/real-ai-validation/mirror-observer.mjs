// Read-only, case-scoped observation of existing bridge state and rendered UI.
// No publication, controller calls, response interception, or content capture.
import { randomUUID } from 'node:crypto';

const CASE_IDS = new Set(['pilot-stream', 'pilot-cancel', 'pilot-hitl-reject']);
const TASK_STATES = new Set(['idle', 'running', 'waiting_confirmation', 'completed', 'failed', 'unavailable']);
const active = new WeakMap();
let nextSequence = 0;
const READ_TIMEOUT_MS = 5000;
const fail = () => { throw Object.assign(new Error('HARU_SYNC_FAILED'), { code: 'HARU_SYNC_FAILED' }); };
const validId = value => Number.isSafeInteger(value) && value > 0;

function absent(role) {
  return { role, caseId: null, installed: false, baselineReady: false, healthy: false,
    connected: false, currentTaskState: 'unavailable', loading: false, hasPending: false,
    bridgeRunningObserved: false, domRunningObserved: false, runningWithNullObserved: false,
    conversationId: null, runningConversationId: null, identityChanged: false,
    generationChanged: false, readTimedOut: false, expired: false };
}
function sanitize(value, role, caseId) {
  const result = absent(role);
  if (!value || value.role !== role || value.caseId !== caseId || !CASE_IDS.has(caseId)) return result;
  result.caseId = caseId;
  for (const key of Object.keys(result)) if (typeof result[key] === 'boolean') result[key] = value[key] === true;
  result.currentTaskState = TASK_STATES.has(value.currentTaskState) ? value.currentTaskState : 'unavailable';
  result.conversationId = validId(value.conversationId) ? value.conversationId : null;
  result.runningConversationId = validId(value.runningConversationId) ? value.runningConversationId : null;
  return result;
}
async function bounded(action) {
  let timer;
  try {
    return await Promise.race([Promise.resolve().then(action), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('HARU_SYNC_FAILED')), READ_TIMEOUT_MS);
    })]);
  } finally { clearTimeout(timer); }
}

// This function is serialized into each renderer. Only its bounded summary
// leaves that renderer; raw bridge state and DOM content are never retained.
async function rendererObservation({ operation, role, caseId, token, sequence, deadline }) {
  const cancelledKey = '__offerpilotBoundedMirrorCancelledThrough';
  const key = '__offerpilotBoundedMirrorObserver';
  const bad = () => { throw new Error('HARU_SYNC_FAILED'); };
  const allowedCases = ['pilot-stream', 'pilot-cancel', 'pilot-hitl-reject'];
  if (!allowedCases.includes(caseId) || !['owner', 'haru'].includes(role) || typeof token !== 'string'
    || !Number.isSafeInteger(sequence) || sequence < 1) bad();
  const existing = window[key];
  if (operation === 'remove') {
    // A scalar cancellation watermark also rejects an install RPC that reaches
    // this renderer after cleanup. It has no timer and retains no case content.
    window[cancelledKey] = Math.max(window[cancelledKey] ?? 0, sequence);
    if (existing?.token === token && existing.sequence === sequence) {
      const cleaned = existing.dispose(); delete window[key]; if (!cleaned) bad();
    }
    return true;
  }
  if (operation === 'read') {
    if (!existing || existing.token !== token || existing.sequence !== sequence || existing.caseId !== caseId || existing.role !== role) return null;
    await existing.refresh();
    return existing.summary();
  }
  if (operation !== 'install' || existing || sequence <= (window[cancelledKey] ?? 0) || Date.now() >= deadline) bad();
  const bridge = window.offerpilotDesktop;
  if (bridge?.role !== role || typeof bridge.getState !== 'function' || (role === 'haru' && typeof bridge.onState !== 'function')) bad();
  let disposed = false, faulted = false, baselineReady = false, cleanupFailed = false;
  let eventRevision = 0, baselineGeneration = null, positiveId = null, pendingRead;
  let pollTimer, expiryTimer, readTimer, baselineTimer, mutationObserver, off, releaseRead, releaseBaseline;
  let connected = false, currentTaskState = 'unavailable', loading = false, hasPending = false;
  let bridgeRunningObserved = false, domRunningObserved = false, runningWithNullObserved = false;
  let runningConversationId = null, conversationId = null, identityChanged = false;
  let generationChanged = false, readTimedOut = false, expired = false;
  const positive = value => Number.isSafeInteger(value) && value > 0;
  const visible = node => Boolean(node && node.getClientRects().length &&
    getComputedStyle(node).display !== 'none' && !['hidden', 'collapse'].includes(getComputedStyle(node).visibility));
  const domRunning = () => {
    if (document.visibilityState !== 'visible') return false;
    if (role === 'owner') {
      const nodes = [...document.querySelectorAll('[data-onboarding-target="pilot"] button[aria-label="停止当前回复"]')].filter(visible);
      return nodes.length === 1 && !nodes[0].disabled && nodes[0].getAttribute('aria-disabled') !== 'true';
    }
    const nodes = [...document.querySelectorAll('main[aria-label="Haru 桌面小窗"] .desktop-haru-avatar [role="status"]')].filter(visible);
    return nodes.length === 1 && nodes[0].textContent?.trim() === '正在处理';
  };
  const stopResources = () => {
    clearInterval(pollTimer); clearTimeout(expiryTimer); clearTimeout(readTimer); clearTimeout(baselineTimer);
    try { mutationObserver?.disconnect(); } catch { cleanupFailed = true; faulted = true; }
    try { off?.(); } catch { cleanupFailed = true; faulted = true; }
    off = undefined;
    releaseRead?.(); releaseRead = undefined;
    releaseBaseline?.(); releaseBaseline = undefined;
  };
  const dispose = () => { if (!disposed) { disposed = true; stopResources(); } return !cleanupFailed; };
  const fault = () => { faulted = true; stopResources(); };
  const sampleDom = () => {
    if (disposed || faulted || document.visibilityState !== 'visible'
      || !baselineReady || !connected || currentTaskState !== 'running'
      || !loading || hasPending || !positive(conversationId)) return;
    try { if (domRunning()) domRunningObserved = true; } catch { fault(); }
  };
  const accept = value => {
    if (disposed || faulted) return;
    // Whitelist scalar fields immediately; never store the bridge snapshot.
    const snapshot = value?.snapshot;
    const nextId = snapshot?.conversationId;
    const nextTask = snapshot?.taskState;
    if (value?.connected !== true || !Number.isSafeInteger(value.generation) || value.generation < 0
      || !snapshot || !['idle', 'running', 'waiting_confirmation', 'completed', 'failed'].includes(nextTask)
      || !(nextId === null || positive(nextId)) || typeof snapshot.loading !== 'boolean'
      || typeof snapshot.hasPending !== 'boolean') { fault(); return; }
    connected = true; currentTaskState = nextTask; loading = snapshot.loading; hasPending = snapshot.hasPending;
    conversationId = nextId;
    // This case requested plain text only. Any approval state makes later idle
    // insufficient proof that no write/approval occurred during the case.
    if (caseId === 'pilot-stream' && (nextTask === 'waiting_confirmation' || hasPending)) { fault(); return; }
    if (nextTask === 'failed' || (nextTask === 'running' && (!loading || hasPending))
      || (nextTask === 'idle' && (loading || hasPending))) { fault(); return; }
    if (!baselineReady) {
      if (nextTask === 'idle' && !loading && !hasPending && nextId === null) {
        // The previous case's rendered running UI must also have disappeared.
        try {
          if (document.visibilityState === 'visible' && !domRunning()) {
            baselineReady = true; baselineGeneration = value.generation;
          }
        } catch { fault(); }
      }
      return;
    }
    if (value.generation !== baselineGeneration) { generationChanged = true; fault(); return; }
    if (positiveId !== null && nextId !== positiveId) { identityChanged = true; fault(); return; }
    if (positive(nextId)) positiveId = nextId;
    if (nextTask === 'running' && loading && !hasPending) {
      bridgeRunningObserved = true;
      if (nextId === null) runningWithNullObserved = true;
      else runningConversationId = nextId;
    }
    sampleDom();
  };
  const summary = () => ({ role, caseId, installed: !disposed, baselineReady,
    healthy: baselineReady && !disposed && !faulted, connected, currentTaskState, loading, hasPending,
    bridgeRunningObserved, domRunningObserved, runningWithNullObserved, conversationId,
    runningConversationId, identityChanged, generationChanged, readTimedOut, expired });
  const refresh = () => {
    if (disposed || faulted) return Promise.resolve();
    if (pendingRead) return pendingRead;
    const revision = eventRevision;
    pendingRead = new Promise(resolve => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true; clearTimeout(readTimer); releaseRead = undefined; resolve();
      };
      releaseRead = finish;
      readTimer = setTimeout(() => { if (!disposed) { readTimedOut = true; fault(); } finish(); },
        Math.max(1, Math.min(5000, baselineReady ? 5000 : deadline - Date.now())));
      Promise.resolve().then(() => bridge.getState()).then(value => {
        if (!disposed && !faulted && !settled && revision === eventRevision) accept(value);
        finish();
      }, () => { if (!disposed && !settled) fault(); finish(); });
    }).finally(() => { pendingRead = undefined; });
    return pendingRead;
  };
  window[key] = { role, caseId, token, sequence, dispose, refresh, summary };
  try {
    if (role === 'haru') off = bridge.onState(value => { eventRevision += 1; accept(value); });
    if (role === 'haru' && typeof off !== 'function') bad();
    while (!baselineReady && !disposed && !faulted && Date.now() < deadline) {
      await refresh();
      if (!baselineReady && !disposed && !faulted && Date.now() < deadline) await new Promise(resolve => {
        releaseBaseline = resolve;
        baselineTimer = setTimeout(() => { releaseBaseline = undefined; resolve(); }, Math.min(50, Math.max(1, deadline - Date.now())));
      });
    }
    if (!baselineReady || disposed || faulted) bad();
    mutationObserver = new MutationObserver(() => { sampleDom(); void refresh(); });
    mutationObserver.observe(document.documentElement, { childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ['disabled', 'aria-disabled', 'style', 'class', 'hidden', 'aria-label'] });
    pollTimer = setInterval(() => { sampleDom(); void refresh(); }, 50);
    expiryTimer = setTimeout(() => { expired = true; fault(); }, 600000);
    return summary();
  } catch {
    dispose();
    if (window[key]?.token === token) delete window[key];
    bad();
  }
}

export async function installMirrorObservation(page, haru, caseId) {
  if (!page || !haru || page === haru || !CASE_IDS.has(caseId) || active.has(page) || active.has(haru)) fail();
  const sequence = ++nextSequence;
  if (!Number.isSafeInteger(sequence)) fail();
  const record = { page, haru, caseId, token: randomUUID(), sequence };
  active.set(page, record); active.set(haru, record);
  try {
    const values = await Promise.all([['owner', page], ['haru', haru]].map(async ([role, surface]) =>
      sanitize(await bounded(() => surface.evaluate(rendererObservation, { operation: 'install', role,
        caseId, token: record.token, sequence: record.sequence, deadline: Date.now() + READ_TIMEOUT_MS })), role, caseId)));
    if (values.some(value => !value.installed || !value.baselineReady || !value.healthy)) fail();
  } catch {
    await removeRecord(record).catch(() => undefined);
    fail();
  }
}
export async function readMirrorObservation(page, haru) {
  const record = active.get(page);
  if (!record || record !== active.get(haru) || record.page !== page || record.haru !== haru)
    return { owner: absent('owner'), haru: absent('haru') };
  const read = async (role, surface) => {
    try { return sanitize(await bounded(() => surface.evaluate(rendererObservation, { operation: 'read', role,
      caseId: record.caseId, token: record.token, sequence: record.sequence })), role, record.caseId); }
    catch { return { ...absent(role), readTimedOut: true }; }
  };
  const [owner, companion] = await Promise.all([read('owner', page), read('haru', haru)]);
  return { owner, haru: companion };
}
async function removeRecord(record) {
  // An older installation may reject after a newer case has already started.
  // Clean only the original record and token, never the current replacement.
  if (active.get(record.page) === record) active.delete(record.page);
  if (active.get(record.haru) === record) active.delete(record.haru);
  await Promise.all([['owner', record.page], ['haru', record.haru]].map(async ([role, surface]) => {
    const removed = await bounded(() => surface.evaluate(rendererObservation, { operation: 'remove', role,
      caseId: record.caseId, token: record.token, sequence: record.sequence }));
    if (removed !== true) fail();
  }));
}
export async function removeMirrorObservation(page, haru) {
  const records = [...new Set([active.get(page), active.get(haru)].filter(Boolean))];
  await Promise.all(records.map(removeRecord));
}
export function isRunningMirrorProven(pair) {
  const { owner, haru } = pair ?? {};
  if (!owner || !haru || owner.role !== 'owner' || haru.role !== 'haru'
    || !CASE_IDS.has(owner.caseId) || owner.caseId !== haru.caseId) return false;
  return [owner, haru].every(value => value.installed === true && value.baselineReady === true && value.healthy === true
    && value.connected === true && TASK_STATES.has(value.currentTaskState)
    && !['failed', 'unavailable'].includes(value.currentTaskState)
    && value.bridgeRunningObserved === true && value.domRunningObserved === true
    && value.identityChanged === false && value.generationChanged === false && value.readTimedOut === false
    && value.expired === false && validId(value.runningConversationId)
    && value.runningConversationId === value.conversationId)
    && owner.runningConversationId === haru.runningConversationId;
}
