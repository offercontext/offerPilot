import test from 'node:test';
import assert from 'node:assert/strict';
import { createUiDiagnostic, sanitizeUiDiagnostic } from '../ui-diagnostics.mjs';
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
