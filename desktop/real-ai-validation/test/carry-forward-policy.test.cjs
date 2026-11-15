'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { RECOVERY, verifyPriorZip, createMockCarryForward, validateCarryForward, carryForwardEvidence, validateCarryForwardEvidence, validateCarryForwardPrefix } = require('../carry-forward-policy.cjs');
const bytes = () => fs.readFileSync(path.join(__dirname, 'fixtures/prior-paid-38694575612.zip'));
test('only the exact reviewed closed source imports a frozen 188 microCNY / 1-request prefix', () => {
  const carry = verifyPriorZip(bytes());
  assert.equal(validateCarryForward(carry, 'live'), carry);
  assert.equal(carry.mode, 'live'); assert.equal(carry.scope, 'pilot-haru-carry-forward-only');
  assert.equal(carry.sentRequests, 1); assert.equal(carry.settledMicroCny, 188); assert.equal(carry.retainedMicroCny, 0);
  assert.equal(carry.source.runId, 38694575612); assert.equal(carry.requests.length, 1);
  assert.equal(carry.requests[0].caseId, 'connection'); assert.equal(carry.requests[0].promptTokens, 34);
  assert.equal(carry.requests[0].completionTokens, 15); assert.equal(carry.requests[0].micro, 188);
  for (const value of [carry, carry.source, carry.requests, carry.requests[0], RECOVERY, RECOVERY.allowedCases]) assert.equal(Object.isFrozen(value), true);
  assert.throws(() => { carry.requests[0].micro = 0; });
  assert.deepEqual(carryForwardEvidence(carry), { scope: RECOVERY.scope, mode: 'live', source: carry.source,
    priorRequestCount: 1, priorSettledMicroCny: 188, priorRetainedMicroCny: 0 });
});
test('plain objects, copied brands and MOCK evidence cannot authorize LIVE recovery', () => {
  const live = verifyPriorZip(bytes()), mock = createMockCarryForward();
  assert.equal(validateCarryForward(mock, 'mock'), mock);
  assert.deepEqual(mock.source, { synthetic: true, kind: 'synthetic-carry-forward-fixture' });
  for (const [value, mode] of [[{ ...live }, 'live'], [structuredClone(live), 'live'], [mock, 'live'], [live, 'mock'], [live, 'LIVE'], [null, 'live']])
    assert.throws(() => validateCarryForward(value, mode), { safeCode: 'CARRY_FORWARD_INVALID' });
  assert.throws(() => carryForwardEvidence({ ...live }), { safeCode: 'CARRY_FORWARD_INVALID' });
});
test('no source digest, numeric state, ZIP entries, JSON or length can be overridden', () => {
  const corrupted = bytes(); corrupted[70] ^= 1;
  for (const bad of [null, 'not bytes', Buffer.alloc(0), Buffer.alloc(65537), bytes().subarray(0, -1), Buffer.concat([bytes(), Buffer.from('extra')]), corrupted])
    assert.throws(() => verifyPriorZip(bad), { safeCode: 'CARRY_FORWARD_INVALID' });
  assert.equal(RECOVERY.additionalRequests, 1); assert.equal(RECOVERY.executionMs, 120000);
  assert.deepEqual(RECOVERY.allowedCases, ['pilot-stream']);
  assert.equal(RECOVERY.sessionId, 'offerpilot-fixed-exe-real-ai-20261008');
});

test('plain public evidence validates exact source and immutable prefix across MOCK VM realms without authorizing broker', () => {
  const carry = createMockCarryForward();
  const snapshot = { carryForward: carryForwardEvidence(carry), sentRequests: 1, currentRequests: 0, requests: carry.requests };
  const crossRealm = vm.runInNewContext(`(${JSON.stringify(snapshot)})`);
  assert.equal(validateCarryForwardPrefix(crossRealm, 'mock'), crossRealm.carryForward);
  assert.throws(() => validateCarryForward(crossRealm, 'mock'));
  for (const change of [v => { v.carryForward.source.synthetic = false; }, v => { v.carryForward.priorSettledMicroCny = 0; },
    v => { v.requests[0].micro = 187; }, v => { v.requests[0].cap++; }, v => { v.requests[0].extra = true; },
    v => { v.sentRequests = 0; }, v => { v.currentRequests = 1; }, v => v.requests.push(v.requests[0])]) {
    const value = structuredClone(snapshot); change(value); assert.throws(() => validateCarryForwardPrefix(value, 'mock'));
  }
  const accessor = structuredClone(snapshot.carryForward); Object.defineProperty(accessor, 'scope', { get: () => RECOVERY.scope });
  assert.throws(() => validateCarryForwardEvidence(accessor, 'mock'));
  const forged = structuredClone(snapshot.carryForward); forged.source = RECOVERY.source;
  assert.throws(() => validateCarryForwardEvidence(forged, 'live'));
});
