'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { CHANNEL, createLocaleStore, registerLocaleIPC, applicationMenu, startupErrorText, translate } = require('../locale.cjs');
const english = require('../locale-catalog.cjs');
const folder = t => { const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'desktop-locale-')); t.after(() => fs.rmSync(dir, { recursive: true, force: true })); return dir; };
test('native locale defaults Chinese, validates strictly, persists and restores on restart', t => {
  const dir = folder(t); const store = createLocaleStore(dir); const values = []; store.subscribe(value => values.push(value));
  assert.equal(store.getLocale(), 'zh-CN');
  for (const value of ['en', 'en-GB', 'ZH-CN', {}, null, true, ['en-US']]) assert.equal(store.setLocale(value), null);
  assert.equal(store.setLocale('en-US'), 'en-US');
  assert.equal(store.t('复制'), 'Copy');
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, 'desktop-locale.json'))), { locale: 'en-US' });
  assert.equal(createLocaleStore(dir).getLocale(), 'en-US');
  assert.equal(store.setLocale('zh-CN'), 'zh-CN');
  assert.equal(store.t('复制'), '复制');
  assert.deepEqual(values, ['en-US', 'zh-CN']);
});
test('corrupt or oversized preference safely defaults Chinese and is repaired by explicit selection', t => {
  const dir = folder(t); const file = path.join(dir, 'desktop-locale.json');
  for (const value of ['oops', '{"locale":"fr-FR"}', 'x'.repeat(1025)]) {
    fs.writeFileSync(file, value); const store = createLocaleStore(dir);
    assert.equal(store.getLocale(), 'zh-CN');
    store.setLocale('zh-CN');
    assert.deepEqual(JSON.parse(fs.readFileSync(file)), { locale: 'zh-CN' });
  }
});
test('failed disk persistence does not change state or broadcast a false success', t => {
  const dir = folder(t); const store = createLocaleStore(dir); const values = []; store.subscribe(value => values.push(value));
  fs.mkdirSync(path.join(dir, 'desktop-locale.json'));
  assert.throws(() => store.setLocale('en-US'), /无法保存语言/);
  assert.equal(store.getLocale(), 'zh-CN'); assert.deepEqual(values, []);
  assert.deepEqual(fs.readdirSync(dir), ['desktop-locale.json']);
});
test('locale IPC isolates owner writes and synchronizes independent trusted windows', t => {
  const origin = 'http://127.0.0.1:4040';
  const contents = () => ({ mainFrame: { url: origin + '/' }, isDestroyed: () => false, sent: [], send(channel, value) { this.sent.push({ channel, value }); } });
  const owner = { webContents: contents() }; let mirror = contents();
  const store = createLocaleStore(folder(t)); const handlers = new Map(); let quitting = false;
  const dispose = registerLocaleIPC({ ipcMain: { handle: (name, fn) => handlers.set(name, fn), removeHandler: name => handlers.delete(name) }, owner, getHaruContents: () => mirror, origin, store, isQuitting: () => quitting });
  const event = c => ({ sender: c, senderFrame: c.mainFrame }); const get = e => handlers.get(CHANNEL + 'get')(e); const set = (e, value) => handlers.get(CHANNEL + 'set')(e, value);
  assert.equal(get(event(mirror)), 'zh-CN');
  assert.equal(set(event(mirror), 'en-US'), null);
  assert.equal(set({ sender: owner.webContents, senderFrame: { url: origin + '/' } }, 'en-US'), null);
  assert.equal(set(event(contents()), 'en-US'), null);
  assert.equal(set(event(owner.webContents), { locale: 'en-US' }), null);
  assert.equal(set(event(owner.webContents), 'en-US'), 'en-US');
  assert.deepEqual(mirror.sent, [{ channel: CHANNEL + 'changed', value: 'en-US' }]);
  assert.equal(get(event(mirror)), 'en-US');
  const old = mirror; mirror = contents();
  assert.equal(get(event(old)), null); assert.equal(get(event(mirror)), 'en-US');
  mirror.mainFrame.url = 'https://untrusted.example/';
  set(event(owner.webContents), 'zh-CN'); assert.deepEqual(mirror.sent, []); assert.equal(get(event(mirror)), null);
  owner.webContents.mainFrame.url = origin + '.evil.test/';
  assert.equal(set(event(owner.webContents), 'en-US'), null);
  owner.webContents.mainFrame.url = origin + '/'; quitting = true;
  assert.equal(get(event(owner.webContents)), null); assert.equal(set(event(owner.webContents), 'en-US'), null);
  dispose(); assert.equal(handlers.size, 0);
});
test('preload exposes locale writes only to owner, without generic IPC or event objects', () => {
  for (const role of ['owner', 'haru']) {
    const apis = {}; const listeners = new Map(); const invokes = [];
    const ipc = { invoke: (...args) => invokes.push(args), on: (name, fn) => listeners.set(name, fn), removeListener: name => listeners.delete(name) };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preload.cjs'), 'utf8'), { require: () => ({ contextBridge: { exposeInMainWorld: (name, api) => apis[name] = api }, ipcRenderer: ipc }), process: { argv: [`--offerpilot-${role}`] } });
    assert.equal(typeof apis.offerpilotLocale.setLocale, role === 'owner' ? 'function' : 'undefined');
    assert.equal(apis.offerpilotLocale.send, undefined); assert.equal(apis.offerpilotLocale.invoke, undefined);
    let received; const off = apis.offerpilotLocale.onLocale(value => received = value);
    listeners.get(CHANNEL + 'changed')({ sender: 'private' }, 'en-US'); assert.equal(received, 'en-US'); off(); assert(!listeners.has(CHANNEL + 'changed'));
    apis.offerpilotLocale.getLocale(); assert.deepEqual(invokes[0], [CHANNEL + 'get']);
  }
});
test('application menu and startup diagnostics follow locale without changing roles', () => {
  const menu = locale => applicationMenu({ t: source => translate(source, locale), showMain() {}, quit() {} });
  assert.equal(menu('zh-CN')[0].label, '文件'); assert.equal(menu('en-US')[0].label, 'File');
  assert.equal(menu('en-US')[1].submenu[0].role, 'undo');
  assert(!JSON.stringify(menu('en-US')).includes('toggleDevTools'));
  assert.equal(startupErrorText(new Error('Backend exited before startup (1).'), (s, v) => translate(s, 'en-US', v)), 'The local service exited before startup (1).');
  assert(!startupErrorText(new Error('technical details'), s => translate(s)).includes('technical details'));
  for (const [source, translated] of Object.entries(english)) { assert.equal(typeof translated, 'string'); assert(translated.length > 0, source); assert(!/[\u4e00-\u9fff]/.test(translated), source); }
});

test('failed observer cannot suppress another window or report a saved value as failed', t => {
  const store = createLocaleStore(folder(t)); const seen = [];
  store.subscribe(() => { throw Error('window closed'); });
  store.subscribe(locale => seen.push(locale));
  assert.equal(store.setLocale('en-US'), 'en-US'); assert.deepEqual(seen, ['en-US']);
});
