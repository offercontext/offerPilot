'use strict';
const { app, BrowserWindow, dialog, session } = require('electron');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { TOKEN_HEADER, isSameOrigin, waitForReady, checkHealth, stopBackend } = require('./lifecycle.cjs');

app.setName('OfferPilot Desktop');
app.setPath('userData', path.join(app.getPath('appData'), 'OfferPilot Desktop'));
let child;
let window;
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
  desktopSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  desktopSession.setPermissionCheckHandler(() => false);
  desktopSession.on('will-download', (event) => event.preventDefault());
  desktopSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = details.requestHeaders;
    for (const name of Object.keys(headers)) {
      if (name.toLowerCase() === TOKEN_HEADER.toLowerCase()) delete headers[name];
    }
    if (window && details.webContentsId === window.webContents.id && isSameOrigin(details.url, ready.origin)) {
      headers[TOKEN_HEADER] = token;
    }
    callback({ requestHeaders: headers });
  });
  desktopSession.webRequest.onHeadersReceived((details, callback) => {
    const headers = { ...details.responseHeaders };
    if (isSameOrigin(details.url, ready.origin)) {
      headers['Content-Security-Policy'] = ["default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; media-src 'self' blob:; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"];
    }
    callback({ responseHeaders: headers });
  });
  window = new BrowserWindow({
    width: 1280, height: 850, minWidth: 900, minHeight: 600, show: false,
    title: 'OfferPilot Desktop', autoHideMenuBar: true,
    webPreferences: {
      session: desktopSession, nodeIntegration: false, contextIsolation: true,
      sandbox: true, webSecurity: true, devTools: !app.isPackaged,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => { if (!isSameOrigin(url, ready.origin)) event.preventDefault(); });
  window.webContents.on('will-redirect', (event, url) => { if (!isSameOrigin(url, ready.origin)) event.preventDefault(); });
  window.webContents.on('will-attach-webview', (event) => event.preventDefault());
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
    stopBackend(child).finally(() => { stopped = true; app.quit(); });
  });
  app.whenReady().then(start).catch((error) => {
    log(`${error.message}\n`);
    if (!quitting) dialog.showErrorBox('OfferPilot Desktop could not start', `${error.message}\n\nLog: ${logFile || app.getPath('userData')}\nIf the saved port is occupied, close the conflicting application and retry. Do not delete the data folder.`);
    app.quit();
  });
}
