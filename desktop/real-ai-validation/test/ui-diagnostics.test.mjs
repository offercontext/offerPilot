import test from 'node:test';
import assert from 'node:assert/strict';
import { createUiDiagnostic, sanitizeUiDiagnostic, sanitizePilotProofDiagnostic, sanitizeRendererDiagnostic } from '../ui-diagnostics.mjs';
import { safeResults } from '../safe-evidence.mjs';
import { CASES } from '../contract.mjs';

test('fixed UI stage captures only target existence uniqueness and visibility', async () => {
  const diagnostic = createUiDiagnostic();
  diagnostic.mark('PROVIDER_ENDPOINT', { count: async () => 0, isVisible: async () => { throw new Error('must not read absent target'); } });
  assert.deepEqual(await diagnostic.snapshot(), { stage: 'PROVIDER_ENDPOINT', targetProbed: true,
    targetFound: false, targetUnique: false, targetVisible: false });
  diagnostic.mark('PROVIDER_CONTEXT', { count: async () => 1, isVisible: async () => true });
  assert.deepEqual(await diagnostic.snapshot(), { stage: 'PROVIDER_CONTEXT', targetProbed: true,
    targetFound: true, targetUnique: true, targetVisible: true });
});
test('untrusted stage and locator errors never disclose text or obscure primary failure', async () => {
  const diagnostic = createUiDiagnostic();
  diagnostic.mark('private-secret-stage', { count: async () => { throw new Error('private-key'); } });
  const result = await diagnostic.snapshot();
  assert.equal(result.stage, 'UNSPECIFIED'); assert.equal(result.targetProbed, false);
  assert.doesNotMatch(JSON.stringify(result), /private/);
  assert.deepEqual(sanitizeUiDiagnostic({ stage: 'PROVIDER_KEY', raw: 'private-secret', targetVisible: 'yes' }),
    { stage: 'PROVIDER_KEY', targetProbed: false, targetFound: false, targetUnique: false, targetVisible: false });
});
test('ambiguous target is never treated as actionable and evidence drops extra data', async () => {
  const diagnostic = createUiDiagnostic();
  diagnostic.mark('CONNECTION_CLICK', { count: async () => 2, isVisible: async () => { throw new Error('must not inspect ambiguous target'); } });
  const d = await diagnostic.snapshot(); assert.equal(d.targetUnique, false); assert.equal(d.targetVisible, false);
  const result = safeResults(CASES.map(id => ({ id, status: 'FAIL', code: 'UI_TIMEOUT', checks: {},
    diagnostic: { ...d, rawError: 'private-key', inputValue: 'private-key' } })));
  assert.equal(result[0].diagnostic.stage, 'CONNECTION_CLICK');
  assert.doesNotMatch(JSON.stringify(result), /private-key|inputValue|rawError/);
});

test('stalled diagnostic locator returns after fixed short bound without replacing the stage', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const diagnostic = createUiDiagnostic();
  diagnostic.mark('PROVIDER_JSON_SCHEMA', { count: () => new Promise(() => {}), isVisible: async () => true });
  const pending = diagnostic.snapshot();
  t.mock.timers.tick(5000);
  assert.deepEqual(await pending, { stage: 'PROVIDER_JSON_SCHEMA', targetProbed: false,
    targetFound: false, targetUnique: false, targetVisible: false });
});

test('mirror diagnosis contains only fixed booleans, never identifiers or prose', async () => {
  const diagnostic = createUiDiagnostic();
  diagnostic.mark('PILOT_RUNNING');
  diagnostic.mirror({ ownerConnected: true, ownerRunningPositiveSeen: true, ownerRunningNullSeen: false,
    haruRunningDomSeen: 'true', conversationId: 123, rawMessage: 'private response', token: 'private-key' });
  const value = await diagnostic.snapshot();
  assert.equal(value.mirror.ownerConnected, true);
  assert.equal(value.mirror.ownerRunningPositiveSeen, true);
  assert.equal(value.mirror.haruRunningDomSeen, false);
  assert.doesNotMatch(JSON.stringify(value), /123|private|conversationId|rawMessage|token/);
  diagnostic.mark('CASE_START');
  assert.equal((await diagnostic.snapshot()).mirror, undefined);
});

test('LIVE evidence allowlist preserves null observations and drops arbitrary source values', async () => {
  const diagnostic = createUiDiagnostic();
  diagnostic.pilotProof({ fault: 'STATUS_IDENTITY_INVALID', faultStage: 'STATUS_AND_MIRROR_READ',
    lastReadStage: 'STATUS_AND_MIRROR_READ', elapsedMs: 42, elapsedCapped: false, pollCount: 1,
    admissionObserved: true, admissionValid: true, lastValidState: null,
    workerDone: null, actualWorkerAlive: null, lastReadOutcome: 'OK', lastHttpStatus: 200,
    request_id: 'private-id', text: 'private-text', url: 'https://private', rawError: 'private-error' });
  const value = await diagnostic.snapshot();
  assert.equal(value.pilotProof.fault, 'STATUS_IDENTITY_INVALID');
  assert.equal(value.pilotProof.lastValidState, null); assert.equal(value.pilotProof.workerDone, null);
  assert.doesNotMatch(JSON.stringify(value), /private|request_id|rawError|url/);
  const safe = safeResults(CASES.map(id => ({ id, status: 'BLOCKED', code: 'LIVE_COMPLETION_UNPROVEN', checks: {}, diagnostic: value })));
  assert.deepEqual(safe[1].diagnostic.pilotProof, value.pilotProof);
  diagnostic.mark('CASE_START'); assert.equal((await diagnostic.snapshot()).pilotProof, undefined);
  const unknown = sanitizeUiDiagnostic({ pilotProof: { fault: 'private', elapsedMs: Infinity, pollCount: -1,
    lastValidState: 'private', admissionObserved: 'true', workerDone: 'false' } }).pilotProof;
  assert.equal(unknown.fault, 'UNEXPECTED_FAILURE'); assert.equal(unknown.elapsedMs, null);
  assert.equal(unknown.admissionObserved, null); assert.equal(unknown.workerDone, null);
  assert.doesNotMatch(JSON.stringify(unknown), /private/);
});

test('admission diagnostics distinguish their HTTP status from baseline and discard raw errors', () => {
  const proof = sanitizeUiDiagnostic({ pilotProof: { admissionHttpStatus: 202,
    admissionBodyOutcome: 'CDP_RESOURCE_UNAVAILABLE', lastHttpStatus: 200, rawError: 'PRIVATE' } }).pilotProof;
  assert.equal(proof.admissionHttpStatus, 202); assert.equal(proof.lastHttpStatus, 200);
  assert.equal(proof.admissionBodyOutcome, 'CDP_RESOURCE_UNAVAILABLE');
  assert.doesNotMatch(JSON.stringify(proof), /PRIVATE|rawError/);
  for (const admissionHttpStatus of [null, -1, 600, 202.5, '202', Infinity]) {
    const invalid = sanitizeUiDiagnostic({ pilotProof: { admissionHttpStatus, admissionBodyOutcome: 'PRIVATE' } }).pilotProof;
    assert.equal(invalid.admissionHttpStatus, null); assert.equal(invalid.admissionBodyOutcome, null);
  }
});


test('renderer schema accepts only fixed values and never raw text, IDs, classes or paths', () => {
  const hostile = { status: 'PRIVATE_STATUS', reason: 'PRIVATE_REASON', rootCount: 3, bodyCount: Infinity,
    assistantCount: -1, documentVisible: 'true', rootVisible: 1, bodyVisible: false,
    generationMatches: 123, conversationMatches: true, text: 'PRIVATE_TEXT', content: 'PRIVATE_TEXT',
    conversationId: 73, generation: 4, className: 'PRIVATE_CLASS', path: 'PRIVATE_PATH', url: 'https://PRIVATE' };
  assert.deepEqual(sanitizeRendererDiagnostic(hostile), { status: 'UNAVAILABLE', reason: null, rootCount: null,
    bodyCount: null, assistantCount: null, documentVisible: null, rootVisible: null, bodyVisible: false,
    generationMatches: null, conversationMatches: true });
  for (const count of [0, 1, 2]) assert.equal(sanitizeRendererDiagnostic({ rootCount: count }).rootCount, count);
  for (const count of [-1, 3, 2.5, '1', NaN, Infinity]) assert.equal(sanitizeRendererDiagnostic({ rootCount: count }).rootCount, null);
  assert.equal(sanitizeRendererDiagnostic({ status: 'UNSAFE', reason: 'OPACITY_ZERO' }).reason, 'OPACITY_ZERO');
});

test('MOCK keeps renderer projection through repeated sanitizers; LIVE always drops it', async () => {
  const proof = { fault: 'RENDERER_UNPROVEN', rendererOwner: { status: 'UNSAFE', reason: 'OPACITY_ZERO',
    rootCount: 1, bodyCount: 1, assistantCount: 1, documentVisible: true, rootVisible: true, bodyVisible: false,
    generationMatches: true, conversationMatches: true, content: 'PRIVATE_TEXT' }, rendererHaru: { status: 'READY', reason: 'READY' } };
  const mock = createUiDiagnostic(true); mock.pilotProof(proof);
  const snapshot = await mock.snapshot();
  assert.deepEqual(snapshot.pilotProof, sanitizePilotProofDiagnostic(proof, true));
  assert.deepEqual(sanitizeUiDiagnostic(snapshot, true), snapshot);
  assert.equal(snapshot.pilotProof.rendererOwner.reason, 'OPACITY_ZERO');
  assert.doesNotMatch(JSON.stringify(snapshot), /PRIVATE|content/);
  for (const mode of [false, undefined, 'mock', 1]) {
    const live = createUiDiagnostic(mode); live.pilotProof(proof);
    assert.equal((await live.snapshot()).pilotProof.rendererOwner, undefined);
    assert.equal(sanitizePilotProofDiagnostic(proof, mode).rendererHaru, undefined);
  }
  mock.mark('CASE_START'); assert.equal((await mock.snapshot()).pilotProof, undefined);
});
