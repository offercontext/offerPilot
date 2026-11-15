'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { isTrustedFrame } = require('./haru-protocol.cjs');
const english = require('./locale-catalog.cjs');
const CHANNEL = 'offerpilot:locale:';
const DEFAULT_LOCALE = 'zh-CN';
const isLocale = value => value === 'zh-CN' || value === 'en-US';
function translate(source, locale = DEFAULT_LOCALE, values) {
  const text = locale === 'en-US' ? english[source] ?? source : source;
  return values ? text.replace(/\{(\w+)\}/g, (match, key) => Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match) : text;
}
function createLocaleStore(userData) {
  const filename = path.join(userData, 'desktop-locale.json');
  let locale = DEFAULT_LOCALE;
  let persisted = false;
  try {
    if (fs.statSync(filename).size <= 1024) {
      const saved = JSON.parse(fs.readFileSync(filename, 'utf8')).locale;
      if (isLocale(saved)) { locale = saved; persisted = true; }
    }
  } catch { /* First launch or invalid preferences default to Chinese. */ }
  const listeners = new Set();
  return {
    getLocale: () => locale,
    t: (source, values) => translate(source, locale, values),
    setLocale(next) {
      if (!isLocale(next)) return null;
      if (next === locale && persisted) return locale;
      const temporary = `${filename}.${randomUUID()}.tmp`;
      try {
        fs.mkdirSync(userData, { recursive: true });
        const fd = fs.openSync(temporary, 'wx', 0o600);
        try { fs.writeFileSync(fd, `${JSON.stringify({ locale: next })}\n`); fs.fsyncSync(fd); }
        finally { fs.closeSync(fd); }
        fs.renameSync(temporary, filename);
      } catch {
        try { fs.unlinkSync(temporary); } catch { /* There may be no temporary file. */ }
        throw new Error(translate('无法保存语言设置，请重试。', locale));
      }
      locale = next;
      persisted = true;
      // A closed native menu/window must not turn a successful disk write into
      // a reported persistence failure or prevent other windows from updating.
      for (const listener of listeners) { try { listener(locale); } catch { /* The next get reads the saved value. */ } }
      return locale;
    },
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
}
function registerLocaleIPC({ ipcMain, owner, getHaruContents, origin, store, isQuitting }) {
  const trustedOwner = event => !isQuitting() && isTrustedFrame(event, owner.webContents, origin);
  const trustedHaru = event => !isQuitting() && isTrustedFrame(event, getHaruContents(), origin);
  ipcMain.handle(CHANNEL + 'get', event => trustedOwner(event) || trustedHaru(event) ? store.getLocale() : null);
  ipcMain.handle(CHANNEL + 'set', (event, locale) => trustedOwner(event) && isLocale(locale) ? store.setLocale(locale) : null);
  const unsubscribe = store.subscribe(locale => {
    if (isQuitting()) return;
    for (const contents of [owner.webContents, getHaruContents()]) {
      if (contents && isTrustedFrame({ sender: contents, senderFrame: contents.mainFrame }, contents, origin)) {
        try { contents.send(CHANNEL + 'changed', locale); } catch { /* The renderer may have closed since the check. */ }
      }
    }
  });
  return () => { unsubscribe(); ipcMain.removeHandler(CHANNEL + 'get'); ipcMain.removeHandler(CHANNEL + 'set'); };
}
function applicationMenu({ t, showMain, quit, development = false }) {
  return [
    { label: t('文件'), submenu: [{ label: t('打开 OfferPilot'), click: showMain }, { type: 'separator' }, { label: t('退出并停止本地服务'), click: quit }] },
    { label: t('编辑'), submenu: [
      { role: 'undo', label: t('撤销') }, { role: 'redo', label: t('重做') }, { type: 'separator' },
      { role: 'cut', label: t('剪切') }, { role: 'copy', label: t('复制') }, { role: 'paste', label: t('粘贴') }, { role: 'selectAll', label: t('全选') },
    ] },
    { label: t('视图'), submenu: [
      ...(development ? [{ role: 'reload', label: t('重新加载') }, { role: 'toggleDevTools', label: t('开发者工具') }, { type: 'separator' }] : []),
      { role: 'resetZoom', label: t('实际大小') }, { role: 'zoomIn', label: t('放大') }, { role: 'zoomOut', label: t('缩小') }, { role: 'togglefullscreen', label: t('全屏') },
    ] },
    { label: t('窗口'), submenu: [{ role: 'minimize', label: t('最小化') }, { role: 'close', label: t('关闭窗口') }] },
  ];
}
function startupErrorText(error, t) {
  const messages = {
    'Invalid saved desktop port. Restore desktop-port.json from backup.': '保存的桌面端口无效。请从备份恢复 desktop-port.json。',
    'Backend or web assets are missing. Rebuild or reinstall the validation package.': '缺少本地服务或网页资源。请重新构建或安装验证包。',
    'Backend changed the saved port unexpectedly.': '本地服务意外更改了保存的端口。',
    'Backend startup timed out.': '本地服务启动超时。',
    'Could not start the bundled backend.': '无法启动随应用提供的本地服务。',
    'Backend startup failed. Check the desktop log for details.': '本地服务启动失败，请查看桌面日志了解详情。',
    'Invalid backend readiness response.': '本地服务的启动响应无效。',
    'Backend health check failed.': '本地服务健康检查失败。',
    'Backend health check timed out.': '本地服务健康检查超时。',
  };
  const exited = /^Backend exited before startup \((-?\d+|null)\)\.$/.exec(error?.message || '');
  if (exited) return t('本地服务在启动前已退出（{code}）。', { code: exited[1] });
  return t(messages[error?.message] || '本地服务未能启动，请查看桌面日志了解详情。');
}
module.exports = { CHANNEL, DEFAULT_LOCALE, isLocale, translate, createLocaleStore, registerLocaleIPC, applicationMenu, startupErrorText };
