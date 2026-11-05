import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

export const PROFILE = 'OfferPilot Desktop';
export const DRAFT = '未发送的原文 draft · keep exactly 17:30 & <literal>';
export const MAIL = Object.freeze({
  subject: '合成面试 invitation · exact original',
  sender: 'synthetic@example.invalid',
  received: '2026-10-15T08:00:00Z',
  body: '仅供本地预览。Interview 2026-10-20T15:00:00+08:00 for 45 minutes.\n<img src="https://example.invalid/not-to-load.png">\nDo not translate this user-provided text.',
});
export const TEXT = {
  'zh-CN': { navigation: '主导航', settings: '设置', language: '界面语言', haru: 'Pilot 桌面小窗',
    expand: '展开 Pilot 对话', pin: 'Pilot 始终置顶', close: '关闭 Pilot 对话', send: '发送', mailbox: '求职邮箱',
    paste: '粘贴单封邮件', preview: '预览将提交的文本', submit: '提交文本并生成待确认建议',
    back: '返回编辑', cancel: '取消', guide: '连接 QQ 邮箱说明',
    file: '文件', menu: ['文件', '编辑', '视图', '窗口'], quit: '退出并停止本地服务',
    tray: ['打开 OfferPilot', '隐藏 Haru', 'Haru 始终置顶', '退出并停止本地服务'] },
  'en-US': { navigation: 'Main navigation', settings: 'Settings', language: 'Interface language', haru: 'Pilot Desktop Companion',
    expand: 'Expand Pilot Chat', pin: 'Keep Pilot on Top', close: 'Close Pilot chat', send: 'Send', mailbox: 'Job-search mailbox',
    paste: 'Paste one email', preview: 'Preview text to submit', submit: 'Submit text and generate suggestions',
    back: 'Back to editing', cancel: 'Cancel', guide: 'QQ Mail connection guide',
    file: 'File', menu: ['File', 'Edit', 'View', 'Window'], quit: 'Quit and Stop Local Service',
    tray: ['Open OfferPilot', 'Hide Haru', 'Keep Haru on Top', 'Quit and Stop Local Service'] },
};

export function samePath(a, b) { return path.win32.normalize(a).toLowerCase() === path.win32.normalize(b).toLowerCase(); }
export function buttonName(label) {
  const escaped = [...label].map(char => char.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  return new RegExp(`^${escaped.join(/^[\u3400-\u9fff]{2}$/u.test(label) ? '\\s*' : '')}$`, 'u');
}
export function classifyRequest(raw, method, origin) {
  let url;
  try { url = new URL(raw); } catch { return { allowed: false, reason: 'invalid-url' }; }
  if (url.username || url.password) return { allowed: false, reason: 'url-credentials' };
  if (['data:', 'blob:'].includes(url.protocol)) return { allowed: true, reason: 'local-resource' };
  if (url.protocol === 'about:' && url.href === 'about:blank') return { allowed: true, reason: 'initial-document' };
  if (!['http:', 'https:'].includes(url.protocol) || url.origin !== origin) return { allowed: false, reason: 'outbound-or-unexpected-origin' };
  if (!['GET', 'HEAD'].includes(method)) return { allowed: false, reason: 'backend-mutation' };
  return { allowed: true, reason: 'local-read' };
}
export function safeRequest(raw, method, decision) {
  let pathname = '<invalid-url>';
  try { const value = new URL(raw); pathname = value.origin === 'null' ? value.protocol : `${value.origin}${value.pathname}`; } catch { /* Never retain raw malformed input. */ }
  return { method, target: pathname, reason: decision.reason };
}
export function ownedProcesses(processes, pid, exe, backend) {
  const main = processes.find(item => item.pid === pid && samePath(item.path, exe));
  assert.ok(main?.created, 'CIM must independently identify the launched installed executable');
  const children = processes.filter(item => item.parentPid === pid && samePath(item.path, backend));
  assert.ok(children.length <= 1, 'unexpected multiple direct backend children');
  return [main, ...children];
}
export function earlyOwnedProcesses(processes, launch, exe, backend) {
  assert.equal(launch.handleRunning, true, 'launch wrapper must still be alive to establish lineage');
  assert.ok(Number.isSafeInteger(launch.handlePid) && launch.handlePid > 0);
  assert.ok(Number.isFinite(launch.startedAt) && Number.isFinite(launch.observedAt) && launch.observedAt >= launch.startedAt);
  const roots = processes.filter(item => samePath(item.path, exe)
    && (item.parentPid === launch.handlePid || item.pid === launch.handlePid)
    && Date.parse(item.created) >= launch.startedAt && Date.parse(item.created) <= launch.observedAt);
  assert.equal(roots.length, 1, 'one exact installed EXE in this live launch lineage is required');
  const result = ownedProcesses(processes, roots[0].pid, exe, backend);
  for (const item of result) assert.ok(Date.parse(item.created) >= Date.parse(roots[0].created)
    && Date.parse(item.created) <= launch.observedAt, 'owned child birth time must fit the verified main lifetime');
  return result;
}
export function validateSockets(snapshot, backendPid, port) {
  const backend = snapshot.listeners.filter(item => item.pid === backendPid);
  assert.deepEqual(backend.map(item => ({ address: item.address, port: item.port })), [{ address: '127.0.0.1', port }], 'backend must listen only on its authenticated IPv4 loopback origin');
  for (const item of snapshot.listeners) assert.ok(['127.0.0.1', '::1'].includes(item.address), 'debugging and backend listeners must stay on loopback');
  assert.deepEqual(snapshot.outbound, [], 'observed product non-loopback TCP connection');
}
export async function sha256(filename) { return createHash('sha256').update(await fs.readFile(filename)).digest('hex'); }
export async function treeDigest(root) {
  const files = [];
  async function visit(relative = '') {
    for (const entry of (await fs.readdir(path.join(root, relative), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name, 'en'))) {
      const name = relative ? `${relative}/${entry.name}` : entry.name;
      assert.equal(entry.isSymbolicLink(), false, 'packaged payload must not contain symlinks');
      if (entry.isDirectory()) await visit(name);
      else if (entry.isFile()) files.push({ path: name, sha256: await sha256(path.join(root, name)) });
      else assert.fail('unexpected payload entry');
    }
  }
  await visit();
  assert.ok(files.length > 0, 'empty payload tree');
  return { files, sha256: createHash('sha256').update(JSON.stringify(files)).digest('hex') };
}
export function assertNativeMenu(menu, locale) {
  const copy = TEXT[locale];
  assert.deepEqual(menu.map(item => item.label), copy.menu, 'native application menu language');
  assert.ok(menu[0].children.some(item => item.label === copy.quit && item.enabled), 'explicit localized quit must be available');
}
