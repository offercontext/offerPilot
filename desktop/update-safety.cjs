'use strict';
const { randomUUID } = require('node:crypto');
const { isTrustedFrame } = require('./haru-protocol.cjs');
const { CHANNEL } = require('./updater.cjs');
function createInstallSafety({ ipcMain, window, origin, dialog, timeoutMs = 5000, lock = () => {}, unlock = () => {} }) {
  const pending = new Map();
  let generation = 0;
  const invalidate = () => {
    generation++;
    for (const request of pending.values()) { clearTimeout(request.timer); request.resolve(null); }
    pending.clear();
  };
  const navigation = (_e, _url, inPlace, mainFrame) => { if (mainFrame && !inPlace) invalidate(); };
  const reply = (event, payload) => {
    if (!isTrustedFrame(event, window.webContents, origin) || !payload || typeof payload.id !== 'string') return;
    const request = pending.get(payload.id);
    if (!request) return;
    pending.delete(payload.id); clearTimeout(request.timer);
    const value = payload.snapshot;
    request.resolve(value && ['ready', 'hasDraft', 'activeRun', 'pendingApproval'].every(k => typeof value[k] === 'boolean') ? value : null);
  };
  ipcMain.on(CHANNEL + 'prepare-reply', reply);
  window.webContents.on('did-start-navigation', navigation);
  window.webContents.on('render-process-gone', invalidate);
  window.webContents.on('destroyed', invalidate);
  const request = () => new Promise(resolve => {
    if (window.isDestroyed() || !isTrustedFrame({sender: window.webContents, senderFrame: window.webContents.mainFrame}, window.webContents, origin)) { resolve(null); return; }
    const id = randomUUID();
    const timer = setTimeout(() => { pending.delete(id); resolve(null); }, timeoutMs);
    pending.set(id, { resolve, timer });
    window.webContents.send(CHANNEL + 'prepare', { id });
  });
  const unsafe = s => {
    if (!s) return '无法确认工作区状态，请重新打开应用后重试。';
    if (!s.ready) return '已知任务状态尚未读取完成，请稍后重试。';
    if (s.hasDraft) return '仍有未保存草稿或附件，请先保存或明确舍弃，再安装更新。';
    if (s.activeRun) return '仍有运行中或状态不明的任务，请等待完成或在任务中停止并核实结果。';
    if (s.pendingApproval) return '仍有待审批操作，请先处理，再安装更新。';
    return null;
  };
  return {
    async prepare() {
      const start = generation;
      const first = unsafe(await request());
      if (first) return first;
      const response = await dialog.showMessageBox(window, { type: 'question', title: '安装更新并重新启动？', message: '请确认已保存所有编辑、可结束后台任务，并同意关闭应用安装。', detail: '部分页面的未保存编辑及后台任务无法完整自动检测。点击继续表示你已保存所有编辑，并确认可结束后台任务。安装前会在本机备份数据与配置；浏览器存储保留原位。安装过程中请勿关机。安装中断可能需要手动重新安装；备份不保证旧版本能直接读取已迁移的数据。', buttons: ['取消', '已保存且可结束任务，关闭并安装'], defaultId: 0, cancelId: 0, noLink: true });
      if (response.response !== 1) return '已取消安装，可稍后继续。';
      if (start !== generation) return '页面状态已变化，请重新检查后再安装。';
      lock();
      try {
        const second = unsafe(await request());
        if (second || start !== generation) { unlock(); return second || '页面状态已变化，请重试。'; }
        return true;
      } catch { unlock(); return '无法核实退出状态，请重试。'; }
    },
    dispose() { invalidate(); ipcMain.removeListener(CHANNEL + 'prepare-reply', reply); window.webContents.removeListener('did-start-navigation', navigation); window.webContents.removeListener('render-process-gone', invalidate); window.webContents.removeListener('destroyed', invalidate); },
  };
}
module.exports = { createInstallSafety };
