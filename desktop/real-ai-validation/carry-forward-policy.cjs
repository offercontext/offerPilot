'use strict';

// One reviewed historical source, not a general ledger restore facility. No
// runtime input, environment variable or testing override can change this pin.
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { inflateRawSync } = require('node:zlib');
function freeze(value) {
  if (value && typeof value === 'object') { for (const item of Object.values(value)) freeze(item); Object.freeze(value); }
  return value;
}
const SOURCE = freeze({ synthetic: false, runId: 38694575612, artifactId: 11953258616,
  digest: '8a31c0a40981a543b85103217b7d3c2a5cd9ebeb080b43664a4c81bd9ad3e917',
  helperCommit: '93b62afb5629f5720558f22ac06b0e93ab7c7513', requestCommit: '4556258bb69e5f7233763cd0b0db26b6545328b8',
  productCommit: '9c245314e570f1d6de034e4d52deb46979874416', buildRunId: 38500952016, productArtifactId: 11865823397,
  fullRegressionRunId: 38500952016, installerSha256: '96c7517bc2a7674d897f324971edb2cbb56a9a9b23846c65af65293eb25c58e6',
  priceCheckedAt: '2026-10-15', priceValidUntilUtc: '2026-10-15T23:59:59.999Z' });
const RECOVERY = freeze({ sessionId: 'offerpilot-fixed-exe-real-ai-20261008', scope: 'pilot-haru-carry-forward-only',
  additionalRequests: 1, executionMs: 120000, allowedCases: ['pilot-stream'], source: SOURCE,
  sourceArtifactName: 'real-ai-single-session-ledger-38694575612',
  sourceUploadStep: 'Retain only numeric ledger and allowlisted result', maxZipBytes: 65536, maxJsonBytes: 16384 });
const ROW = freeze({ caseId: 'connection', status: 'SETTLED', envelope: 'UNCHANGED', cap: 64, micro: 188,
  promptTokens: 34, completionTokens: 15, cacheHitTokens: 0, cacheMissTokens: 34,
  outboundStarted: true, upstreamResponded: true, clientDisconnectObserved: false });
const reasons = ['AUTH', 'ROUTE', 'CLOSED', 'DEADLINE', 'UNARMED', 'BUSY', 'CASE', 'BUDGET', 'COUNT', 'BODY', 'MODEL',
  'PARAMETER', 'CANCELLED', 'DISCONNECT', 'TIMEOUT', 'LEDGER', 'UPSTREAM', 'REDIRECT', 'PROTOCOL', 'USAGE', 'SETTLED', 'EXPIRED', 'UPSTREAM_DISCONNECT'];
const brands = new WeakSet();
function invalid() { throw Object.assign(new Error('CARRY_FORWARD_INVALID'), { safeCode: 'CARRY_FORWARD_INVALID' }); }
function brand(mode, source, row) {
  const carry = freeze({ mode, scope: RECOVERY.scope, source, requests: [row], sentRequests: 1, settledMicroCny: 188, retainedMicroCny: 0 });
  brands.add(carry); return carry;
}
function validateCarryForward(carry, mode) {
  if (!brands.has(carry) || !['live', 'mock'].includes(mode) || carry.mode !== mode) invalid();
  return carry;
}
function carryForwardEvidence(carry) {
  validateCarryForward(carry, carry?.mode);
  return freeze({ scope: carry.scope, mode: carry.mode, source: carry.source,
    priorRequestCount: 1, priorSettledMicroCny: 188, priorRetainedMicroCny: 0 });
}
// Compare the serialized evidence schema, including cross-realm MOCK records.
// Accessors, symbols, hidden fields and extra keys are never evidence fields.
function sameEvidence(actual, expected) {
  if (expected === null || typeof expected !== 'object') { assert.equal(actual, expected); return; }
  assert.ok(actual !== null && typeof actual === 'object' && Array.isArray(actual) === Array.isArray(expected));
  const keys = Reflect.ownKeys(actual); assert.ok(keys.every(key => typeof key === 'string'));
  assert.deepEqual(keys.sort(), Reflect.ownKeys(expected).sort());
  for (const key of keys) {
    const descriptor = Object.getOwnPropertyDescriptor(actual, key); assert.ok(Object.hasOwn(descriptor, 'value'));
    assert.equal(descriptor.enumerable, Object.getOwnPropertyDescriptor(expected, key).enumerable);
    sameEvidence(descriptor.value, expected[key]);
  }
}
function validateCarryForwardEvidence(summary, mode) {
  try {
    assert.ok(['live', 'mock'].includes(mode));
    sameEvidence(summary, { scope: RECOVERY.scope, mode,
      source: mode === 'live' ? SOURCE : { synthetic: true, kind: 'synthetic-carry-forward-fixture' },
      priorRequestCount: 1, priorSettledMicroCny: 188, priorRetainedMicroCny: 0 });
    return summary;
  } catch { invalid(); }
}
function validateCarryForwardPrefix(snapshot, mode) {
  try {
    validateCarryForwardEvidence(snapshot?.carryForward, mode);
    assert.ok(Number.isSafeInteger(snapshot.sentRequests) && snapshot.sentRequests >= 1 && snapshot.sentRequests <= 2);
    assert.ok(Array.isArray(snapshot.requests) && snapshot.requests.length === snapshot.sentRequests);
    sameEvidence(snapshot.requests[0], ROW);
    assert.equal(snapshot.currentRequests, snapshot.sentRequests - 1);
    return snapshot.carryForward;
  } catch { invalid(); }
}
function createMockCarryForward() {
  return brand('mock', { synthetic: true, kind: 'synthetic-carry-forward-fixture' }, { ...ROW });
}
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) { crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0); }
  return (crc ^ 0xffffffff) >>> 0;
}
// JSON.parse alone accepts duplicate keys. Scan the already bounded UTF-8 text
// first and reject duplicate keys at every depth, before interpreting any state.
function strictJson(bytes) {
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); let cursor = 0;
  const ws = () => { while (/\s/.test(text[cursor] || '') && cursor < text.length) cursor++; };
  function string() {
    const start = cursor++; let escaped = false;
    while (cursor < text.length) { const char = text[cursor++]; if (!escaped && char === '"') return JSON.parse(text.slice(start, cursor));
      if (!escaped && char === '\\') escaped = true; else escaped = false; }
    invalid();
  }
  function value(depth = 0) {
    assert.ok(depth <= 32); ws(); const char = text[cursor];
    if (char === '"') { string(); return; }
    if (char === '{') {
      cursor++; ws(); const keys = new Set(); if (text[cursor] === '}') { cursor++; return; }
      while (cursor < text.length) {
        ws(); assert.equal(text[cursor], '"'); const key = string(); assert.ok(!keys.has(key)); keys.add(key);
        ws(); assert.equal(text[cursor++], ':'); value(depth + 1); ws(); const end = text[cursor++];
        if (end === '}') return; assert.equal(end, ',');
      } invalid();
    }
    if (char === '[') {
      cursor++; ws(); if (text[cursor] === ']') { cursor++; return; }
      while (cursor < text.length) { value(depth + 1); ws(); const end = text[cursor++]; if (end === ']') return; assert.equal(end, ','); } invalid();
    }
    const match = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(cursor));
    assert.ok(match); cursor += match[0].length;
  }
  value(); ws(); assert.equal(cursor, text.length); return JSON.parse(text);
}
function fixedZipJson(bytes) {
  const end = bytes.length - 22;
  assert.equal(bytes.readUInt32LE(end), 0x06054b50); assert.equal(bytes.readUInt16LE(end + 20), 0);
  for (const offset of [4, 6]) assert.equal(bytes.readUInt16LE(end + offset), 0);
  assert.equal(bytes.readUInt16LE(end + 8), 2); assert.equal(bytes.readUInt16LE(end + 10), 2);
  const centralSize = bytes.readUInt32LE(end + 12), centralStart = bytes.readUInt32LE(end + 16);
  assert.equal(centralStart + centralSize, end); let cursor = centralStart, localEnd = 0; const entries = {};
  for (let index = 0; index < 2; index++) {
    assert.equal(bytes.readUInt32LE(cursor), 0x02014b50);
    const flags = bytes.readUInt16LE(cursor + 8), method = bytes.readUInt16LE(cursor + 10), crc = bytes.readUInt32LE(cursor + 16);
    const compressed = bytes.readUInt32LE(cursor + 20), size = bytes.readUInt32LE(cursor + 24);
    const nameLen = bytes.readUInt16LE(cursor + 28), extraLen = bytes.readUInt16LE(cursor + 30), commentLen = bytes.readUInt16LE(cursor + 32);
    const local = bytes.readUInt32LE(cursor + 42), name = bytes.subarray(cursor + 46, cursor + 46 + nameLen).toString('utf8');
    assert.ok(['result.json', 'ledger.json'].includes(name) && !Object.hasOwn(entries, name));
    assert.equal(flags, 8); assert.equal(method, 8); assert.equal(extraLen, 0); assert.equal(commentLen, 0);
    assert.equal(bytes.readUInt16LE(cursor + 34), 0); assert.ok(size > 0 && size <= RECOVERY.maxJsonBytes);
    assert.ok(compressed > 0 && compressed <= RECOVERY.maxZipBytes); assert.equal(local, localEnd);
    assert.equal(bytes.readUInt32LE(local), 0x04034b50); assert.equal(bytes.readUInt16LE(local + 6), flags);
    assert.equal(bytes.readUInt16LE(local + 8), method); assert.equal(bytes.readUInt16LE(local + 26), nameLen);
    assert.equal(bytes.readUInt16LE(local + 28), 0);
    assert.equal(bytes.subarray(local + 30, local + 30 + nameLen).toString('utf8'), name);
    const dataStart = local + 30 + nameLen, dataEnd = dataStart + compressed;
    assert.ok(dataEnd + 16 <= centralStart); assert.equal(bytes.readUInt32LE(dataEnd), 0x08074b50);
    assert.equal(bytes.readUInt32LE(dataEnd + 4), crc); assert.equal(bytes.readUInt32LE(dataEnd + 8), compressed);
    assert.equal(bytes.readUInt32LE(dataEnd + 12), size);
    const plain = inflateRawSync(bytes.subarray(dataStart, dataEnd), { maxOutputLength: RECOVERY.maxJsonBytes });
    assert.equal(plain.length, size); assert.equal(crc32(plain), crc); entries[name] = strictJson(plain);
    localEnd = dataEnd + 16; cursor += 46 + nameLen + extraLen + commentLen;
  }
  assert.equal(localEnd, centralStart); assert.equal(cursor, end); return entries;
}
function verifyPriorZip(bytes) {
  try {
    assert.ok(Buffer.isBuffer(bytes) && bytes.length >= 22 && bytes.length <= RECOVERY.maxZipBytes);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), SOURCE.digest);
    const { 'result.json': result, 'ledger.json': ledger } = fixedZipJson(bytes);
    assert.deepEqual(ledger, { schema: 1, kind: 'bounded-real-provider-cost-counters', currency: 'CNY',
      priceCheckedAt: SOURCE.priceCheckedAt, priceValidUntilUtc: SOURCE.priceValidUntilUtc, sessionId: RECOVERY.sessionId,
      model: 'deepseek-flash', productCommit: SOURCE.productCommit, buildRunId: SOURCE.buildRunId, artifactId: SOURCE.productArtifactId,
      budgetMicroCny: 10000000, reserveMicroCny: 3000000, sentRequests: 1, settledMicroCny: 188, retainedMicroCny: 0,
      active: false, closed: true, journalFailed: false, helperCommit: SOURCE.helperCommit, requestCommit: SOURCE.requestCommit,
      runId: String(SOURCE.runId), runAttempt: 1, denied: Object.fromEntries(reasons.map(reason => [reason, 0])),
      requests: [ROW], mode: 'LIVE', billingKind: 'real-provider-conservative-accounting' });
    assert.equal(ledger.settledMicroCny, ROW.cacheMissTokens * 2 + ROW.completionTokens * 8);
    for (const [key, expected] of Object.entries({ schema: 1, mode: 'LIVE', realProviderCalled: true,
      productCommit: SOURCE.productCommit, buildRunId: SOURCE.buildRunId, artifactId: SOURCE.productArtifactId,
      fullRegressionRunId: SOURCE.fullRegressionRunId, fullRegression: 'independent-not-certified', installerSha256: SOURCE.installerSha256,
      releaseReady: false, independentFullGateCertified: false, route: 'real-installed-ui-via-loopback-budget-broker-to-real-provider',
      screenshotsCaptured: false, rawLogsCaptured: false, status: 'FAIL', code: 'UI_CASES_INCOMPLETE', cleanupCode: 'CLEANUP_PASSED', cleanupPassed: true }))
      assert.deepEqual(result[key], expected);
    assert.deepEqual(result.screenshotEvidence, { captured: [], skipped: [] });
    assert.deepEqual(result.coverageLimitations, []);
    assert.deepEqual(result.scenarios.map(item => item.id), ['connection', 'pilot-stream', 'pilot-cancel', 'pilot-hitl-reject', 'interview-preparation', 'resume-structure', 'offer-negotiation']);
    assert.deepEqual(result.scenarios[0].checks, { settingsSavedThroughUi: true, connectionTestClicked: true, connectionSucceeded: true, oneProviderRequestVerified: true });
    for (const [index, scenario] of result.scenarios.entries()) {
      assert.equal(scenario.status, index === 0 ? 'PASS' : 'BLOCKED');
      assert.equal(scenario.code, index === 0 ? 'PASSED' : 'LIVE_COMPLETION_UNPROVEN');
      if (index !== 0) assert.deepEqual(scenario.checks, {});
    }
    return brand('live', SOURCE, ledger.requests[0]);
  } catch { invalid(); }
}
module.exports = { RECOVERY, PRIOR_ROW: ROW, verifyPriorZip, createMockCarryForward, validateCarryForward, carryForwardEvidence, validateCarryForwardEvidence, validateCarryForwardPrefix };
