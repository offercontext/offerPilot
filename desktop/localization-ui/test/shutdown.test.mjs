import test from 'node:test';
import assert from 'node:assert/strict';
import { bounded, containFailedLaunch, requestProductionQuit } from '../shutdown.mjs';

test('failure teardown invokes the real quit without safety preflight or passing any stage', async () => {
  const calls = []; const menu = { label: 'Quit and Stop Local Service', enabled: true, click: () => calls.push('quit') };
  const app = { evaluate: fn => fn({ Menu: { getApplicationMenu: () => ({ items: [{ submenu: { items: [menu] } }] }) } }) };
  const result = await containFailedLaunch({ requestQuit: () => requestProductionQuit(app),
    waitExited: async () => { await new Promise(resolve => setTimeout(resolve, 10)); assert.deepEqual(calls, ['quit']);
      return { allInstalledProcessesExited: true, backendPortClosed: true }; },
    forceOwned: async () => assert.fail('normal production exit must not force cleanup') });
  assert.deepEqual(result, { purpose: 'failure-containment-only', productionQuitRequested: true,
    forcedCleanupAttempted: false, allInstalledProcessesExited: true, backendPortClosed: true });
});

test('failed or missing menu falls back to exact ownership cleanup without claiming acceptance', async () => {
  for (const requestQuit of [async () => { throw new Error('sticky safety failure'); }, async () => false]) {
    let attempts = 0, stopped = false;
    const result = await containFailedLaunch({ requestQuit,
      waitExited: async () => { attempts++; if (!stopped) throw new Error('still-running');
        return { allInstalledProcessesExited: true, backendPortClosed: true }; },
      forceOwned: async () => { stopped = true; return { stopped: 2 }; } });
    assert.equal(attempts, 2); assert.equal(result.productionQuitRequested, false);
    assert.equal(result.forcedCleanupAttempted, true); assert.equal(result.allInstalledProcessesExited, true);
    assert.equal(result.stopped, 2); assert.equal(result.status, undefined);
  }
});

test('unconfirmed cleanup stays unconfirmed and waits are bounded', async () => {
  await assert.rejects(bounded(() => new Promise(() => {}), 10), /shutdown-timeout/);
  const result = await containFailedLaunch({ requestQuit: async () => true,
    waitExited: async () => { throw new Error('still-running'); }, forceOwned: async () => { throw new Error('identity-mismatch'); } });
  assert.equal(result.forceCleanupUnconfirmed, true); assert.equal(result.allInstalledProcessesExited, false);
  assert.equal(result.backendPortClosed, null);
  for (const items of [[], [{ label: 'Quit and Stop Local Service', enabled: false, click() {} }]]) {
    await assert.rejects(requestProductionQuit({ evaluate: fn => fn({ Menu: { getApplicationMenu: () => ({ items: [{ submenu: { items } }] }) } }) }), /explicit-quit-not-found/);
  }
});
