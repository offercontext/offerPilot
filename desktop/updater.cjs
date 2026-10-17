'use strict';
const { strictSignatureVerifier } = require('./update-signature.cjs');
const { isTrustedFrame } = require('./haru-protocol.cjs');
const CHANNEL = 'offerpilot:updates:';
// Activation requires an approved, signed release channel. Never take a feed,
// publisher, installer path or updater options from renderer/user preferences.
const RELEASE_POLICY = null;
function unavailableReason({ packaged, platform, policy }) {
  if (!packaged) return '开发环境不安装更新，请使用正式签名的 Windows 安装版。';
  if (platform !== 'win32') return '此更新入口仅支持 Windows 安装版。';
  if (!policy) return '此验证包尚未配置正式签名更新源。';
  return null;
}
async function productionAdapter(policy, load = () => require('electron-updater').autoUpdater) {
  if (!policy || policy.provider !== 'github' || policy.owner !== 'offercontext' || policy.repo !== 'offerPilot'
    || typeof policy.publisherName !== 'string' || !policy.publisherName.startsWith('CN=')) throw new Error('Unapproved release policy');
  const adapter = load();
  // Pinned electron-updater 6.8.9 skips signature checks without publisherName.
  // Validate its actual packaged config before any network request. Do not use
  // setFeedURL, which could diverge from the config supplying the trust anchor.
  const config = await adapter.configOnDisk.value;
  const publishers = Array.isArray(config.publisherName) ? config.publisherName : [config.publisherName];
  if (config.provider !== policy.provider || config.owner !== policy.owner || config.repo !== policy.repo
    || config.token || config.private || config.url || (config.host && config.host !== 'github.com') || (config.protocol && config.protocol !== 'https') || publishers.length !== 1 || publishers[0] !== policy.publisherName
    || typeof adapter.verifyUpdateCodeSignature !== 'function') throw new Error('Signed update configuration is missing or mismatched');
  adapter.verifyUpdateCodeSignature = strictSignatureVerifier(policy.publisherName);
  return adapter;
}
function createUpdater({ version, adapter, unavailable, prepareInstall, performInstall, verifyDownload = async () => { throw new Error("No integrity verifier"); }, onInstallError = () => {} }) {
  let state = { currentVersion: version, status: unavailable ? 'unavailable' : 'idle', ...(unavailable ? { reason: unavailable } : {}) };
  const listeners = new Set();
  let busy = false;
  let downloaded = false;
  let availableInfo = null;
  let checkOutcome = null;
  let downloadedPaths = null;
  let installStarted = false;
  let disposed = false;
  let errors = 0;
  const handlers = [];
  const getState = () => ({ ...state });
  const publish = patch => {
    if (disposed) return;
    state = { ...state, reason: undefined, ...patch };
    for (const fn of listeners) fn(getState());
  };
  const fail = () => { errors++; publish({ status: 'error', reason: '更新未完成。请检查网络或稍后重试；若安装已经启动，请核实安装结果。' }); };
  if (adapter && !unavailable) {
    adapter.autoDownload = false;
    adapter.autoInstallOnAppQuit = false;
    adapter.allowPrerelease = false;
    adapter.allowDowngrade = false;
    adapter.disableWebInstaller = true;
    const on = (name, fn) => { adapter.on(name, fn); handlers.push([name, fn]); };
    on('error', () => { if (disposed) return; fail(); if (installStarted) onInstallError(); });
    on('update-available', info => { availableInfo = info; checkOutcome = 'available'; });
    on('update-not-available', () => { availableInfo = null; checkOutcome = 'current'; });
    on('download-progress', p => { if (state.status === 'downloading') publish({ percent: Math.max(0, Math.min(100, Number(p.percent) || 0)) }); });
  }
  async function run(action) {
    if (disposed || unavailable || busy || !adapter) return getState();
    busy = true;
    const previousErrors = errors;
    try {
      if (action === 'check') {
        downloaded = false;
        availableInfo = null;
        checkOutcome = null;
        publish({ status: 'checking', version: undefined, releaseNotes: undefined, percent: undefined });
        await adapter.checkForUpdates();
        // Update availability must come from the updater's semver decision,
        // never from a fabricated version or lexicographic string comparison.
        if (errors !== previousErrors || checkOutcome === null) throw new Error('Update check did not establish a result');
        if (!availableInfo) publish({ status: 'idle', reason: '当前已是此发布渠道的最新版本。' });
        else {
          const info = availableInfo;
          publish({ status: 'available', version: String(info.version).slice(0, 100), releaseNotes: typeof info.releaseNotes === 'string' ? info.releaseNotes.slice(0, 20000) : '' });
        }
      } else if (action === 'download' && state.status === 'available') {
        publish({ status: 'downloading', percent: 0 });
        downloadedPaths = await adapter.downloadUpdate();
        await verifyDownload(downloadedPaths, availableInfo);
        if (errors !== previousErrors) throw new Error('Update download failed');
        downloaded = true;
        publish({ status: 'downloaded', percent: 100 });
      } else if (action === 'install' && downloaded && state.status === 'downloaded') {
        const result = await prepareInstall();
        if (result !== true) publish({ status: 'downloaded', reason: typeof result === 'string' ? result : '请先保存草稿并处理运行中任务和待审批操作，再重试更新。' });
        else {
          publish({ status: 'installing' });
          await performInstall(async () => {
            await verifyDownload(downloadedPaths, availableInfo);
            installStarted = true;
            adapter.quitAndInstall(false, true);
            if (errors !== previousErrors) throw new Error('Installer could not start');
          });
        }
      }
    } catch { fail(); }
    finally { busy = false; }
    return getState();
  }
  return {
    getState, check: () => run('check'), download: () => run('download'), install: () => run('install'),
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    dispose() {
      disposed = true; listeners.clear();
      // Network errors can arrive while ordinary app exit drains the backend.
      // Keep a harmless error listener until process exit, avoiding an uncaught
      // EventEmitter error that could interrupt graceful shutdown.
      for (const [event, fn] of handlers) if (event !== 'error') adapter.removeListener(event, fn);
    },
  };
}
function registerUpdaterIPC({ ipcMain, window, origin, updater, isQuitting }) {
  const trusted = e => !isQuitting() && isTrustedFrame(e, window.webContents, origin);
  for (const [channel, method] of [['state', 'getState'], ['check', 'check'], ['download', 'download'], ['install', 'install']]) {
    ipcMain.handle(CHANNEL + channel, e => trusted(e) ? updater[method]() : null);
  }
  const off = updater.subscribe(state => {
    if (!window.isDestroyed() && isTrustedFrame({ sender: window.webContents, senderFrame: window.webContents.mainFrame }, window.webContents, origin)) window.webContents.send(CHANNEL + 'state', state);
  });
  return () => { off(); for (const action of ['state', 'check', 'download', 'install']) ipcMain.removeHandler(CHANNEL + action); };
}
module.exports = { CHANNEL, RELEASE_POLICY, unavailableReason, productionAdapter, createUpdater, registerUpdaterIPC };
