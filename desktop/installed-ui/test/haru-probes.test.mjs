import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { EventEmitter } from 'node:events';
import { installTrayObserver, restoreTrayObserver, invokeTrayAction, probeStorageIsolation, probeHaruApiDeny, probeHaruStatusMirror } from '../haru-probes.mjs';
function storage() { const entries = new Map(); return { getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key) }; }
function renderer(shared = {}) {
  let cookie = '';
  const context = vm.createContext({ localStorage: storage(), sessionStorage: storage(), document: {
    get cookie() { return cookie; }, set cookie(value) { cookie = value.includes('Max-Age=0') ? '' : value.split(';')[0]; },
  }, ...shared });
  return { context, evaluate: async (fn, arg) => { context.argument = arg; return structuredClone(await vm.runInContext(`(${fn.toString()})(argument)`, context)); } };
}
test('synthetic owner/Haru storage probes assert bidirectional isolation and clean keys', async () => {
  const owner = renderer(); const haru = renderer();
  const result = await probeStorageIsolation(owner, haru);
  assert.equal(result.cookies, 'isolated');
  assert.equal(owner.context.localStorage.getItem('__offerpilot_installed_partition_probe'), null);
  assert.equal(haru.context.document.cookie, '');
});
test('shared local storage or cookies fail and cleanup still runs', async () => {
  for (const component of ['localStorage', 'document']) {
    const owner = renderer(); const haru = renderer({ [component]: owner.context[component] });
    await assert.rejects(probeStorageIsolation(owner, haru));
    assert.equal(owner.context.localStorage.getItem('__offerpilot_installed_partition_probe'), null);
    assert.equal(owner.context.document.cookie, '');
  }
});
test('preexisting probe keys are never overwritten or silently accepted', async () => {
  const owner = renderer(); const haru = renderer();
  owner.context.localStorage.setItem('__offerpilot_installed_partition_probe', 'existing');
  await assert.rejects(probeStorageIsolation(owner, haru));
  assert.equal(owner.context.localStorage.getItem('__offerpilot_installed_partition_probe'), 'existing');
});
function networkPage(fetch) { return renderer({ fetch, AbortSignal: { timeout: () => undefined } }); }
test('direct Haru API denial requires transport cancellation with healthy owner before/after', async () => {
  let ownerCalls = 0; let probes = 0;
  const owner = networkPage(async () => { ownerCalls++; return { status: 200 }; });
  const haru = networkPage(async () => { probes++; throw new TypeError('blocked'); });
  assert.equal((await probeHaruApiDeny(owner, haru)).deniedBeforeHttp, 3);
  assert.equal(probes, 3); assert.equal(ownerCalls, 2);
});
test('Haru API success, unauthorized responses, timeouts, or unhealthy owner cannot pass as denial', async () => {
  for (const status of [200, 401, 403, 404, 500]) {
    await assert.rejects(probeHaruApiDeny(networkPage(async () => ({ status: 200 })), networkPage(async () => ({ status }))));
  }
  for (const name of ['TimeoutError', 'AbortError', 'Error']) await assert.rejects(probeHaruApiDeny(networkPage(async () => ({ status: 200 })), networkPage(async () => { throw { name }; })));
  await assert.rejects(probeHaruApiDeny(networkPage(async () => ({ status: 503 })), networkPage(async () => { throw new TypeError(); })));
});
function trayFixture() {
  class Tray extends EventEmitter { isDestroyed() { return false; } setContextMenu(menu) { this.current = menu; } }
  const calls = []; const tray = new Tray();
  const menu = { items: ['打开 OfferPilot', '隐藏 Haru', 'Haru 始终置顶', '退出并停止本地服务'].map(label => ({ label, click: () => calls.push(label) })) };
  return { Tray, tray, menu, calls };
}
test('tray observer preserves real method/callbacks and requires an actual menu refresh', () => {
  const fixture = trayFixture(); const original = fixture.Tray.prototype.setContextMenu;
  try {
    installTrayObserver(fixture);
    assert.throws(() => invokeTrayAction(fixture, 'open'));
    fixture.tray.setContextMenu(fixture.menu);
    assert.equal(fixture.tray.current, fixture.menu);
    invokeTrayAction(fixture, 'open'); invokeTrayAction(fixture, 'hide'); invokeTrayAction(fixture, 'pin');
    assert.deepEqual(fixture.calls, ['打开 OfferPilot', '隐藏 Haru', 'Haru 始终置顶']);
    assert.throws(() => invokeTrayAction(fixture, 'show'));
    assert.throws(() => invokeTrayAction(fixture, 'unknown'));
  } finally { restoreTrayObserver(fixture); }
  assert.equal(fixture.Tray.prototype.setContextMenu, original);
  assert.equal(globalThis.__offerpilotInstalledTray, undefined);
});
test('tray events invoke exactly one production handler and ambiguous menus fail', () => {
  const fixture = trayFixture();
  try {
    installTrayObserver(fixture); fixture.tray.setContextMenu(fixture.menu);
    assert.throws(() => invokeTrayAction(fixture, 'click'));
    fixture.tray.on('click', () => fixture.calls.push('click')); fixture.tray.on('double-click', () => fixture.calls.push('double-click'));
    invokeTrayAction(fixture, 'click'); invokeTrayAction(fixture, 'double-click');
    fixture.tray.on('click', () => {}); assert.throws(() => invokeTrayAction(fixture, 'click'));
    fixture.menu.items.push(fixture.menu.items[0]); assert.throws(() => invokeTrayAction(fixture, 'open'));
    assert.deepEqual(fixture.calls, ['click', 'double-click']);
  } finally { restoreTrayObserver(fixture); }
});
test('queued real tray quit cannot be confused with window hide or an exit assertion', async () => {
  const fixture = trayFixture();
  try {
    installTrayObserver(fixture); fixture.tray.setContextMenu(fixture.menu);
    assert.equal(invokeTrayAction(fixture, 'quit'), true);
    assert.deepEqual(fixture.calls, []);
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.deepEqual(fixture.calls, ['退出并停止本地服务']);
  } finally { restoreTrayObserver(fixture); }
});
function statusFixture(failCapture = false) {
  const original = { version: 3, contextLabel: '实际上下文', taskState: 'idle' };
  let snapshot = original; const states = [];
  const ownerBridge = { role: 'owner', getState: async () => ({ connected: true, snapshot }), publish: value => { snapshot = value; states.push(value.taskState); } };
  const haruBridge = { role: 'haru', getState: ownerBridge.getState, request: () => {} };
  const owner = renderer({ window: { offerpilotDesktop: ownerBridge } });
  const haru = renderer({ window: { offerpilotDesktop: haruBridge } });
  const labels = { idle: '随时待命', running: '正在处理', waiting_confirmation: '等待你确认', completed: '已完成', failed: '需要查看' };
  haru.waitForFunction = async fn => assert.equal(await haru.evaluate(fn), true);
  haru.getByRole = role => ({ waitFor: async () => {}, filter: ({ hasText }) => ({ waitFor: async () => assert.match(labels[snapshot.taskState], hasText) }) });
  return { owner, haru, states, original, current: () => snapshot, capture: async () => { if (failCapture) throw new Error('capture failed'); } };
}
test('status probe exercises every real preload/IPC label then restores original bounded snapshot', async () => {
  const fixture = statusFixture();
  const result = await probeHaruStatusMirror(fixture.owner, fixture.haru, fixture.capture);
  assert.deepEqual(fixture.states, ['idle', 'running', 'waiting_confirmation', 'completed', 'failed', 'idle']);
  assert.equal(fixture.current(), fixture.original);
  assert.equal(result.realAiTaskExecutionProven, false);
  assert.equal(fixture.owner.context.window.__offerpilotInstalledSnapshot, undefined);
});
test('failed status capture still restores owner snapshot, and Haru cannot gain publish capability', async () => {
  const fixture = statusFixture(true);
  await assert.rejects(probeHaruStatusMirror(fixture.owner, fixture.haru, fixture.capture));
  assert.equal(fixture.current(), fixture.original);
  const unsafe = statusFixture(); unsafe.haru.context.window.offerpilotDesktop.publish = () => {};
  await assert.rejects(probeHaruStatusMirror(unsafe.owner, unsafe.haru));
  assert.deepEqual(unsafe.states, []);
});

test('failed native tray menu installation cannot manufacture observed callbacks', () => {
  const fixture = trayFixture();
  fixture.Tray.prototype.setContextMenu = () => { throw new Error('native menu failed'); };
  try {
    installTrayObserver(fixture);
    assert.throws(() => fixture.tray.setContextMenu(fixture.menu), /native menu failed/);
    assert.throws(() => invokeTrayAction(fixture, 'open'), /not observed/);
    assert.deepEqual(fixture.calls, []);
  } finally { restoreTrayObserver(fixture); }
});
