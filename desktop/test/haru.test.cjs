'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { CHANNEL, sanitizeSnapshot, validateRequest, isTrustedFrame, clampBounds, allowHaruResource, authenticatedHeaders } = require('../haru-protocol.cjs');
const { createHaruShell } = require('../haru.cjs');
const origin = 'http://127.0.0.1:4040';
const view = { version: 1, taskState: 'idle', conversationId: 7, messages: [{ role: 'assistant', content: 'hello', confirmation_token: 'secret' }], canSend: true, pending: { confirmation_token: 'secret' }, config: { api_key: 'secret' } };

test('snapshot copies only bounded prose, booleans and theme enum; never credentials or action payloads', () => {
  const value = sanitizeSnapshot({ ...view, error: 'x'.repeat(5000) });
  assert.equal(value.messages[0].content, 'hello');
  assert.equal(value.error.length, 1200);
  assert(!JSON.stringify(value).includes('secret'));
  assert(!('pending' in value));
  for (const animationLevel of ['full', 'minimal', 'off']) assert.equal(sanitizeSnapshot({ ...view, animationLevel }).animationLevel, animationLevel);
  for (const animationLevel of [undefined, true, { level: 'off' }, null, 'invalid']) assert(!('animationLevel' in sanitizeSnapshot({ ...view, animationLevel })));
  for (const aiConfigured of [true, false]) assert.equal(sanitizeSnapshot({ ...view, aiConfigured }).aiConfigured, aiConfigured);
  for (const aiConfigured of [undefined, 'false', { api_key: 'secret' }, null]) assert(!('aiConfigured' in sanitizeSnapshot({ ...view, aiConfigured })));
  assert.equal(sanitizeSnapshot({ ...view, version: NaN }), null);
  assert.equal(sanitizeSnapshot({ ...view, taskState: 'approved' }), null);
  for (const theme of ['dark', 'light']) assert.equal(sanitizeSnapshot({ ...view, theme }).theme, theme);
  for (const theme of [undefined, 'system', '<script>', {}, null]) assert(!('theme' in sanitizeSnapshot({ ...view, theme })));
});
test('IPC has no approval, arbitrary method, or unbounded submission command', () => {
  for (const action of ['approve', 'confirm', 'execute', 'shell', 'selectConversation']) assert.equal(validateRequest({ action, version: 1 }), null);
  assert.equal(validateRequest({ action: 'send', version: 1, text: 'x'.repeat(16001) }), null);
  assert.equal(validateRequest({ action: 'send', version: 1, text: '   ' }), null);
  assert.deepEqual(validateRequest({ action: 'stop', version: 1, confirmation_token: 'secret' }), { action: 'stop', version: 1 });
});
test('IPC verifies exact webContents AND top frame AND exact origin', () => {
  const contents = { isDestroyed: () => false, mainFrame: { url: origin + '/' } };
  assert(isTrustedFrame({ sender: contents, senderFrame: contents.mainFrame }, contents, origin));
  assert(!isTrustedFrame({ sender: contents, senderFrame: { url: origin + '/' } }, contents, origin));
  assert(!isTrustedFrame({ sender: {}, senderFrame: contents.mainFrame }, contents, origin));
  contents.mainFrame.url = origin + '.evil.test/';
  assert(!isTrustedFrame({ sender: contents, senderFrame: contents.mainFrame }, contents, origin));
});
test('Haru resource auth rejects API, encoded routes, methods, query and child frames', () => {
  const request = (route, overrides = {}) => ({ url: origin + route, method: 'GET', resourceType: 'script', ...overrides });
  assert(allowHaruResource(request('/assets/index-a2.js'), origin));
  assert(allowHaruResource(request('/live2d/haru/texture_00.png'), origin));
  assert(allowHaruResource(request('/?desktopSurface=haru', { resourceType: 'mainFrame' }), origin));
  for (const route of ['/api/settings', '/%61pi/settings', '/assets/..%2fapi/settings', '/assets/%2e%2e/api/settings', '/assets/%252e%252e/api/settings', '/assets/index.js?secret=1', '/docs', '/', '/?desktopSurface=owner', '/assets/../api/settings']) assert(!allowHaruResource(request(route), origin), route);
  for (const overrides of [{ method: 'POST' }, { resourceType: 'subFrame' }, { url: 'https://evil.test/assets/a.js' }]) assert(!allowHaruResource(request('/assets/a.js', overrides), origin));
  // Rendering recovery must not add a broad poster/file/network exception.
  for (const url of ['data:image/png;base64,AA==', `blob:${origin}/haru`, 'file:///haru.png', 'https://evil.test/live2d/haru.png']) {
    assert(!allowHaruResource(request('/live2d/haru.png', { url, resourceType: 'image' }), origin));
  }
});
test('dual-session token injection confines owner API and Haru static requests and strips forged outbound headers', () => {
  let destroyed = false;
  const owner = { id: 1, isDestroyed: () => false };
  const mirror = { id: 2, isDestroyed: () => destroyed };
  const trustedContents = new Set([owner, mirror]);
  const base = { origin, token: 'process-only-token', ownerContents: owner, trustedContents };
  const request = (id, route) => ({ webContentsId: id, url: origin + route, method: 'GET', resourceType: 'script', requestHeaders: { 'x-offerpilot-desktop-token': 'forged', Other: 'kept' } });
  const values = (details, role) => Object.values(authenticatedHeaders(details, { ...base, role }));
  assert(values(request(1, '/api/settings'), 'owner').includes('process-only-token'));
  assert(!values(request(2, '/api/settings'), 'owner').includes('process-only-token'));
  assert(!values(request(1, '/assets/a.js'), 'haru').includes('process-only-token'));
  assert(!values(request(99, '/assets/a.js'), 'haru').includes('process-only-token'));
  assert(!values(request(2, '/%61pi/settings'), 'haru').includes('process-only-token'));
  assert(values(request(2, '/assets/a.js'), 'haru').includes('process-only-token'));
  assert(!values({ ...request(1, '/api/settings'), url: 'https://example.com/' }, 'owner').includes('process-only-token'));
  assert(!values({ ...request(2, '/assets/a.js'), url: 'https://example.com/' }, 'haru').includes('forged'));
  destroyed = true;
  assert(!values(request(2, '/assets/a.js'), 'haru').includes('process-only-token'));
});

test('bounds recover on negative-coordinate, removed, scaled and tiny displays', () => {
  const areas = [{ x: 0, y: 0, width: 1920, height: 1040 }, { x: -1280, y: 0, width: 1280, height: 720 }];
  assert.deepEqual(clampBounds({ x: -1200, y: 100, width: 260, height: 340 }, areas), { x: -1200, y: 100, width: 260, height: 340 });
  assert.deepEqual(clampBounds({ x: -1200, y: 100, width: 260, height: 340 }, [areas[0]]), { x: 0, y: 100, width: 260, height: 340 });
  assert.deepEqual(clampBounds({ x: 9999, y: 9999, width: 420, height: 740 }, [{ x: 0, y: 0, width: 320, height: 480 }]), { x: 0, y: 0, width: 320, height: 480 });
  const invalid = clampBounds({ x: NaN, y: Infinity, width: -1, height: null }, areas);
  assert(Object.values(invalid).every(Number.isFinite));
});

function harness(t, { trayFails = false, preferences, folder: existingFolder } = {}) {
  let nextId = 0;
  class Contents extends EventEmitter {
    constructor() { super(); this.id = ++nextId; this.mainFrame = { url: origin + '/' }; this.sent = []; this.destroyed = false; }
    isDestroyed() { return this.destroyed; }
    send(channel, value) { this.sent.push({ channel, value }); }
  }
  class Window extends EventEmitter {
    constructor(options = {}) { super(); this.options = options; this.webContents = new Contents(); this.visible = false; this.bounds = { x: 10, y: 20, width: 260, height: 340, ...options }; this.destroyed = false; this.calls = []; Window.all.push(this); }
    isDestroyed() { return this.destroyed; }
    isVisible() { return this.visible; }
    isMinimized() { return Boolean(this.minimized); }
    restore() { this.minimized = false; this.calls.push('restore'); }
    focus() { this.calls.push('focus'); }
    show() { this.visible = true; this.calls.push('show'); this.emit('show'); }
    hide() { this.visible = false; this.emit('hide'); }
    getBounds() { const { x, y, width, height } = this.bounds; return { x, y, width, height }; }
    setBounds(value) { this.bounds = value; }
    setAlwaysOnTop(value) { this.pinned = value; }
    async loadURL(url) { this.calls.push('loadURL'); this.url = url; this.webContents.mainFrame.url = url; }
    destroy() { this.destroyed = true; this.webContents.destroyed = true; this.webContents.emit('destroyed'); }
  }
  Window.all = [];
  let tray;
  class Tray extends EventEmitter {
    constructor() { super(); if (trayFails) throw new Error('missing tray'); tray = this; }
    isDestroyed() { return Boolean(this.destroyed); }
    destroy() { this.destroyed = true; }
    setToolTip() {}
    setContextMenu(menu) { this.menu = menu; }
  }
  class Ipc extends EventEmitter {
    constructor() { super(); this.handlers = new Map(); }
    handle(name, handler) { this.handlers.set(name, handler); }
    removeHandler(name) { this.handlers.delete(name); }
  }
  const ipc = new Ipc();
  const screen = new EventEmitter();
  screen.getPrimaryDisplay = () => ({ id: 1, workArea: { x: 0, y: 0, width: 1280, height: 800 } });
  screen.getAllDisplays = () => [screen.getPrimaryDisplay()];
  let cursor = { x: 1100, y: 500 };
  screen.getCursorScreenPoint = () => ({ ...cursor });
  const main = new Window();
  const folder = existingFolder || fs.mkdtempSync(path.join(os.tmpdir(), 'haru-shell-'));
  if (preferences !== undefined) fs.writeFileSync(path.join(folder, 'haru-window.json'), preferences);
  const menus = [];
  const Menu = { buildFromTemplate: items => { const menu = { items, popup: options => { menu.options = options; menus.push(menu); } }; return menu; } };
  let quitting = false;
  let quitCalls = 0;
  const registered = [];
  const shell = createHaruShell({ BrowserWindow: Window, Tray, Menu, nativeImage: { createFromPath: value => value }, screen, ipcMain: ipc, mainWindow: main, desktopSession: {}, origin, userData: folder, registerWindow: win => registered.push(win), isQuitting: () => quitting, quit: () => { quitCalls++; quitting = true; } });
  const mirror = Window.all[1];
  mirror.emit('ready-to-show');
  const event = win => ({ sender: win.webContents, senderFrame: win.webContents.mainFrame });
  const invoke = (win, name, value) => ipc.handlers.get(CHANNEL + name)(event(win), value);
  const publish = value => ipc.emit(CHANNEL + 'publish', event(main), value);
  t.after(() => { quitting = true; shell.dispose(); fs.rmSync(folder, { recursive: true, force: true }); });
  return { folder, menus, tray, main, mirror, ipc, event, invoke, publish, shell, registered, screen, setCursor: point => { cursor = point; }, setQuitting: value => { quitting = value; }, quitCalls: () => quitCalls };
}
test('only owner publishes; only mirror sends; stale epochs fail after reload', async t => {
  const h = harness(t);
  assert.equal(h.registered.length, 1);
  assert.equal(h.mirror.options.transparent, true);
  assert.equal(h.mirror.options.webPreferences.sandbox, true);
  h.ipc.emit(CHANNEL + 'publish', h.event(h.mirror), view);
  assert.equal(h.invoke(h.mirror, 'state').connected, false);
  h.publish(view);
  const generation = h.invoke(h.mirror, 'state').generation;
  assert.equal(h.invoke(h.main, 'request', { action: 'send', version: 1, generation, text: 'hello' }).ok, false);
  h.main.webContents.emit('did-start-navigation', {}, origin, false, true);
  assert.equal(h.invoke(h.mirror, 'state').connected, false);
  h.publish(view);
  assert.equal(h.invoke(h.mirror, 'request', { action: 'send', version: 1, generation, text: 'hello' }).reason, 'stale');
});
test('single in-flight IPC delivery, bounded owner ack, and no duplicate stop forwarding', async t => {
  const h = harness(t);
  h.publish(view);
  const request = { action: 'send', version: 1, generation: h.invoke(h.mirror, 'state').generation, text: 'hello' };
  const promise = h.invoke(h.mirror, 'request', request);
  assert.equal(h.invoke(h.mirror, 'request', request).reason, 'busy');
  const command = h.main.webContents.sent.find(item => item.channel === CHANNEL + 'command').value;
  h.ipc.emit(CHANNEL + 'reply', h.event(h.mirror), { id: command.id, result: { ok: true } });
  assert.equal(h.invoke(h.mirror, 'request', request).reason, 'busy');
  h.ipc.emit(CHANNEL + 'reply', h.event(h.main), { id: command.id, result: { ok: true } });
  assert.deepEqual(await promise, { ok: true });
  assert.equal(h.main.webContents.sent.filter(item => item.channel === CHANNEL + 'command').length, 1);
});
test('same-document navigation preserves the owner snapshot and pending acknowledgement', async t => {
  const h = harness(t);
  h.publish(view);
  const generation = h.invoke(h.mirror, 'state').generation;
  const pending = h.invoke(h.mirror, 'request', { action: 'send', version: 1, generation, text: 'hello' });
  const command = h.main.webContents.sent.find(item => item.channel === CHANNEL + 'command').value;
  h.main.webContents.emit('did-start-navigation', {}, origin + '/?view=pilot', true, true);
  const state = h.invoke(h.mirror, 'state');
  assert.equal(state.connected, true);
  assert.equal(state.generation, generation);
  assert.equal(state.snapshot.version, 1);
  assert.equal(h.invoke(h.mirror, 'request', { action: 'stop', version: 1, generation }).reason, 'busy');
  h.ipc.emit(CHANNEL + 'reply', h.event(h.main), { id: command.id, result: { ok: true } });
  assert.deepEqual(await pending, { ok: true });
  h.main.webContents.emit('did-start-navigation', {}, origin, false, true);
  assert.equal(h.invoke(h.mirror, 'state').connected, false);
  assert.equal(h.invoke(h.mirror, 'state').generation, generation + 1);
});
test('owner loss invalidates state and resolves pending commands without replay', async t => {
  const h = harness(t);
  h.publish(view);
  const pending = h.invoke(h.mirror, 'request', { action: 'stop', version: 1, generation: h.invoke(h.mirror, 'state').generation });
  h.main.webContents.emit('render-process-gone');
  assert.equal((await pending).reason, 'unavailable');
  assert.equal(h.invoke(h.mirror, 'state').connected, false);
  assert.equal(h.main.webContents.sent.filter(item => item.channel === CHANNEL + 'command').length, 1);
});
test('close collapses Haru without hiding it or stopping owner; trayless main close quits safely', t => {
  const h = harness(t);
  h.main.show();
  let prevented = 0;
  h.main.emit('close', { preventDefault: () => prevented++ });
  h.mirror.emit('close', { preventDefault: () => prevented++ });
  assert.equal(prevented, 2);
  assert.equal(h.main.isVisible(), false);
  assert.equal(h.mirror.isVisible(), true);
  assert.equal(h.invoke(h.mirror, 'state').expanded, false);
  assert.equal(h.quitCalls(), 0);
  const noTray = harness(t, { trayFails: true });
  noTray.main.emit('close', { preventDefault() {} });
  assert.equal(noTray.quitCalls(), 1);
});
test('hidden mirror skips message broadcasts and receives latest state on show', t => {
  const h = harness(t);
  h.mirror.hide();
  const count = h.mirror.webContents.sent.length;
  h.publish(view);
  h.publish({ ...view, messages: [{ role: 'assistant', content: 'latest' }] });
  assert.equal(h.mirror.webContents.sent.length, count);
  h.shell.showHaru();
  assert.equal(h.mirror.webContents.sent.at(-1).value.snapshot.messages[0].content, 'latest');
});
test('a removed screen reclamps current bounds and pin is an explicit window action', t => {
  const h = harness(t);
  h.mirror.setBounds({ x: -900, y: 999, width: 420, height: 740 });
  h.screen.emit('display-removed');
  assert.deepEqual(h.mirror.getBounds(), { x: 0, y: 60, width: 420, height: 740 });
  assert.equal(h.invoke(h.mirror, 'window', 'toggle-top'), true);
  assert.equal(h.mirror.pinned, false);
  assert.equal(h.invoke(h.mirror, 'window', 'quit'), false);
});

test('Haru opens Pilot from a hidden or minimized main window without replacing its conversation or document', t => {
  const h = harness(t);
  h.publish(view);
  const before = h.invoke(h.mirror, 'state');
  h.main.webContents.mainFrame.url = origin + '/?view=settings';
  h.main.minimized = true;
  assert.equal(h.invoke(h.mirror, 'window', 'show-main'), true);
  assert.deepEqual(h.main.calls, ['restore', 'show', 'focus']);
  assert.equal(h.main.isVisible(), true);
  assert.equal(h.main.webContents.mainFrame.url, origin + '/?view=settings');
  assert.deepEqual(h.main.webContents.sent, [{ channel: CHANNEL + 'show-pilot', value: undefined }]);
  const after = h.invoke(h.mirror, 'state');
  assert.deepEqual(after.snapshot, before.snapshot);
  assert.equal(after.generation, before.generation);
  h.main.hide();
  assert.equal(h.invoke(h.mirror, 'window', 'show-main'), true);
  assert.deepEqual(h.main.calls.slice(-2), ['show', 'focus']);
  assert.equal(h.main.webContents.sent.length, 2);
  assert.equal(h.invoke(h.main, 'window', 'show-main'), true);
  assert.equal(h.main.webContents.sent.length, 2, 'owner focus does not loop back into Pilot navigation');
});

test('Pilot intent during owner startup/reload is delivered once when its trusted snapshot returns', t => {
  const h = harness(t);
  h.invoke(h.mirror, 'window', 'show-main');
  h.invoke(h.mirror, 'window', 'show-main');
  assert.equal(h.main.webContents.sent.length, 0);
  h.ipc.emit(CHANNEL + 'publish', h.event(h.mirror), view);
  assert.equal(h.main.webContents.sent.length, 0);
  h.publish(view);
  h.publish(view);
  assert.equal(h.main.webContents.sent.length, 1);
  h.main.webContents.emit('did-start-navigation', {}, origin, false, true);
  h.invoke(h.mirror, 'window', 'show-main');
  assert.equal(h.main.webContents.sent.length, 1);
  h.publish({ ...view, version: 2 });
  assert.equal(h.main.webContents.sent.length, 2);
});

test('opening Pilot preserves an in-flight mirror command and does not replay its submission', async t => {
  const h = harness(t);
  h.publish(view);
  const request = { action: 'send', version: 1, generation: h.invoke(h.mirror, 'state').generation, text: 'keep this conversation' };
  const pending = h.invoke(h.mirror, 'request', request);
  const command = h.main.webContents.sent[0].value;
  h.invoke(h.mirror, 'window', 'show-main');
  assert.equal(h.invoke(h.mirror, 'request', request).reason, 'busy');
  assert.equal(h.main.webContents.sent.filter(item => item.channel === CHANNEL + 'command').length, 1);
  assert.equal(h.main.webContents.sent.filter(item => item.channel === CHANNEL + 'show-pilot').length, 1);
  h.ipc.emit(CHANNEL + 'reply', h.event(h.main), { id: command.id, result: { ok: true } });
  assert.deepEqual(await pending, { ok: true });
});

test('Pilot notification cannot be sent to an owner that has left the trusted origin', t => {
  const h = harness(t);
  h.publish(view);
  h.main.webContents.mainFrame.url = 'https://hostile.example/';
  assert.equal(h.invoke(h.mirror, 'window', 'show-main'), true);
  assert.deepEqual(h.main.webContents.sent, []);
  h.publish(view);
  assert.deepEqual(h.main.webContents.sent, []);
  h.main.webContents.mainFrame.url = origin + '/';
  h.publish(view);
  assert.deepEqual(h.main.webContents.sent, [{ channel: CHANNEL + 'show-pilot', value: undefined }]);
});

test('hostile senders cannot open Pilot or move Haru; destroyed and quitting owners do not accept navigation', t => {
  const h = harness(t);
  h.publish(view);
  const bounds = h.mirror.getBounds();
  const windowHandler = h.ipc.handlers.get(CHANNEL + 'window');
  const dragHandler = h.ipc.handlers.get(CHANNEL + 'drag');
  const impostor = { isDestroyed: () => false, mainFrame: { url: origin + '/' } };
  for (const event of [
    { sender: impostor, senderFrame: impostor.mainFrame },
    { sender: h.mirror.webContents, senderFrame: { url: origin + '/' } },
  ]) {
    assert.equal(windowHandler(event, 'show-main'), false);
    assert.equal(windowHandler(event, 'show-ai-settings'), false);
    assert.equal(dragHandler(event, 'start'), false);
  }
  h.mirror.webContents.mainFrame.url = 'https://hostile.example/';
  assert.equal(h.invoke(h.mirror, 'window', 'show-main'), false);
  assert.equal(h.invoke(h.mirror, 'drag', 'start'), false);
  h.mirror.webContents.mainFrame.url = origin + '/?desktopSurface=haru';
  assert.deepEqual(h.main.calls, []);
  assert.deepEqual(h.main.webContents.sent, []);
  assert.deepEqual(h.mirror.getBounds(), bounds);
  h.setQuitting(true);
  assert.equal(h.invoke(h.mirror, 'window', 'show-main'), false);
  assert.equal(h.invoke(h.mirror, 'drag', 'start'), false);
  h.setQuitting(false);
  h.main.destroy();
  assert.equal(h.invoke(h.mirror, 'window', 'show-main'), false);
  assert.deepEqual(h.main.calls, []);
});

test('Haru drag uses the actual cursor, ignores click jitter and never expands or changes owner state', t => {
  const h = harness(t);
  h.publish(view);
  h.mirror.setBounds({ x: 100, y: 120, width: 260, height: 340 });
  h.setCursor({ x: 180, y: 200 });
  assert.equal(h.invoke(h.main, 'drag', 'start'), false);
  assert.equal(h.invoke(h.mirror, 'drag', { phase: 'start', x: 1000, y: 1000 }), false);
  assert.equal(h.invoke(h.mirror, 'drag', 'move'), false);
  assert.equal(h.invoke(h.mirror, 'drag', 'start'), true);
  assert.equal(h.invoke(h.mirror, 'drag', 'start'), false, 'duplicate start cannot reset the gesture anchor');
  h.setCursor({ x: 182, y: 201 });
  assert.equal(h.invoke(h.mirror, 'drag', 'move'), true);
  assert.deepEqual(h.mirror.getBounds(), { x: 100, y: 120, width: 260, height: 340 });
  h.setCursor({ x: 240, y: 280 });
  assert.equal(h.invoke(h.mirror, 'drag', 'move'), true);
  assert.deepEqual(h.mirror.getBounds(), { x: 160, y: 200, width: 260, height: 340 });
  h.setCursor({ x: 250, y: 285 });
  assert.equal(h.invoke(h.mirror, 'drag', 'end'), true, 'end applies the final cursor even when a move event was missed');
  assert.deepEqual(h.mirror.getBounds(), { x: 170, y: 205, width: 260, height: 340 });
  h.setCursor({ x: 900, y: 700 });
  assert.equal(h.invoke(h.mirror, 'drag', 'move'), false);
  assert.equal(h.invoke(h.mirror, 'drag', 'end'), false);
  assert.equal(h.invoke(h.mirror, 'state').expanded, false);
  assert.equal(h.invoke(h.mirror, 'state').snapshot.conversationId, view.conversationId);
  assert.deepEqual(h.main.webContents.sent, []);
});

test('drag rejects hidden/outside/invalid cursors and clamps offscreen movement on negative-coordinate monitors', t => {
  const h = harness(t);
  const primary = h.screen.getPrimaryDisplay();
  h.screen.getAllDisplays = () => [primary, { id: 2, workArea: { x: -1280, y: -100, width: 1280, height: 900 } }];
  h.mirror.setBounds({ x: 100, y: 120, width: 260, height: 340 });
  h.setCursor({ x: 0, y: 0 });
  assert.equal(h.invoke(h.mirror, 'drag', 'start'), false);
  h.setCursor({ x: NaN, y: Infinity });
  assert.equal(h.invoke(h.mirror, 'drag', 'start'), false);
  h.setCursor({ x: 180, y: 200 });
  h.mirror.hide();
  assert.equal(h.invoke(h.mirror, 'drag', 'start'), false);
  h.mirror.show();
  assert.equal(h.invoke(h.mirror, 'drag', 'start'), true);
  h.setCursor({ x: -1100, y: 100 });
  h.invoke(h.mirror, 'drag', 'move');
  assert.deepEqual(h.mirror.getBounds(), { x: -1180, y: 20, width: 260, height: 340 });
  h.setCursor({ x: -9999, y: -9999 });
  h.invoke(h.mirror, 'drag', 'end');
  assert.deepEqual(h.mirror.getBounds(), { x: -1280, y: -100, width: 260, height: 340 });
});

test('cancel, blur, hide, resize, navigation and display changes end a drag instead of reusing stale bounds', t => {
  const interruptions = [
    h => h.invoke(h.mirror, 'drag', 'cancel'),
    h => h.mirror.emit('blur'),
    h => h.mirror.hide(),
    h => h.invoke(h.mirror, 'window', 'expand'),
    h => h.mirror.webContents.emit('did-start-navigation', {}, origin, false, true),
    h => h.screen.emit('display-metrics-changed'),
  ];
  for (const interrupt of interruptions) {
    const h = harness(t);
    h.mirror.setBounds({ x: 100, y: 120, width: 260, height: 340 });
    h.setCursor({ x: 180, y: 200 });
    assert.equal(h.invoke(h.mirror, 'drag', 'start'), true);
    h.setCursor({ x: 200, y: 220 });
    h.invoke(h.mirror, 'drag', 'move');
    interrupt(h);
    const bounds = h.mirror.getBounds();
    h.setCursor({ x: 400, y: 500 });
    assert.equal(h.invoke(h.mirror, 'drag', 'move'), false);
    assert.deepEqual(h.mirror.getBounds(), bounds);
  }
});

test('preload exposes drag only to Haru and payload-free Pilot subscription only to owner', async () => {
  const vm = require('node:vm');
  const source = fs.readFileSync(require.resolve('../preload.cjs'), 'utf8');
  for (const role of ['owner', 'haru']) {
    const exposed = {};
    const ipc = new EventEmitter();
    const invoked = [];
    ipc.invoke = (...args) => { invoked.push(args); return Promise.resolve(true); };
    vm.runInNewContext(source, {
      process: { argv: [`--offerpilot-${role}`] },
      require: () => ({ contextBridge: { exposeInMainWorld: (key, value) => { exposed[key] = value; } }, ipcRenderer: ipc }),
    });
    const bridge = exposed.offerpilotDesktop;
    assert.equal(Object.isFrozen(bridge), true);
    assert.equal(bridge.invoke, undefined);
    assert.equal(bridge.send, undefined);
    if (role === 'owner') {
      assert.equal(bridge.dragWindow, undefined);
      let received = 0;
      const off = bridge.onShowPilot(() => { received++; });
      ipc.emit(CHANNEL + 'show-pilot', { sender: 'not exposed' });
      off();
      ipc.emit(CHANNEL + 'show-pilot', {});
      assert.equal(received, 1);
      const offSettings = bridge.onShowAISettings(() => { received++; });
      ipc.emit(CHANNEL + 'show-ai-settings', {});
      offSettings();
      ipc.emit(CHANNEL + 'show-ai-settings', {});
      assert.equal(received, 2);
    } else {
      assert.equal(bridge.onShowPilot, undefined);
      assert.equal(bridge.onShowAISettings, undefined);
      assert.equal(await bridge.dragWindow('start'), true);
      assert.deepEqual(invoked, [[CHANNEL + 'drag', 'start']]);
    }
  }
});

test('AI settings intent restores the owner and is delivered once without changing conversation', t => {
  const h = harness(t);
  h.main.minimized = true;
  assert.equal(h.invoke(h.mirror, 'window', 'show-ai-settings'), true);
  assert.deepEqual(h.main.calls, ['restore', 'show', 'focus']);
  assert.equal(h.main.webContents.sent.length, 0);
  h.publish(view);
  h.publish(view);
  assert.deepEqual(h.main.webContents.sent, [{ channel: CHANNEL + 'show-ai-settings', value: undefined }]);
  assert.equal(h.invoke(h.mirror, 'state').snapshot.conversationId, 7);
  assert.equal(h.invoke(h.mirror, 'window', 'show-ai-settings?url=evil'), false);
});

test('new and malformed preferences default to pinned, only explicit boolean false opts out', t => {
  for (const preferences of [undefined, 'broken', 'null', '{}', '{"alwaysOnTop":"false"}', '{"alwaysOnTop":0}', '{"alwaysOnTop":true}']) {
    const h = harness(t, { preferences });
    assert.equal(h.mirror.options.alwaysOnTop, true, String(preferences));
    assert.equal(h.invoke(h.mirror, 'state').alwaysOnTop, true);
  }
  const h = harness(t, { preferences: '{"alwaysOnTop":false}' });
  assert.equal(h.mirror.options.alwaysOnTop, false);
});
test('character context menu toggles durable pin state and hides with tray recovery in either form', t => {
  const h = harness(t);
  for (const expanded of [true, false, true, false]) {
    h.invoke(h.mirror, 'window', expanded ? 'expand' : 'collapse');
    assert.equal(h.invoke(h.main, 'window', 'context-menu'), false);
    const foreign = { sender: h.mirror.webContents, senderFrame: { url: origin + '/' } };
    assert.equal(h.ipc.handlers.get(CHANNEL + 'window')(foreign, 'context-menu'), false);
    assert.equal(h.invoke(h.mirror, 'window', 'context-menu'), true);
    const menu = h.menus.at(-1);
    assert.equal(menu.options.window, h.mirror);
    assert.equal(menu.items[1].checked, true);
    menu.items[1].click();
    assert.equal(h.mirror.pinned, false);
    assert.equal(JSON.parse(fs.readFileSync(path.join(h.folder, 'haru-window.json'))).alwaysOnTop, false);
    h.invoke(h.mirror, 'window', 'context-menu');
    assert.equal(h.menus.at(-1).items[1].checked, false);
    menu.items[0].click();
    assert.equal(h.mirror.isVisible(), false);
    assert.equal(h.invoke(h.mirror, 'window', 'context-menu'), false);
    assert.equal(h.tray.menu.items[1].label, '显示 Haru');
    if (expanded) h.tray.menu.items[1].click();
    else h.tray.emit('click');
    assert.equal(h.mirror.isVisible(), true);
    assert.equal(h.invoke(h.mirror, 'state').expanded, expanded);
    h.invoke(h.mirror, 'window', 'toggle-top');
  }
  h.invoke(h.mirror, 'window', 'toggle-top');
  h.shell.dispose();
  const restarted = harness(t, { folder: h.folder });
  assert.equal(restarted.mirror.options.alwaysOnTop, false);
});
test('repeated close keeps the character visible, collapses geometry, and allows real shutdown', t => {
  const h = harness(t);
  for (let i = 0; i < 3; i++) {
    h.invoke(h.mirror, 'window', 'expand');
    h.mirror.emit('close', { preventDefault() {} });
    assert.equal(h.invoke(h.mirror, 'state').expanded, false);
    assert.equal(h.mirror.isVisible(), true);
    assert.equal(h.mirror.getBounds().width, 260);
    assert.equal(h.mirror.getBounds().height, 340);
  }
  h.setQuitting(true);
  h.mirror.emit('close', { preventDefault() { assert.fail('must not prevent application shutdown'); } });
});
