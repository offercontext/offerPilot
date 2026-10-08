'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { CHANNEL, sanitizeSnapshot, validateRequest, isTrustedFrame, clampBounds } = require('./haru-protocol.cjs');

function createHaruShell({ BrowserWindow, Tray, Menu, nativeImage, screen, ipcMain, mainWindow, desktopSession, origin, userData, registerWindow, isQuitting, quit, log = () => {} }) {
  let haru;
  let tray;
  let snapshot = null;
  let expanded = false;
  let pinned = false;
  let nextCommand = 0;
  let generation = 0;
  let saveTimer;
  const pending = new Map();
  const stateFile = path.join(userData, 'haru-window.json');
  let saved;
  try { saved = JSON.parse(fs.readFileSync(stateFile, 'utf8')); pinned = saved?.alwaysOnTop === true; } catch { /* First launch or invalid preferences use defaults. */ }
  const workAreas = () => {
    const primary = screen.getPrimaryDisplay();
    return [primary, ...screen.getAllDisplays().filter(display => display.id !== primary.id)].map(display => display.workArea);
  };
  const liveHaru = () => haru && !haru.isDestroyed();
  const state = () => ({ connected: snapshot !== null, generation, snapshot, visible: Boolean(liveHaru() && haru.isVisible()), expanded, alwaysOnTop: pinned });
  const publishState = () => {
    if (liveHaru()) haru.webContents.send(CHANNEL + 'state', state());
  };
  const savePosition = () => {
    clearTimeout(saveTimer);
    if (!liveHaru()) return;
    const bounds = haru.getBounds();
    saved = bounds;
    try {
      fs.writeFileSync(stateFile + '.tmp', JSON.stringify({ ...bounds, alwaysOnTop: pinned }));
      fs.renameSync(stateFile + '.tmp', stateFile);
    } catch { log('Could not save Haru window position.\n'); }
  };
  const fit = () => {
    if (liveHaru()) haru.setBounds(clampBounds(haru.getBounds(), workAreas()));
  };
  const showMain = () => {
    if (isQuitting() || mainWindow.isDestroyed()) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  };
  const refreshMenu = () => {
    if (!tray || tray.isDestroyed()) return;
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: '打开 OfferPilot', click: showMain },
      { label: liveHaru() && haru.isVisible() ? '隐藏 Haru' : '显示 Haru', click: () => liveHaru() && haru.isVisible() ? haru.hide() : showHaru() },
      { label: 'Haru 始终置顶', type: 'checkbox', checked: pinned, click: () => setPinned(!pinned) },
      { type: 'separator' },
      { label: '退出并停止本地服务', click: quit },
    ]));
  };
  const setPinned = value => {
    pinned = value;
    if (liveHaru()) haru.setAlwaysOnTop(pinned);
    savePosition();
    publishState();
    refreshMenu();
  };
  const invalidateOwner = () => {
    snapshot = null;
    generation += 1;
    for (const request of pending.values()) { clearTimeout(request.timer); request.resolve({ ok: false, reason: 'unavailable' }); }
    pending.clear();
    publishState();
  };
  const showHaru = () => {
    if (isQuitting()) return;
    if (!liveHaru()) {
      expanded = false;
      haru = new BrowserWindow({
        ...clampBounds({ ...saved, width: 260, height: 340 }, workAreas()),
        show: false, frame: false, transparent: true, backgroundColor: '#00000000',
        resizable: false, maximizable: false, fullscreenable: false,
        skipTaskbar: true, alwaysOnTop: pinned, title: 'Haru', autoHideMenuBar: true,
        webPreferences: { session: desktopSession, preload: path.join(__dirname, 'preload.cjs'), additionalArguments: ['--offerpilot-haru'], nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, devTools: false },
      });
      registerWindow(haru);
      haru.once('ready-to-show', () => { if (!isQuitting() && liveHaru()) { haru.show(); publishState(); } });
      haru.on('close', event => { if (!isQuitting()) { event.preventDefault(); haru.hide(); } });
      haru.on('show', () => { publishState(); refreshMenu(); });
      haru.on('hide', () => { publishState(); refreshMenu(); });
      haru.on('move', () => { clearTimeout(saveTimer); saveTimer = setTimeout(savePosition, 300); });
      haru.webContents.on('render-process-gone', () => {
        // The mirror may be recreated; it never owns or replays a request.
        savePosition();
        haru.destroy();
        refreshMenu();
      });
      void haru.loadURL(`${origin}/?desktopSurface=haru`).catch(() => { if (liveHaru()) haru.destroy(); showMain(); });
    } else {
      fit();
      haru.show();
      haru.focus();
    }
    refreshMenu();
  };
  const trustedOwner = event => isTrustedFrame(event, mainWindow.webContents, origin);
  const trustedHaru = event => liveHaru() && isTrustedFrame(event, haru.webContents, origin);
  const handlers = {
    state: event => trustedOwner(event) || trustedHaru(event) ? state() : null,
    window: (event, action) => {
      if ((!trustedOwner(event) && !trustedHaru(event)) || isQuitting()) return false;
      if (action === 'show-main') showMain();
      else if (action === 'show-haru') showHaru();
      else if (action === 'hide-haru' && liveHaru()) haru.hide();
      else if (action === 'toggle-top') setPinned(!pinned);
      else if ((action === 'expand' || action === 'collapse') && liveHaru()) {
        expanded = action === 'expand';
        const old = haru.getBounds();
        const width = expanded ? 420 : 260;
        const height = expanded ? 740 : 340;
        haru.setBounds(clampBounds({ x: old.x + old.width - width, y: old.y + old.height - height, width, height }, workAreas()));
        publishState();
      } else return false;
      return true;
    },
    request: (event, value) => {
      if (!trustedHaru(event) || isQuitting()) return { ok: false, reason: 'unavailable' };
      const request = validateRequest(value);
      if (!request || !snapshot) return { ok: false, reason: 'unavailable' };
      if (value.generation !== generation) return { ok: false, reason: 'stale' };
      if (request.version !== snapshot.version) return { ok: false, reason: 'stale' };
      if (pending.size > 0) return { ok: false, reason: 'busy' };
      const id = ++nextCommand;
      return new Promise(resolve => {
        const timer = setTimeout(() => {
          pending.delete(id);
          // Delivery may already have happened. Never replay on timeout.
          resolve({ ok: false, reason: 'uncertain' });
        }, 5000);
        pending.set(id, { resolve, timer });
        mainWindow.webContents.send(CHANNEL + 'command', { ...request, id });
      });
    },
  };
  for (const [name, handler] of Object.entries(handlers)) ipcMain.handle(CHANNEL + name, handler);
  const onPublish = (event, value) => {
    if (!trustedOwner(event) || isQuitting()) return;
    const clean = sanitizeSnapshot(value);
    if (!clean) return;
    snapshot = clean;
    if (liveHaru() && haru.isVisible()) publishState();
  };
  const onDisconnect = event => { if (trustedOwner(event)) invalidateOwner(); };
  const onReply = (event, value) => {
    if (!trustedOwner(event) || !Number.isSafeInteger(value?.id)) return;
    const request = pending.get(value.id);
    if (!request) return;
    clearTimeout(request.timer);
    pending.delete(value.id);
    const reasons = new Set(['stale', 'busy', 'unavailable']);
    request.resolve(value.result?.ok === true ? { ok: true } : { ok: false, reason: reasons.has(value.result?.reason) ? value.result.reason : 'unavailable' });
  };
  ipcMain.on(CHANNEL + 'publish', onPublish);
  ipcMain.on(CHANNEL + 'disconnect', onDisconnect);
  ipcMain.on(CHANNEL + 'reply', onReply);
  mainWindow.webContents.on('did-start-navigation', (_event, _url, isInPlace, isMainFrame) => { if (isMainFrame && !isInPlace) invalidateOwner(); });
  mainWindow.webContents.on('render-process-gone', invalidateOwner);
  mainWindow.on('close', event => {
    if (!isQuitting()) {
      event.preventDefault();
      if (tray && !tray.isDestroyed()) mainWindow.hide();
      else quit();
    }
  });
  screen.on('display-removed', fit);
  screen.on('display-metrics-changed', fit);
  try {
    tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'assets', 'haru-tray.png')));
    tray.setToolTip('OfferPilot · Haru（退出请使用托盘菜单）');
    tray.on('double-click', showMain);
    tray.on('click', showHaru);
    refreshMenu();
  } catch { log('System tray unavailable; closing the main window will exit.\n'); }
  showHaru();
  return {
    showHaru,
    dispose() {
      savePosition();
      invalidateOwner();
      if (tray && !tray.isDestroyed()) tray.destroy();
      for (const name of Object.keys(handlers)) ipcMain.removeHandler(CHANNEL + name);
      ipcMain.removeListener(CHANNEL + 'publish', onPublish);
      ipcMain.removeListener(CHANNEL + 'disconnect', onDisconnect);
      ipcMain.removeListener(CHANNEL + 'reply', onReply);
      screen.removeListener('display-removed', fit);
      screen.removeListener('display-metrics-changed', fit);
    },
  };
}
module.exports = { createHaruShell };
