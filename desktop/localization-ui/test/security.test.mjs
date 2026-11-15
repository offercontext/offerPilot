import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { preferenceObservation, assertDevToolsPreference, assertDevToolsProbe, probeDevToolsDisabled } from '../security.mjs';

const policy = { lockedElectron: '44.5.1', installedSourcesHashMatched: true,
  ownerPackagedDisabled: true, haruDisabled: true };
const negative = { beforeOpen: false, beforeContents: false, openedEvent: false,
  afterOpen: false, afterContents: false };

test('raw getter diagnostics distinguish omitted, false, true and invalid values', () => {
  assert.equal(preferenceObservation(undefined), 'undefined');
  assert.equal(preferenceObservation(false), false);
  assert.equal(preferenceObservation(true), true);
  for (const value of [null, 0, 'secret']) assert.equal(preferenceObservation(value), 'invalid-type');
});

test('omitted DevTools preference requires exact Electron and independently hash-bound source policies', () => {
  assertDevToolsPreference(false, '44.5.1', policy);
  assertDevToolsPreference(undefined, '44.5.1', policy);
  for (const value of [true, null, 0, 'false']) assert.throws(() => assertDevToolsPreference(value, '44.5.1', policy));
  for (const version of [undefined, '44.5.0', '44.5.2', '45.0.0']) {
    assert.throws(() => assertDevToolsPreference(undefined, version, policy));
  }
  for (const field of Object.keys(policy)) {
    assert.throws(() => assertDevToolsPreference(undefined, '44.5.1', { ...policy, [field]: false }));
    const missing = { ...policy }; delete missing[field];
    assert.throws(() => assertDevToolsPreference(undefined, '44.5.1', missing));
  }
  assert.throws(() => assertDevToolsPreference(undefined, '44.5.1', undefined));
});

test('patched Electron omission requires its exact source lock without weakening old-package evidence', () => {
  const patchedPolicy = { ...policy, lockedElectron: '44.10.2' };
  assertDevToolsPreference(undefined, '44.10.2', patchedPolicy);
  assertDevToolsPreference(false, '44.10.2', patchedPolicy);
  for (const value of [undefined, false]) {
    assert.throws(() => assertDevToolsPreference(value, '44.10.2', policy));
    assert.throws(() => assertDevToolsPreference(value, '44.5.1', patchedPolicy));
    assert.throws(() => assertDevToolsPreference(value, undefined, undefined));
  }
  for (const version of ['44.9.1', '44.10.1', '44.10.3', '45.2.3']) {
    assert.throws(() => assertDevToolsPreference(undefined, version, { ...patchedPolicy, lockedElectron: version }));
  }
  for (const field of Object.keys(patchedPolicy)) {
    assert.throws(() => assertDevToolsPreference(undefined, '44.10.2', { ...patchedPolicy, [field]: false }));
    const missing = { ...patchedPolicy }; delete missing[field];
    assert.throws(() => assertDevToolsPreference(undefined, '44.10.2', missing));
  }
  for (const value of [true, null, 0, 'false']) {
    assert.throws(() => assertDevToolsPreference(value, '44.10.2', patchedPolicy));
  }
});

test('negative behavioral proof rejects every open-state, contents, event or missing observation', () => {
  assertDevToolsProbe(negative);
  for (const field of Object.keys(negative)) {
    assert.throws(() => assertDevToolsProbe({ ...negative, [field]: true }));
    const missing = { ...negative }; delete missing[field];
    assert.throws(() => assertDevToolsProbe(missing));
  }
  assert.throws(() => assertDevToolsProbe(undefined));
});

test('actual probe targets one existing window and preserves transient-open failure evidence', async () => {
  const contents = new EventEmitter();
  contents.isDevToolsOpened = () => false;
  let calls = 0;
  contents.openDevTools = options => {
    calls++;
    assert.deepEqual(options, { mode: 'detach', activate: false });
    contents.emit('devtools-opened');
  };
  const electron = { BrowserWindow: { getAllWindows: () => [{ id: 1, webContents: contents }] } };
  await assert.rejects(probeDevToolsDisabled(electron, 2), /exact-product-window-required/);
  assert.equal(calls, 0);
  const result = await probeDevToolsDisabled(electron, 1);
  assert.deepEqual(result, { ...negative, openedEvent: true });
  assert.throws(() => assertDevToolsProbe(result));
  assert.equal(calls, 1);
  assert.equal(contents.listenerCount('devtools-opened'), 0);
});

test('already-open DevTools cannot be hidden or retried into passing evidence', async () => {
  const contents = new EventEmitter();
  contents.isDevToolsOpened = () => true;
  contents.devToolsWebContents = {};
  contents.openDevTools = () => assert.fail('already open window must not be opened again');
  const electron = { BrowserWindow: { getAllWindows: () => [{ id: 1, webContents: contents }] } };
  const result = await probeDevToolsDisabled(electron, 1);
  assert.throws(() => assertDevToolsProbe(result));
  assert.equal(result.beforeOpen, true);
  assert.equal(result.beforeContents, true);
  assert.equal(result.afterOpen, true);
  assert.equal(result.afterContents, true);
  assert.equal(contents.listenerCount('devtools-opened'), 0);
});
