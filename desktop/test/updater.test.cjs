'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createUpdater, productionAdapter, registerUpdaterIPC, unavailableReason, CHANNEL } = require('../updater.cjs');
function fixture(overrides = {}) {
  const adapter = new EventEmitter();
  adapter.checkForUpdates = async () => adapter.emit('update-available', {version: '1.2.0', releaseNotes: '<script>plain text only</script>'});
  adapter.downloadUpdate = async () => adapter.emit('download-progress', {percent: 50});
  let installs = 0;
  adapter.quitAndInstall = () => { installs++; };
  const updater = createUpdater({ version: '1.0.0', adapter, prepareInstall: async () => true, performInstall: async install => install(), verifyDownload: async () => {}, ...overrides });
  return {adapter, updater, installs: () => installs};
}
test('manual check, download, install; no implicit download/install', async () => {
  const f = fixture();
  assert.equal(f.adapter.autoDownload, false); assert.equal(f.adapter.autoInstallOnAppQuit, false);
  assert.equal(f.adapter.allowDowngrade, false); assert.equal(f.adapter.allowPrerelease, false); assert.equal(f.adapter.disableWebInstaller, true);
  assert.equal((await f.updater.check()).status, 'available'); assert.equal(f.installs(), 0);
  assert.equal((await f.updater.download()).status, 'downloaded'); assert.equal(f.installs(), 0);
  assert.equal((await f.updater.install()).status, 'installing'); assert.equal(f.installs(), 1);
});
test('unavailable has actual reason and never contacts updater', async () => {
  const f = fixture({unavailable: 'no signed feed'}); let called = false; f.adapter.checkForUpdates = () => {called = true;};
  for (const action of ['check', 'download', 'install']) assert.equal((await f.updater[action]()).status, 'unavailable');
  assert.equal(called, false); assert.ok(unavailableReason({packaged: true, platform: 'win32', policy: null}));
});
test('single-flight rejects duplicate check and out-of-order installation', async () => {
  const f = fixture(); let finish; let count = 0;
  f.adapter.checkForUpdates = () => { count++; return new Promise(resolve => {finish = resolve;}); };
  const one = f.updater.check(); await f.updater.check(); await f.updater.install();
  assert.equal(count, 1); assert.equal(f.installs(), 0); finish(); await one;
});
test('download errors including event-only failure never allow install', async () => {
  const f = fixture(); await f.updater.check(); f.adapter.downloadUpdate = async () => f.adapter.emit('error', Error('checksum'));
  assert.equal((await f.updater.download()).status, 'error'); await f.updater.install(); assert.equal(f.installs(), 0);
});
test('safety cancellation and backup failure prevent installer', async () => {
  const f = fixture({prepareInstall: async () => 'Save your draft'}); await f.updater.check(); await f.updater.download();
  assert.equal((await f.updater.install()).reason, 'Save your draft'); assert.equal(f.installs(), 0);
  const g = fixture({performInstall: async () => { throw Error('disk full'); }}); await g.updater.check(); await g.updater.download();
  assert.equal((await g.updater.install()).status, 'error'); assert.equal(g.installs(), 0);
});
test('installer event-only error rejects performInstall for recovery', async () => {
  let recovered = false;
  const f = fixture({performInstall: async install => { try {await install();} catch(e) { recovered = true; throw e; }}});
  f.adapter.quitAndInstall = () => f.adapter.emit('error', Error('no installer'));
  await f.updater.check(); await f.updater.download(); await f.updater.install(); assert.equal(recovered, true);
});
test('production adapter refuses absent and mismatched signature trust anchor', async () => {
  const policy = {provider:'github',owner:'offercontext',repo:'offerPilot',publisherName:'CN=Trusted Publisher'};
  const adapter = {configOnDisk: {value: Promise.resolve({...policy})}, verifyUpdateCodeSignature() {}};
  assert.equal(await productionAdapter(policy, () => adapter), adapter);
  for (const patch of [{publisherName:undefined}, {publisherName:'Other'}, {repo:'other'}, {token:'not-a-real-token'}, {publisherName:['CN=Trusted Publisher','Other']}]) {
    await assert.rejects(productionAdapter(policy, () => ({...adapter, configOnDisk:{value:Promise.resolve({...policy,...patch})}})));
  }
  await assert.rejects(productionAdapter(null, () => { throw Error('must not load'); }));
});
test('IPC rejects Haru, child frames, foreign origin and shutdown', async () => {
  const handlers = new Map(); const ipcMain = {handle:(name, fn) => handlers.set(name,fn), removeHandler:name=>handlers.delete(name)};
  const contents = {isDestroyed:()=>false,mainFrame:{url:'http://127.0.0.1:9999/'},send(){}};
  const window = {webContents:contents,isDestroyed:()=>false}; const f = fixture(); let quitting = false;
  const off = registerUpdaterIPC({ipcMain, window, origin:'http://127.0.0.1:9999', updater:f.updater,isQuitting:()=>quitting});
  const state = handlers.get(CHANNEL+'state'); const trusted = {sender:contents,senderFrame:contents.mainFrame};
  assert.equal(state(trusted).currentVersion,'1.0.0'); assert.equal(state({...trusted,sender:{}}),null);
  assert.equal(state({...trusted,senderFrame:{url:contents.mainFrame.url}}),null);
  contents.mainFrame.url = 'https://example.com/'; assert.equal(state(trusted),null);
  contents.mainFrame.url = 'http://127.0.0.1:9999/'; quitting = true; assert.equal(state(trusted),null); off(); assert.equal(handlers.size,0);
});
test('async installer event invokes recovery and verifies final cached asset again', async () => {
  let recovered=0, verified=0;
  const f=fixture({verifyDownload:async()=>{verified++;},onInstallError:()=>{recovered++;}});
  f.adapter.quitAndInstall=()=>queueMicrotask(()=>f.adapter.emit('error',Error('spawn failed')));
  await f.updater.check();await f.updater.download();await f.updater.install();
  assert.equal(verified,2);assert.equal(recovered,1);assert.equal(f.updater.getState().status,'error');
});
test('Haru preload has no update operations; owner has only finite operations', () => {
  const vm=require('node:vm'); const fs=require('node:fs'); const source=fs.readFileSync(require.resolve('../preload.cjs'),'utf8');
  for(const role of ['haru','owner']){
    const exposed={}; vm.runInNewContext(source,{process:{argv:[`--offerpilot-${role}`]},require:()=>({contextBridge:{exposeInMainWorld:(key,value)=>{exposed[key]=value;}},ipcRenderer:{}})});
    if(role==='haru') assert.equal(exposed.offerpilotUpdates,undefined);
    else assert.deepEqual(Object.keys(exposed.offerpilotUpdates).sort(),['check','download','getState','install','onPrepareInstall','onState','replyPrepareInstall'].sort());
  }
});
test('missing authoritative update response is not reported as latest',async()=>{
  const f=fixture();f.adapter.checkForUpdates=async()=>null;
  assert.equal((await f.updater.check()).status,'error');
});
test('late network error during shutdown is harmless and cannot trigger recovery',()=>{
  let recovered=0;const f=fixture({onInstallError:()=>{recovered++;}});f.updater.dispose();
  assert.doesNotThrow(()=>f.adapter.emit('error',Error('socket closed')));assert.equal(recovered,0);
});
