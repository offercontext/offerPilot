import test from 'node:test';
import assert from 'node:assert/strict';
import { safeFailure, commandFailure } from '../diagnostics.mjs';

test('diagnostics preserve only allowlisted classes/codes/tool exit status', () => {
  const error = new Error('token=secret ws://127.0.0.1:1234/private-debug-endpoint');
  error.code = 'ENOENT';
  error.stack = 'private stack';
  error.cause = { headers: { authorization: 'secret' } };
  assert.deepEqual(safeFailure(error), { errorClass: 'Error', errorCode: 'ENOENT' });
  assert.deepEqual(safeFailure(commandFailure('COMMAND_EXIT', '7zip', 2)),
    { errorClass: 'Error', errorCode: 'COMMAND_EXIT', tool: '7zip', exitCode: 2 });
  assert.deepEqual(safeFailure({ name: 'token=secret', code: 'ws://private', tool: 'private/path', exitCode: 'secret' }), { errorClass: 'Error' });
});

test('security diagnostics are bounded booleans/enums and identify every failed expectation', async () => {
  const { securityDiagnostics } = await import('../diagnostics.mjs');
  const { validateSecurity } = await import('../contract.mjs');
  const good = { packaged: true, nodeIntegration: false, contextIsolation: true, sandbox: true,
    webSecurity: true, devToolsOpened: false, unsafeSwitches: [],
    devToolsProbe: { beforeOpen: false, beforeContents: false, openedEvent: false, afterOpen: false, afterContents: false } };
  assert.deepEqual(securityDiagnostics(good).failedExpectations, []);
  validateSecurity(good);
  for (const field of Object.keys(good).filter((field) => !['unsafeSwitches', 'devToolsProbe'].includes(field))) {
    for (const value of [!good[field], undefined, null, 'secret ws://private', { token: 'secret' }, 0]) {
      const changed = { ...good, [field]: value, ignoredSecret: 'must-never-appear' };
      const result = securityDiagnostics(changed);
      assert.deepEqual(result.failedExpectations, [field]);
      assert.ok([true, false, 'undefined', 'null', 'invalid-type'].includes(result.observed[field]));
      assert.doesNotMatch(JSON.stringify(result), /secret|private|ignoredSecret|must-never-appear/);
      assert.throws(() => validateSecurity(changed), 'diagnostics must never relax the gate');
    }
  }
  const result = securityDiagnostics({ ...good, unsafeSwitches: ['no-sandbox', 'ws://secret', 'no-sandbox'] });
  assert.deepEqual(result.failedExpectations, ['unsafeSwitches']);
  assert.deepEqual(result.observed.unsafeSwitches, { type: 'array', present: ['no-sandbox'], unexpectedEntries: true });
  assert.throws(() => validateSecurity({ ...good, unsafeSwitches: ['no-sandbox', 'ws://secret'] }));
  assert.doesNotMatch(JSON.stringify(result), /ws:|secret/);
});
test('unavailable getter does not replace the required strict behavior probe', async () => {
  const { recordSecurityBeforeValidation } = await import('../diagnostics.mjs');
  const { validateSecurity } = await import('../contract.mjs');
  const observed = { packaged: true, nodeIntegration: false, contextIsolation: true, sandbox: true,
    webSecurity: true, devToolsOpened: false, unsafeSwitches: [] };
  const launch = {};
  let saved;
  await recordSecurityBeforeValidation(launch, observed, async () => { saved = structuredClone(launch); });
  assert.equal(saved.security.observed.devToolsPreference, 'undefined');
  assert.deepEqual(saved.security.failedExpectations, ['beforeOpen', 'beforeContents', 'openedEvent', 'afterOpen', 'afterContents']
    .map((field) => `devToolsProbe.${field}`));
  assert.equal(saved.securityValidation, 'pending');
  assert.throws(() => validateSecurity(observed), 'missing probe must still fail');
  observed.devToolsProbe = { beforeOpen: false, beforeContents: false, openedEvent: false, afterOpen: false, afterContents: false };
  await recordSecurityBeforeValidation(launch, observed, async () => { saved = structuredClone(launch); });
  assert.deepEqual(saved.security.failedExpectations, []);
  validateSecurity(observed);
  observed.devToolsProbe.afterContents = true;
  await recordSecurityBeforeValidation(launch, observed, async () => { saved = structuredClone(launch); });
  assert.deepEqual(saved.security.failedExpectations, ['devToolsProbe.afterContents']);
  assert.throws(() => validateSecurity(observed));
});
