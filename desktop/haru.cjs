'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { translate } = require('./locale.cjs');
const { CHANNEL, sanitizeSnapshot, validateRequest, isTrustedFrame, clampBounds } = require('./haru-protocol.cjs');

function createHaruShell({ BrowserWindow, Tray, Menu, nativeImage, screen, ipcMain, mainWindow, desktopSession, origin, userData, registerWindow, isQuitting, quit, log = () => {}, t = translate, subscribeLocale = () => () => {} }) {
  let haru;
  let tray;
  let snapshot = null;
  let expanded = false;
  let pinned = true;
  let nextCommand = 0;
  let generation = 0;
  let saveTimer;
  let drag = null;
  let ownerDestination = null;
  const pending = new Map();
  const stateFile = path.join(userData, 'haru-window.json');
  let saved;
  try { saved = JSON.parse(fs.readFileSync(stateFile, 'utf8')); pinned = saved?.alwaysOnTop !== false; } catch { /* First launch or invalid preferences use defaults. */ }
  const workAreas = () => {
    const primary = screen.getPrimaryDisplay();
    return [primary, ...screen.getAllDisplays().filter(display => display.id !== primary.id)].map(display => display.workArea);
  };
  const liveHaru = () => haru && !haru.isDestroyed();
  const cancelDrag = () => { drag = null; };
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
    cancelDrag();
    if (liveHaru()) haru.setBounds(clampBounds(haru.getBounds(), workAreas()));
  };
  const showMain = () => {
    if (isQuitting() || mainWindow.isDestroyed() || mainWindow.webContents.isDestroyed()) return false;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
    return true;
  };
  const publishOwnerIntent = () => {
    // A snapshot is published after the owner installs its listeners. Keep one
    // startup/reload intent; never reload the document or select a conversation.
    if (!ownerDestination || !snapshot || isQuitting() || mainWindow.isDestroyed()) return;
    const contents = mainWindow.webContents;
    if (!isTrustedFrame({ sender: contents, senderFrame: contents.mainFrame }, contents, origin)) return;
    const destination = ownerDestination;
    ownerDestination = null;
    contents.send(CHANNEL + destination);
  };
  const refreshMenu = () => {
    if (!tray || tray.isDestroyed()) return;
    tray.setToolTip(t('OfferPilot · Haru（退出请使用托盘菜单）'));
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: t('打开 OfferPilot'), click: showMain },
      { label: liveHaru() && haru.isVisible() ? t('隐藏 Haru') : t('显示 Haru'), click: () => liveHaru() && haru.isVisible() ? haru.hide() : showHaru() },
      { label: t('Haru 始终置顶'), type: 'checkbox', checked: pinned, click: () => setPinned(!pinned) },
      { type: 'separator' },
      { label: t('退出并停止本地服务'), click: quit },
    ]));
  };
  const setPinned = value => {
    pinned = value;
    if (liveHaru()) haru.setAlwaysOnTop(pinned);
    savePosition();
    publishState();
    refreshMenu();
  };
  const setExpanded = value => {
    if (!liveHaru()) return;
    cancelDrag();
    expanded = value;
    const old = haru.getBounds();
    const width = expanded ? 420 : 260;
    const height = expanded ? 740 : 340;
    haru.setBounds(clampBounds({ x: old.x + old.width - width, y: old.y + old.height - height, width, height }, workAreas()));
    publishState();
  };
  const showContextMenu = () => {
    cancelDrag();
    Menu.buildFromTemplate([
      { label: t('隐藏 Haru'), click: () => { if (!isQuitting() && liveHaru()) haru.hide(); } },
      { label: t('Haru 始终置顶'), type: 'checkbox', checked: pinned, click: () => { if (!isQuitting()) setPinned(!pinned); } },
    ]).popup({ window: haru });
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
        icon: path.join(__dirname, 'assets', 'haru-icon.ico'),
        ...clampBounds({ ...saved, width: 260, height: 340 }, workAreas()),
        show: false, frame: false, transparent: true, backgroundColor: '#00000000',
        resizable: false, maximizable: false, fullscreenable: false,
        skipTaskbar: true, alwaysOnTop: pinned, title: 'Haru', autoHideMenuBar: true,
        webPreferences: { session: desktopSession, preload: path.join(__dirname, 'preload.cjs'), additionalArguments: ['--offerpilot-haru'], nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, devTools: false },
      });
      registerWindow(haru);
      haru.once('ready-to-show', () => { if (!isQuitting() && liveHaru()) { haru.show(); publishState(); } });
      haru.on('close', event => { if (!isQuitting()) { event.preventDefault(); setExpanded(false); } });
      haru.on('show', () => { publishState(); refreshMenu(); });
      haru.on('hide', () => { cancelDrag(); publishState(); refreshMenu(); });
      haru.on('blur', cancelDrag);
      haru.on('move', () => { clearTimeout(saveTimer); saveTimer = setTimeout(savePosition, 300); });
      haru.webContents.on('did-start-navigation', (_event, _url, isInPlace, isMainFrame) => { if (isMainFrame && !isInPlace) cancelDrag(); });
      haru.webContents.on('render-process-gone', () => {
        // The mirror may be recreated; it never owns or replays a request.
        cancelDrag();
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
      if (action === 'show-main' || action === 'show-ai-settings') {
        if (!showMain()) return false;
        if (trustedHaru(event)) { ownerDestination = action === 'show-ai-settings' ? 'show-ai-settings' : 'show-pilot'; publishOwnerIntent(); }
      }
      else if (action === 'show-haru') showHaru();
      else if (action === 'hide-haru' && liveHaru()) haru.hide();
      else if (action === 'context-menu' && trustedHaru(event) && haru.isVisible()) showContextMenu();
      else if (action === 'toggle-top') setPinned(!pinned);
      else if ((action === 'expand' || action === 'collapse') && liveHaru()) {
        setExpanded(action === 'expand');
      } else return false;
      return true;
    },
    drag: (event, phase) => {
      // The mirror may only move itself. Coordinates, target windows and sizes
      // never come from the renderer; Electron's cursor and bounds use DIP.
      if (!trustedHaru(event) || isQuitting()) return false;
      if (phase === 'cancel') { cancelDrag(); return true; }
      if (!haru.isVisible() || !['start', 'move', 'end'].includes(phase)) return false;
      const cursor = screen.getCursorScreenPoint();
      if (![cursor?.x, cursor?.y].every(Number.isFinite)) { cancelDrag(); return false; }
      if (phase === 'start') {
        if (drag) return false;
        const bounds = haru.getBounds();
        if (cursor.x < bounds.x || cursor.x >= bounds.x + bounds.width || cursor.y < bounds.y || cursor.y >= bounds.y + bounds.height) return false;
        drag = { cursor, bounds, moved: false };
        return true;
      }
      if (!drag) return false;
      const dx = cursor.x - drag.cursor.x;
      const dy = cursor.y - drag.cursor.y;
      drag.moved ||= Math.hypot(dx, dy) >= 5;
      if (drag.moved) haru.setBounds(clampBounds({ ...drag.bounds, x: drag.bounds.x + dx, y: drag.bounds.y + dy }, workAreas()));
      if (phase === 'end') { cancelDrag(); savePosition(); }
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
    publishOwnerIntent();
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
    tray.setToolTip(t('OfferPilot · Haru（退出请使用托盘菜单）'));
    tray.on('double-click', showMain);
    tray.on('click', showHaru);
    refreshMenu();
  } catch { log('System tray unavailable; closing the main window will exit.\n'); }
  const unsubscribeLocale = subscribeLocale(refreshMenu);
  showHaru();
  return {
    getHaruContents: () => liveHaru() ? haru.webContents : null,
    showHaru,
    dispose() {
      unsubscribeLocale();
      cancelDrag();
      ownerDestination = null;
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
