import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { observeDevToolsDisabled } from '../devtools-probe.mjs';
import { validateSecurity } from '../contract.mjs';
function fixture(onOpen) {
  const contents = new EventEmitter();
  contents.isDevToolsOpened = () => false;
  contents.devToolsWebContents = null;
  contents.openDevTools = (options) => {
    assert.deepEqual(options, { mode: 'detach', activate: false });
    assert.equal(contents.listenerCount('devtools-opened'), 1, 'listener must predate open attempt');
    onOpen?.(contents);
  };
  return { contents, electron: { BrowserWindow: { getAllWindows: () => [{ webContents: contents }] } } };
}
const good = { packaged: true, nodeIntegration: false, contextIsolation: true, sandbox: true,
  webSecurity: true, devToolsOpened: false, unsafeSwitches: [] };
test('disabled DevTools yields only five false observations and removes its listener', async () => {
  const { contents, electron } = fixture();
  const result = await observeDevToolsDisabled(electron);
  assert.deepEqual(result, { beforeOpen: false, beforeContents: false, openedEvent: false, afterOpen: false, afterContents: false });
  validateSecurity({ ...good, devToolsProbe: result });
  assert.equal(contents.listenerCount('devtools-opened'), 0);
});
test('even a transient opened event fails when the final window is closed', async () => {
  const { contents, electron } = fixture((contents) => contents.emit('devtools-opened'));
  const result = await observeDevToolsDisabled(electron);
  assert.equal(result.openedEvent, true);
  assert.equal(result.afterOpen, false);
  assert.throws(() => validateSecurity({ ...good, devToolsProbe: result }));
  assert.equal(contents.listenerCount('devtools-opened'), 0);
});
test('created devtools contents fail before a visible/open event', async () => {
  const { electron } = fixture((contents) => { contents.devToolsWebContents = { token: 'never returned' }; });
  const result = await observeDevToolsDisabled(electron);
  assert.equal(result.afterContents, true);
  assert.doesNotMatch(JSON.stringify(result), /token|never returned/);
  assert.throws(() => validateSecurity({ ...good, devToolsProbe: result }));
});
test('already open DevTools is never retried or converted into a pass', async () => {
  const { electron, contents } = fixture(() => { throw new Error('must not try opening again'); });
  contents.isDevToolsOpened = () => true;
  const result = await observeDevToolsDisabled(electron);
  assert.equal(result.beforeOpen, true);
  assert.throws(() => validateSecurity({ ...good, devToolsProbe: result }));
});
test('probe API exceptions remove the listener and remain failures', async () => {
  const { electron, contents } = fixture(() => { throw new Error('API failure'); });
  await assert.rejects(observeDevToolsDisabled(electron));
  assert.equal(contents.listenerCount('devtools-opened'), 0);
});
