'use strict';
// Validation entry only. Production renderer, preload, shell, capability policy
// and IPC validation are imported unchanged. This is NOT production main.cjs
// startup/backend/updater/installer validation.
const { app, BrowserWindow, session, Tray, Menu, nativeImage, screen, ipcMain, dialog, shell } = require('electron');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { createHaruShell } = require('../haru.cjs');
const { allowHaruResource } = require('../haru-protocol.cjs');
const { createCapabilities } = require('../capabilities.cjs');
const { fixtureResponse, allowedApiPaths } = require('./fixture-api.cjs');
const profile = process.env.HARU_NATIVE_PROFILE;
if (!profile || !path.isAbsolute(profile) || !fs.existsSync(profile)) throw new Error('Owned precreated temporary profile is required');
app.setPath('userData', profile);
const webRoot = path.resolve(__dirname, '../../web/dist');
let server, haruShell, quitting = false;
const audit = { denied: [], apiReads: [], allowedApiPaths, staticFiles: 0, constructedWindows: [] };
globalThis.haruNativeAudit = audit;
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.moc3': 'application/octet-stream' };
app.whenReady().then(async () => {
  server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname.startsWith('/api/')) {
      const data = fixtureResponse(req.method, req.url);
      if (data === null) { audit.denied.push({ method: req.method, path: url.pathname, kind: 'unknown-api' }); res.writeHead(403).end(); return; }
      audit.apiReads.push(url.pathname); res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }).end(JSON.stringify(data)); return;
    }
    const valid = ['GET', 'HEAD'].includes(req.method) && !url.pathname.includes('%') &&
      (url.pathname === '/' || /^\/(?:assets|live2d)\/[A-Za-z0-9_./-]+$/.test(url.pathname));
    const file = path.resolve(webRoot, url.pathname === '/' ? 'index.html' : '.' + url.pathname);
    if (!valid || !file.startsWith(webRoot + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      audit.denied.push({ method: req.method, path: url.pathname, kind: 'unknown-static' }); res.writeHead(404).end(); return;
    }
    audit.staticFiles++; res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    if (req.method === 'HEAD') res.end(); else fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  globalThis.haruNativeOrigin = origin;
  const ownerSession = session.fromPartition('haru-validation-owner', { cache: false });
  const haruSession = session.fromPartition('haru-validation-mirror', { cache: false });
  const trusted = new Set();
  const capabilities = createCapabilities({ origin, desktopSession: ownerSession, isTrustedContents: contents => trusted.has(contents) && !contents.isDestroyed(), dialog, shell, BrowserWindow });
  for (const partition of [ownerSession, haruSession]) {
    // Stricter fixture-level network deny surrounds the unchanged product
    // renderer/CSP. No external models, provider requests or WebSockets.
    partition.webRequest.onBeforeRequest((details, callback) => {
      const url = new URL(details.url);
      const allowed = url.origin === origin && ['GET', 'HEAD'].includes(details.method)
        && (partition === ownerSession || allowHaruResource(details, origin));
      if (!allowed) audit.denied.push({ kind: 'network', method: details.method, path: url.pathname, protocol: url.protocol });
      callback({ cancel: !allowed });
    });
    partition.webRequest.onHeadersReceived((details, callback) => callback({ responseHeaders: {
      ...details.responseHeaders, 'Content-Security-Policy': [capabilities.contentSecurityPolicy],
    } }));
  }
  haruSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  haruSession.setPermissionCheckHandler(() => false);
  haruSession.on('will-download', event => event.preventDefault());
  const registerWindow = win => { trusted.add(win.webContents); win.webContents.once('destroyed', () => trusted.delete(win.webContents)); capabilities.installWindowPolicy(win); };
  const mainWindow = new BrowserWindow({ width: 1280, height: 850, show: false, title: 'OfferPilot native validation',
    webPreferences: { session: ownerSession, preload: path.resolve(__dirname, '../preload.cjs'), additionalArguments: ['--offerpilot-owner'],
      nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, devTools: false } });
  registerWindow(mainWindow);
  const observedBrowserWindow = new Proxy(BrowserWindow, {
    construct(Target, args) {
      const win = Reflect.construct(Target, args);
      audit.constructedWindows.push({ webContentsId: win.webContents.id, frame: args[0].frame, transparent: args[0].transparent });
      return win;
    },
  });
  haruShell = createHaruShell({ BrowserWindow: observedBrowserWindow, Tray, Menu, nativeImage, screen, ipcMain, mainWindow, desktopSession: haruSession,
    origin, userData: profile, registerWindow, isQuitting: () => quitting, quit: () => app.quit() });
  mainWindow.once('ready-to-show', () => mainWindow.show());
  await mainWindow.loadURL(origin + '/');
}).catch(error => { console.error(error); app.exit(1); });
app.on('before-quit', () => { quitting = true; haruShell?.dispose(); server?.close(); });
app.on('window-all-closed', () => app.quit());
