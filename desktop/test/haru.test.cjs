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

test('snapshot copies only bounded prose and booleans; never credentials or action payloads', () => {
  const value = sanitizeSnapshot({ ...view, error: 'x'.repeat(5000) });
  assert.equal(value.messages[0].content, 'hello');
  assert.equal(value.error.length, 1200);
  assert(!JSON.stringify(value).includes('secret'));
  assert(!('pending' in value));
  assert.equal(sanitizeSnapshot({ ...view, version: NaN }), null);
  assert.equal(sanitizeSnapshot({ ...view, taskState: 'approved' }), null);
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

function harness(t, { trayFails = false } = {}) {
  let nextId = 0;
  class Contents extends EventEmitter {
    constructor() { super(); this.id = ++nextId; this.mainFrame = { url: origin + '/' }; this.sent = []; this.destroyed = false; }
    isDestroyed() { return this.destroyed; }
    send(channel, value) { this.sent.push({ channel, value }); }
  }
  class Window extends EventEmitter {
    constructor(options = {}) { super(); this.options = options; this.webContents = new Contents(); this.visible = false; this.bounds = { x: 10, y: 20, width: 260, height: 340, ...options }; this.destroyed = false; Window.all.push(this); }
    isDestroyed() { return this.destroyed; }
    isVisible() { return this.visible; }
    isMinimized() { return false; }
    restore() {}
    focus() {}
    show() { this.visible = true; this.emit('show'); }
    hide() { this.visible = false; this.emit('hide'); }
    getBounds() { const { x, y, width, height } = this.bounds; return { x, y, width, height }; }
    setBounds(value) { this.bounds = value; }
    setAlwaysOnTop(value) { this.pinned = value; }
    async loadURL(url) { this.url = url; this.webContents.mainFrame.url = url; }
    destroy() { this.destroyed = true; this.webContents.destroyed = true; this.webContents.emit('destroyed'); }
  }
  Window.all = [];
  class Tray extends EventEmitter {
    constructor() { super(); if (trayFails) throw new Error('missing tray'); }
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
  const main = new Window();
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'haru-shell-'));
  let quitting = false;
  let quitCalls = 0;
  const registered = [];
  const shell = createHaruShell({ BrowserWindow: Window, Tray, Menu: { buildFromTemplate: value => value }, nativeImage: { createFromPath: value => value }, screen, ipcMain: ipc, mainWindow: main, desktopSession: {}, origin, userData: folder, registerWindow: win => registered.push(win), isQuitting: () => quitting, quit: () => { quitCalls++; quitting = true; } });
  const mirror = Window.all[1];
  mirror.emit('ready-to-show');
  const event = win => ({ sender: win.webContents, senderFrame: win.webContents.mainFrame });
  const invoke = (win, name, value) => ipc.handlers.get(CHANNEL + name)(event(win), value);
  const publish = value => ipc.emit(CHANNEL + 'publish', event(main), value);
  t.after(() => { quitting = true; shell.dispose(); fs.rmSync(folder, { recursive: true, force: true }); });
  return { main, mirror, ipc, event, invoke, publish, shell, registered, screen, quitCalls: () => quitCalls };
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
test('close hides both windows without stopping owner; trayless main close quits safely', t => {
  const h = harness(t);
  h.main.show();
  let prevented = 0;
  h.main.emit('close', { preventDefault: () => prevented++ });
  h.mirror.emit('close', { preventDefault: () => prevented++ });
  assert.equal(prevented, 2);
  assert.equal(h.main.isVisible(), false);
  assert.equal(h.mirror.isVisible(), false);
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
  assert.equal(h.mirror.pinned, true);
  assert.equal(h.invoke(h.mirror, 'window', 'quit'), false);
});
