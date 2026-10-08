'use strict';
const path = require('node:path');

// Model data only. Executable ONNX modules/WASM are packaged with the frontend.
const MODEL_DATA_ORIGINS = ['https://huggingface.co', 'https://us.aws.cdn.hf.co', 'https://us.gcp.cdn.hf.co', 'https://cdn-lfs-us-1.hf.co', 'https://cdn-lfs-eu-1.hf.co', 'https://cas-bridge.xethub.hf.co'];
const contentSecurityPolicy = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' " + MODEL_DATA_ORIGINS.join(' ') + "; worker-src 'self' blob:; media-src 'self' blob:; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";
function localURL(value, origin, allowBlob = false) {
  try {
    const url = new URL(value);
    return url.origin === origin && !url.username && !url.password
      && (url.protocol === 'http:' || (allowBlob && url.protocol === 'blob:'));
  } catch { return false; }
}
function externalURL(value, origin) {
  try {
    if (typeof value !== 'string' || value.length > 8192 || /[\x00-\x1f\x7f]/.test(value)) return null;
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.origin === origin) return null;
    return url.href;
  } catch { return null; }
}
function safeFilename(value) {
  const name = path.win32.basename(path.posix.basename(String(value || 'download')))
    .replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g, '_').replace(/[. ]+$/g, '').slice(0, 180);
  return !name || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name) ? `export-${name || 'download'}` : name;
}
function sourceDownload(value, origin) {
  try {
    const url = new URL(value);
    return localURL(value, origin) && !url.search && !url.hash
      && /^\/api\/knowledge\/sources\/\d+\/(?:content|assets\/\d+\/content)$/.test(url.pathname);
  } catch { return false; }
}
function createCapabilities({ origin, desktopSession, isTrustedContents, dialog, shell, BrowserWindow }) {
  const audioGrants = new WeakSet();
  const pending = new WeakSet();
  const epochs = new WeakMap();
  const sourceDownloads = new WeakMap();
  const trusted = contents => Boolean(contents && isTrustedContents(contents) && localURL(contents.getURL(), origin));
  const mainFrame = (contents, details) => trusted(contents) && details?.isMainFrame === true && localURL(details.requestingUrl, origin);
  const parent = contents => BrowserWindow.fromWebContents(contents);
  desktopSession.setPermissionCheckHandler((contents, permission, requestingOrigin, details) =>
    permission === 'media' && mainFrame(contents, details) && localURL(requestingOrigin, origin)
      && details.mediaType === 'audio' && audioGrants.has(contents));
  desktopSession.setPermissionRequestHandler((contents, permission, callback, details) => {
    const audioOnly = permission === 'media' && mainFrame(contents, details)
      && Array.isArray(details.mediaTypes) && details.mediaTypes.length === 1 && details.mediaTypes[0] === 'audio';
    const clipboardWrite = permission === 'clipboard-sanitized-write' && mainFrame(contents, details);
    if ((!audioOnly && !clipboardWrite) || pending.has(contents)) { callback(false); return; }
    pending.add(contents);
    const startingURL = contents.getURL();
    const epoch = epochs.get(contents);
    Promise.resolve().then(() => dialog.showMessageBox(parent(contents), {
      type: 'question', title: clipboardWrite ? '复制到剪贴板？' : '允许录音？',
      message: clipboardWrite ? '允许将当前选定内容复制到系统剪贴板？' : '允许 OfferPilot 在当前窗口使用麦克风？',
      detail: clipboardWrite ? '会替换现有剪贴板内容，仅允许这一次复制，不读取剪贴板。'
        : '仅用于你主动开始的录音。不会授权摄像头、屏幕录制或其他设备。关闭或重新加载窗口后需要再次确认。',
      buttons: ['取消', clipboardWrite ? '复制' : '允许麦克风'], defaultId: 0, cancelId: 0, noLink: true,
    })).then(result => {
      const allowed = result.response === 1 && mainFrame(contents, details) && contents.getURL() === startingURL && epochs.get(contents) === epoch;
      if (allowed && audioOnly) audioGrants.add(contents);
      pending.delete(contents);
      callback(allowed);
    }, () => { pending.delete(contents); callback(false); });
  });
  desktopSession.on('will-download', (event, item, contents, frame) => {
    const urls = item.getURLChain();
    const ticket = contents && sourceDownloads.get(contents);
    if (contents) sourceDownloads.delete(contents);
    const approvedSource = ticket && ticket.epoch === epochs.get(contents) && ticket.expires >= Date.now()
      && urls.length === 1 && urls[0] === ticket.url && sourceDownload(ticket.url, origin);
    const validFrame = frame === contents?.mainFrame && frame != null;
    if (!trusted(contents) || (!validFrame && !(frame == null && approvedSource))
      || !urls.length || !urls.every(url => localURL(url, origin, true))
      || (!approvedSource && !localURL(item.getInitiatorOrigin(), origin))) {
      event.preventDefault(); return;
    }
    // Never select an automatic path, persist a path, or launch the saved file.
    // Electron's native save dialog confirms the path and overwrite explicitly.
    let selected;
    try { selected = dialog.showSaveDialogSync(parent(contents), {
      title: '保存 OfferPilot 导出文件', defaultPath: safeFilename(item.getFilename()),
      buttonLabel: '保存', properties: ['showOverwriteConfirmation', 'dontAddToRecent'],
    }); } catch { event.preventDefault(); return; }
    if (!selected || !trusted(contents)) { event.preventDefault(); return; }
    item.setSavePath(selected);
  });
  function requestExternal(contents, value) {
    const url = externalURL(value, origin);
    if (!trusted(contents) || !url || pending.has(contents)) return;
    const startingURL = contents.getURL();
    const epoch = epochs.get(contents);
    pending.add(contents);
    Promise.resolve().then(() => dialog.showMessageBox(parent(contents), {
      type: 'question', title: '打开外部链接？', message: '在系统浏览器中打开此链接？', detail: url,
      buttons: ['取消', '打开浏览器'], defaultId: 0, cancelId: 0, noLink: true,
    })).then(async result => {
      if (result.response === 1 && trusted(contents) && contents.getURL() === startingURL && epochs.get(contents) === epoch) await shell.openExternal(url);
    }).catch(() => { /* Closing a window or a rejected OS launch must not crash the app. */ })
      .finally(() => pending.delete(contents));
  }
  function installWindowPolicy(win) {
    const contents = win.webContents;
    contents.setWindowOpenHandler(({ url }) => {
      if (trusted(contents) && sourceDownload(url, origin)) {
        sourceDownloads.set(contents, {url, epoch: epochs.get(contents), expires: Date.now() + 5000});
        try { contents.downloadURL(url); } catch { sourceDownloads.delete(contents); }
      }
      else requestExternal(contents, url);
      return { action: 'deny' };
    });
    contents.on('will-navigate', (event, url) => {
      if (!localURL(url, origin)) { event.preventDefault(); requestExternal(contents, url); }
    });
    contents.on('will-redirect', (event, url) => { if (!localURL(url, origin)) event.preventDefault(); });
    contents.on('will-attach-webview', event => event.preventDefault());
    contents.on('did-start-navigation', (_event, _url, isInPlace, isMainFrame) => {
      if (isMainFrame && !isInPlace) {
        audioGrants.delete(contents); sourceDownloads.delete(contents); epochs.set(contents, (epochs.get(contents) || 0) + 1);
      }
    });
    contents.on('destroyed', () => { audioGrants.delete(contents); pending.delete(contents); sourceDownloads.delete(contents); epochs.set(contents, (epochs.get(contents) || 0) + 1); });
  }
  return { installWindowPolicy, contentSecurityPolicy };
}
module.exports = { createCapabilities, contentSecurityPolicy, localURL, externalURL, safeFilename, sourceDownload };
