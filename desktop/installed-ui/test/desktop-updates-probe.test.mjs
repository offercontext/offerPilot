import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { verifyUnavailableDesktopUpdates, readUnavailableDesktopUpdateState } from '../desktop-updates-probe.mjs';
import { updatesFixture } from './fixtures/desktop-updates-fixture.mjs';

test('unsigned desktop update verification reads only real owner state and public disabled UI', async () => {
  const f = updatesFixture();
  const result = await verifyUnavailableDesktopUpdates(f.page);
  assert.deepEqual(f.calls, ['getState']);
  assert.equal(result.status, 'unavailable');
  assert.equal(result.checkControlDisabled, true);
  assert.equal(result.updateActionsInvoked, false);
  assert.equal(result.updateFeedActivated, false);
  assert.equal(result.signedUpgradeEndToEndValidated, false);
  assert.doesNotMatch(JSON.stringify(result), /private-token|do-not-record|releaseNotes/);
});
for (const options of [{ cardCount: 0 }, { cardCount: 2 }, { hidden: true }, { checkEnabled: true },
  { downloadVisible: true }, { installVisible: true }, { missingBridge: true }, { haru: true }, { stateError: true },
  { state: { status: 'idle' } }, { state: { status: 'available' } }, { state: { status: 'downloading' } },
  { state: { currentVersion: 'unexpected' } }, { state: { reason: 'unexpected update source' } },
  { missingText: '当前无法在线更新' }, { missingText: '此验证包尚未配置正式签名更新源。' }, { missingText: '0.1.0-desktop.1' }]) {
  test(`unexpected update state or UI ${JSON.stringify(options)} fails without invoking an update`, async () => {
    const f = updatesFixture(options);
    await assert.rejects(verifyUnavailableDesktopUpdates(f.page));
    assert.equal(f.calls.includes('forbidden-update-action'), false);
    if (options.haru || options.missingBridge) assert.equal(f.calls.includes('getState'), false);
  });
}
test('owner state reader survives serialization and exposes fixed booleans only', async () => {
  const f = updatesFixture();
  const args = { version: '0.1.0-desktop.1', reason: '此验证包尚未配置正式签名更新源。' };
  const context = vm.createContext({ window: f.window, args, setTimeout, clearTimeout });
  const result = await vm.runInContext(`(${readUnavailableDesktopUpdateState.toString()})(args)`, context);
  assert.ok(Object.values(result).every(value => value === true));
  assert.deepEqual(f.calls, ['getState']);
});
test('unanswered production state IPC has a bounded timeout without invoking another operation', async () => {
  const cleared = [];
  const context = vm.createContext({
    window: { offerpilotDesktop: { role: 'owner' }, offerpilotUpdates: { getState: () => new Promise(() => {}) } },
    args: { version: '0.1.0-desktop.1', reason: '此验证包尚未配置正式签名更新源。' },
    setTimeout: (fn, delay) => { assert.equal(delay, 5000); queueMicrotask(fn); return 73; },
    clearTimeout: value => cleared.push(value),
  });
  await assert.rejects(vm.runInContext(`(${readUnavailableDesktopUpdateState.toString()})(args)`, context), /state read timed out/);
  assert.deepEqual(cleared, [73]);
});
