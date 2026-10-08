import assert from 'node:assert/strict';

// Observe real menu installation without replacing any product callback or guard.
// The retained tray/menu stay in the inspected main process, never in evidence.
export function installTrayObserver({ Tray }) {
  if (globalThis.__offerpilotInstalledTray) throw new Error('tray observer already installed');
  const original = Tray.prototype.setContextMenu;
  const state = { original, tray: null, menu: null };
  const observe = function (menu) {
    const labels = menu?.items?.map(item => item.label) || [];
    const matches = labels.includes('打开 OfferPilot') && labels.includes('退出并停止本地服务');
    if (matches && state.tray && state.tray !== this) throw new Error('multiple application trays');
    const result = original.call(this, menu);
    // A failed OS menu installation must not manufacture callable tray evidence.
    if (matches) { state.tray = this; state.menu = menu; }
    return result;
  };
  state.observe = observe;
  globalThis.__offerpilotInstalledTray = state;
  Tray.prototype.setContextMenu = observe;
}
export function restoreTrayObserver({ Tray }) {
  const state = globalThis.__offerpilotInstalledTray;
  if (!state) return;
  if (Tray.prototype.setContextMenu !== state.observe) throw new Error('tray observer was replaced');
  Tray.prototype.setContextMenu = state.original;
  delete globalThis.__offerpilotInstalledTray;
}
export function invokeTrayAction(_electron, action) {
  const state = globalThis.__offerpilotInstalledTray;
  if (!state?.tray || state.tray.isDestroyed()) throw new Error('real application tray not observed');
  if (action === 'click' || action === 'double-click') {
    if (state.tray.listenerCount(action) !== 1) throw new Error('ambiguous tray event callback');
    state.tray.emit(action);
    return true;
  }
  const labels = { open: '打开 OfferPilot', hide: '隐藏 Haru', show: '显示 Haru', pin: 'Haru 始终置顶', quit: '退出并停止本地服务' };
  const matches = state.menu.items.filter(item => item.label === labels[action]);
  if (!labels[action] || matches.length !== 1 || typeof matches[0].click !== 'function'
    || matches[0].enabled === false || matches[0].visible === false) throw new Error('exact enabled tray callback required');
  // Quit is queued after the inspector reply. Only independent OS process/port
  // disappearance can subsequently prove exit; callback delivery alone cannot.
  if (action === 'quit') setTimeout(() => matches[0].click(matches[0], undefined, {}), 0);
  else matches[0].click(matches[0], undefined, {});
  return true;
}
export async function probeStorageIsolation(owner, haru) {
  const key = '__offerpilot_installed_partition_probe';
  const read = key => ({ local: localStorage.getItem(key), session: sessionStorage.getItem(key),
    cookie: document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith(`${key}=`))?.slice(key.length + 1) ?? null });
  const clean = key => { localStorage.removeItem(key); sessionStorage.removeItem(key); document.cookie = `${key}=; Max-Age=0; Path=/; SameSite=Strict`; };
  assert.deepEqual(await owner.evaluate(read, key), { local: null, session: null, cookie: null }, 'probe key must be fresh');
  assert.deepEqual(await haru.evaluate(read, key), { local: null, session: null, cookie: null }, 'probe key must be fresh');
  try {
    await owner.evaluate(key => { localStorage.setItem(key, 'owner'); sessionStorage.setItem(key, 'owner'); document.cookie = `${key}=owner; Path=/; SameSite=Strict`; }, key);
    assert.deepEqual(await owner.evaluate(read, key), { local: 'owner', session: 'owner', cookie: 'owner' });
    assert.deepEqual(await haru.evaluate(read, key), { local: null, session: null, cookie: null }, 'Haru can read owner storage');
    await haru.evaluate(key => { localStorage.setItem(key, 'haru'); sessionStorage.setItem(key, 'haru'); document.cookie = `${key}=haru; Path=/; SameSite=Strict`; }, key);
    assert.deepEqual(await haru.evaluate(read, key), { local: 'haru', session: 'haru', cookie: 'haru' });
    assert.deepEqual(await owner.evaluate(read, key), { local: 'owner', session: 'owner', cookie: 'owner' }, 'Haru can overwrite owner storage');
    return { localStorage: 'isolated', sessionStorage: 'isolated', cookies: 'isolated', syntheticKeysOnly: true };
  } finally { await Promise.all([owner.evaluate(clean, key), haru.evaluate(clean, key)]); }
}
export async function probeHaruApiDeny(owner, haru) {
  assert.equal(await owner.evaluate(async () => (await fetch('/api/health')).status), 200, 'owner backend must be reachable before deny probes');
  const probes = ['/api/health', '/api/applications', '/api/config'];
  for (const target of probes) {
    const result = await haru.evaluate(async target => {
      try { const response = await fetch(target, { signal: AbortSignal.timeout(5000) }); return { blocked: false, status: response.status }; }
      catch (error) { return { blocked: error.name === 'TypeError' }; }
    }, target);
    assert.deepEqual(result, { blocked: true }, 'Haru API must be cancelled before an HTTP response, not merely unauthenticated');
  }
  assert.equal(await owner.evaluate(async () => (await fetch('/api/health')).status), 200, 'backend unavailable cannot prove Haru isolation');
  return { deniedBeforeHttp: probes.length, ownerHealthBeforeAndAfter: 200, methods: ['GET'], mutationsAttempted: false };
}
export async function probeHaruStatusMirror(owner, haru, capture) {
  await haru.getByRole('main', { name: 'Haru 桌面小窗', exact: true }).waitFor();
  await haru.waitForFunction(async () => {
    const state = await window.offerpilotDesktop?.getState();
    return state?.connected === true && state.snapshot?.version >= 1;
  });
  const roles = await Promise.all([owner, haru].map(page => page.evaluate(() => ({ role: window.offerpilotDesktop.role,
    publish: typeof window.offerpilotDesktop.publish, request: typeof window.offerpilotDesktop.request,
    genericIpc: ['send', 'invoke', 'ipcRenderer'].some(key => key in window.offerpilotDesktop) }))));
  assert.deepEqual(roles, [{ role: 'owner', publish: 'function', request: 'undefined', genericIpc: false },
    { role: 'haru', publish: 'undefined', request: 'function', genericIpc: false }]);
  await owner.evaluate(async () => {
    if (window.__offerpilotInstalledSnapshot) throw new Error('snapshot probe already running');
    window.__offerpilotInstalledSnapshot = (await window.offerpilotDesktop.getState()).snapshot;
  });
  const cases = [['idle', '随时待命'], ['running', '正在处理'], ['waiting_confirmation', '等待你确认'], ['completed', '已完成'], ['failed', '需要查看']];
  try {
    for (const [index, [taskState, label]] of cases.entries()) {
      await owner.evaluate(({ taskState, index }) => window.offerpilotDesktop.publish({
        version: window.__offerpilotInstalledSnapshot.version + index + 1, taskState, conversationId: null,
        messages: [], contextLabel: '安装验证合成状态', loading: taskState === 'running',
        hasPending: taskState === 'waiting_confirmation', canSend: false, canStop: false, stopping: false,
        error: '', stopMessage: '',
      }), { taskState, index });
      await haru.getByRole('status').filter({ hasText: new RegExp(`^${label}$`) }).waitFor();
      assert.equal(await haru.evaluate(async expected => (await window.offerpilotDesktop.getState()).snapshot.taskState === expected, taskState), true);
    }
    if (capture) await capture('synthetic-status-failed', haru);
  } finally {
    await owner.evaluate(() => { window.offerpilotDesktop.publish(window.__offerpilotInstalledSnapshot); delete window.__offerpilotInstalledSnapshot; });
  }
  await haru.waitForFunction(async () => (await window.offerpilotDesktop.getState()).snapshot?.contextLabel !== '安装验证合成状态');
  return { labels: cases.map(([state]) => state), source: 'synthetic-bounded-owner-preload-publish',
    rendererControllerOrBackendMocked: false, realAiTaskExecutionProven: false, originalSnapshotRestored: true };
}
