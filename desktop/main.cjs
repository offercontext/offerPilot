'use strict';
const { app, BrowserWindow, dialog, session, shell, Tray, Menu, nativeImage, screen, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { allowHaruResource, authenticatedHeaders } = require('./haru-protocol.cjs');
const { createHaruShell } = require('./haru.cjs');
const { createCapabilities } = require('./capabilities.cjs');
const { RELEASE_POLICY, unavailableReason, productionAdapter, createUpdater, registerUpdaterIPC } = require('./updater.cjs');
const { createInstallSafety } = require('./update-safety.cjs');
const { backupForUpdate } = require('./update-backup.cjs');
const { verifyDownloadedUpdate } = require('./update-integrity.cjs');
const { stopBackendForUpdate } = require('./update-install.cjs');
const { isSameOrigin, waitForReady, checkHealth, stopBackend } = require('./lifecycle.cjs');

app.setName('OfferPilot Desktop');
app.setPath('userData', path.join(app.getPath('appData'), 'OfferPilot Desktop'));
let child;
let window;
let haruShell;
let updateLock = false;
let disposeUpdates = () => {};
const trustedContents = new Set();
const isTrustedContents = contents => trustedContents.has(contents) && !contents.isDestroyed();
let quitting = false;
let stopped = false;
let logFile;

function log(message) {
  if (!logFile) return;
  try {
    if (fs.existsSync(logFile) && fs.statSync(logFile).size > 1024 * 1024) {
      fs.renameSync(logFile, `${logFile}.previous`);
    }
    fs.appendFileSync(logFile, message);
  } catch { /* A log failure must not suppress startup errors. */ }
}
function storedPort(filename) {
  if (!fs.existsSync(filename)) return 0;
  const port = JSON.parse(fs.readFileSync(filename, 'utf8')).port;
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid saved desktop port. Restore desktop-port.json from backup.');
  return port;
}
async function start() {
  const userData = app.getPath('userData');
  fs.mkdirSync(userData, { recursive: true });
  logFile = path.join(userData, 'desktop.log');
  const dataDir = path.join(userData, 'data');
  const portFile = path.join(userData, 'desktop-port.json');
  const port = storedPort(portFile);
  const repo = path.resolve(__dirname, '..');
  const staticDir = app.isPackaged ? path.join(process.resourcesPath, 'web') : path.join(repo, 'web', 'dist');
  const executable = app.isPackaged
    ? path.join(process.resourcesPath, 'backend', process.platform === 'win32' ? 'offerpilot-backend.exe' : 'offerpilot-backend')
    : path.join(repo, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  if (!fs.existsSync(executable) || !fs.existsSync(path.join(staticDir, 'index.html'))) {
    throw new Error('Backend or web assets are missing. Rebuild or reinstall the validation package.');
  }
  const token = randomBytes(32).toString('hex');
  const args = [...(app.isPackaged ? [] : ['-m', 'offerpilot.desktop']), '--data-dir', dataDir, '--static-dir', staticDir, '--port', String(port)];
  child = spawn(executable, args, {
    cwd: userData,
    env: { ...process.env, OFFERPILOT_DESKTOP_TOKEN: token, PYTHONUNBUFFERED: '1' },
    windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
  });
  child.stderr.on('data', (chunk) => log(chunk.toString().split(token).join('[redacted]')));
  // Prevent unhandled stream errors during a simultaneous shutdown/start failure.
  child.stdin.on('error', () => {});
  child.once('exit', (code, signal) => {
    log(`Backend exited: code=${code} signal=${signal}\n`);
    if (!quitting && window) {
      dialog.showErrorBox('OfferPilot Desktop', 'The local backend stopped. Close and reopen OfferPilot. Your saved data remains in the desktop data folder.');
      app.quit();
    }
  });
  const ready = await waitForReady(child, 60000, {
    allowPythonRedirector: !app.isPackaged && process.platform === 'win32',
  });
  await checkHealth(ready.origin, token);
  if (quitting) return;
  if (port && Number(new URL(ready.origin).port) !== port) throw new Error('Backend changed the saved port unexpectedly.');
  if (!port) {
    fs.writeFileSync(`${portFile}.tmp`, `${JSON.stringify({ port: Number(new URL(ready.origin).port) })}\n`);
    fs.renameSync(`${portFile}.tmp`, portFile);
  }
  const desktopSession = session.fromPartition('persist:offerpilot-desktop', { cache: false });
  // An ephemeral, separate partition keeps Haru away from owner storage, auth
  // cookies and BroadcastChannel, in addition to the restricted IPC boundary.
  const haruSession = session.fromPartition('offerpilot-haru', { cache: false });
  haruSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  haruSession.setPermissionCheckHandler(() => false);
  haruSession.on('will-download', event => event.preventDefault());
  haruSession.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !allowHaruResource(details, ready.origin) }));
  const { installWindowPolicy, contentSecurityPolicy } = createCapabilities({
    origin: ready.origin, desktopSession, isTrustedContents, dialog, shell, BrowserWindow,
  });
  const registerWindow = win => {
    const contents = win.webContents;
    trustedContents.add(contents);
    contents.once('destroyed', () => trustedContents.delete(contents));
    installWindowPolicy(win);
  };
  for (const securedSession of [desktopSession, haruSession]) {
    securedSession.webRequest.onBeforeSendHeaders((details, callback) => {
      const headers = authenticatedHeaders(details, {
        origin: ready.origin, token, role: securedSession === desktopSession ? 'owner' : 'haru',
        ownerContents: window?.webContents, trustedContents,
      });
      callback({ requestHeaders: headers });
    });
    securedSession.webRequest.onHeadersReceived((details, callback) => {
      const headers = { ...details.responseHeaders };
      if (isSameOrigin(details.url, ready.origin)) {
        headers['Content-Security-Policy'] = [contentSecurityPolicy];
      }
      callback({ responseHeaders: headers });
    });
  }
  window = new BrowserWindow({
    width: 1280, height: 850, minWidth: 900, minHeight: 600, show: false,
    title: 'OfferPilot Desktop', autoHideMenuBar: true,
    webPreferences: {
      session: desktopSession, preload: path.join(__dirname, 'preload.cjs'),
      additionalArguments: ['--offerpilot-owner'], nodeIntegration: false, contextIsolation: true,
      sandbox: true, webSecurity: true, devTools: !app.isPackaged,
    },
  });
  registerWindow(window);
  haruShell = createHaruShell({
    BrowserWindow, Tray, Menu, nativeImage, screen, ipcMain,
    mainWindow: window, desktopSession: haruSession, origin: ready.origin, userData, registerWindow,
    isQuitting: () => quitting || updateLock, quit: () => app.quit(), log,
  });
  const lockUpdates = value => {
    updateLock = value;
    for (const win of BrowserWindow.getAllWindows()) if (!win.isDestroyed()) win.setEnabled(!value);
  };
  const installSafety = createInstallSafety({ ipcMain, window, origin: ready.origin, dialog,
    lock: () => lockUpdates(true), unlock: () => lockUpdates(false) });
  let unavailable = unavailableReason({ packaged: app.isPackaged, platform: process.platform, policy: RELEASE_POLICY });
  // The release channel remains disabled until an approved signed distribution
  // config is packaged. No token, dev feed, or renderer-controlled URL is used.
  let adapter = null;
  if (!unavailable) {
    try { adapter = await productionAdapter(RELEASE_POLICY); }
    catch { unavailable = '正式更新源或发布者签名配置不完整，已阻止应用内更新。'; }
  }
  let recoveringUpdate = false;
  const recoverStoppedVersion = () => {
    if (recoveringUpdate) return;
    recoveringUpdate = true;
    stopped = true;
    lockUpdates(false);
    try { dialog.showErrorBox('更新未完成', '更新准备或安装启动失败。将尝试重新启动原版本；若未重新打开，请手动启动并核实安装结果。备份与数据目录未被删除。'); } catch { /* A closed OS dialog must not suppress recovery. */ }
    try { app.relaunch(); } catch { log('Could not schedule the original application restart.\n'); }
    app.quit();
  };
  const updates = createUpdater({ version: app.getVersion(), adapter, unavailable,
    prepareInstall: () => installSafety.prepare(),
    verifyDownload: (paths, info) => verifyDownloadedUpdate({ paths, info, verifier: adapter.verifyUpdateCodeSignature, publisher: RELEASE_POLICY.publisherName }),
    onInstallError: recoverStoppedVersion,
    performInstall: async install => {
      try {
        await desktopSession.flushStorageData();
        quitting = true;
        await stopBackendForUpdate(child);
        stopped = true;
        // Persist Haru position only after the backend confirms graceful exit.
        haruShell?.dispose();
        await backupForUpdate({ userData, version: app.getVersion(), backendExited: child.exitCode === 0 && child.signalCode === null });
        await install();
      } catch (error) {
        lockUpdates(false);
        if (stopped || child.exitCode !== null || child.signalCode !== null) {
          recoverStoppedVersion();
        } else {
          quitting = false;
          dialog.showErrorBox('更新未安装', '无法确认本地服务已停止，已阻止备份和安装。请等待当前操作结束，核对保存结果后重新启动应用。');
        }
        throw error;
      }
    },
  });
  const removeUpdateIPC = registerUpdaterIPC({ ipcMain, window, origin: ready.origin, updater: updates, isQuitting: () => quitting || updateLock });
  disposeUpdates = () => { removeUpdateIPC(); installSafety.dispose(); updates.dispose(); };
  window.webContents.on('render-process-gone', () => {
    if (!quitting) {
      dialog.showErrorBox('OfferPilot Desktop', 'The application window stopped. Reopen OfferPilot to recover your saved work.');
      app.quit();
    }
  });
  window.once('ready-to-show', () => {
    if (!quitting && window && !window.isDestroyed()) window.show();
  });
  await window.loadURL(ready.origin);
}
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!quitting && window && !window.isDestroyed()) {
      if (window.isMinimized()) window.restore();
      window.show();
      window.focus();
    }
  });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', (event) => {
    if (stopped) return;
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    disposeUpdates();
    haruShell?.dispose();
    stopBackend(child).finally(() => { stopped = true; app.quit(); });
  });
  app.whenReady().then(start).catch((error) => {
    log(`${error.message}\n`);
    if (!quitting) dialog.showErrorBox('OfferPilot Desktop could not start', `${error.message}\n\nLog: ${logFile || app.getPath('userData')}\nIf the saved port is occupied, close the conflicting application and retry. Do not delete the data folder.`);
    app.quit();
  });
}
