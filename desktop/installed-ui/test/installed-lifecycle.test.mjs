import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { DESKTOP_SOURCE_FILES } from '../desktop-source-manifest.mjs';
const source = fs.readFileSync(new URL('../smoke.mjs', import.meta.url), 'utf8');
const lifecycle = source.slice(source.indexOf('async function closeNormally()'), source.indexOf('async function openList'));
async function runLifecycle(options = {}) {
  let quit = false;
  const calls = [];
  const owner = { id: 1, visible: true, isVisible() { return this.visible; }, isDestroyed: () => false,
    close() { this.visible = false; if (options.xQuits) quit = true; } };
  const haru = { id: 2, visible: true, isVisible() { return this.visible; }, isDestroyed: () => false,
    close() { this.visible = false; } };
  const page = { isClosed: () => quit, evaluate: async () => options.unhealthy ? 503 : 200 };
  const mirror = { isClosed: () => Boolean(options.haruDestroyed), evaluate: async () => !options.disconnected };
  const identities = [{ pid: 15, created: 'main-created' }, { pid: 16, created: 'backend-created' }];
  const context = { assert, stage: '', owned: identities, fatalNetwork: false,
    invokeTrayAction: function invokeTrayAction() {}, setTimeout: fn => fn(),
    savedPort: async () => 8111, checkpoint: async name => calls.push(name), writeReport: async () => {},
    waitUntil: async predicate => { assert.equal(await predicate(), true, 'terminal condition not established'); },
    endpointOpen: async ({ port }) => options.portLeak || (!quit && port === 8111),
    windows: async () => ({ processes: quit && !options.quitDoesNotExit ? [] : identities.map(item => options.reusedPid ? { ...item, created: 'different-process' } : item) }),
  };
  const app = {
    browserWindow: async candidate => ({ evaluate: fn => fn(candidate === page ? owner : haru) }),
    evaluate: async (fn, arg) => {
      if (fn.name !== 'invokeTrayAction') return fn({ BrowserWindow: { fromId: id => [owner, haru].find(win => win.id === id) } }, arg);
      calls.push(arg);
      if (['open', 'double-click'].includes(arg)) owner.visible = true;
      if (arg === 'hide') haru.visible = false;
      if (['show', 'click'].includes(arg)) haru.visible = true;
      if (arg === 'quit') quit = true;
      return true;
    },
  };
  const info = { number: 1, port: 8111 };
  context.current = { app, page, haru: mirror, ids: { owner: 1, haru: 2 }, info,
    debugEndpoints: [{ address: '127.0.0.1', port: 9222 }] };
  vm.createContext(context);
  await vm.runInContext(`${lifecycle}\ncloseNormally()`, context);
  return { info, calls, context };
}
test('actual smoke lifecycle proves X-hide, preserved owners, real callback paths, then OS exit and ports', async () => {
  const { info, calls, context } = await runLifecycle();
  assert.equal(info.mainCloseHidesWithoutStoppingBackend, true);
  assert.equal(info.trueExitViaProductionTrayCallback, true);
  assert.equal(info.mainBackendAndRendererExited, true);
  assert.equal(info.backendAndDebugPortsClosed, true);
  assert.deepEqual(calls, ['open', 'hide', 'show', 'click', 'double-click', 'quit', 'launch-1-tray-exit-and-port-release']);
  assert.equal(context.current, undefined);
});
for (const option of ['xQuits', 'reusedPid', 'unhealthy', 'disconnected', 'haruDestroyed', 'quitDoesNotExit', 'portLeak']) {
  test(`${option} cannot turn hide or callback delivery into successful true exit`, async () => {
    await assert.rejects(runLifecycle({ [option]: true }));
  });
}
test('installed launch preserves native download policy and verifies the complete reviewed desktop module list', () => {
  assert.doesNotMatch(source, /firstWindow\(|acceptDownloads\s*:/);
  assert.match(source, /for \(const name of DESKTOP_SOURCE_FILES\)/);
  assert.equal(DESKTOP_SOURCE_FILES.length, 12);
  assert.ok(DESKTOP_SOURCE_FILES.includes('updater.cjs'));
  assert.ok(DESKTOP_SOURCE_FILES.includes('update-signature.cjs'));
  assert.match(source, /security\.owner\.devToolsProbe = await app\.evaluate\(observeDevToolsDisabled, ids\.owner\)/);
  assert.match(source, /security\.haru\.devToolsProbe = await app\.evaluate\(observeDevToolsDisabled, ids\.haru\)/);
  assert.match(source, /validatePartitionIsolation\(security\.partitionIsolation\)/);
  assert.match(source, /sourceDesktopModulesMatch/);
});
