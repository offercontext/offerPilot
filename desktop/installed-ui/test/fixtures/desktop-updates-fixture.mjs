import assert from 'node:assert/strict';
import { readUnavailableDesktopUpdateState } from '../../desktop-updates-probe.mjs';

export function updatesFixture(options = {}, suppliedPage) {
  const calls = [];
  const state = { currentVersion: '0.1.0-desktop.1', status: 'unavailable', reason: '此验证包尚未配置正式签名更新源。',
    releaseNotes: 'private-token do-not-record', ...options.state };
  const text = new Set(['当前无法在线更新', '此验证包尚未配置正式签名更新源。', '0.1.0-desktop.1']);
  if (options.missingText) text.delete(options.missingText);
  const actions = () => { calls.push('forbidden-update-action'); throw new Error('update actions must never be invoked'); };
  const window = { offerpilotDesktop: { role: options.haru ? 'haru' : 'owner' }, offerpilotUpdates: options.missingBridge ? undefined : {
    getState: async () => { calls.push('getState'); if (options.stateError) throw new Error('state unavailable'); return state; },
    check: actions, download: actions, install: actions,
  } };
  const page = suppliedPage ?? {};
  const rows = [{ name: 'reload 检查更新', text: '检查更新', disabled: !options.checkEnabled }];
  if (options.downloadVisible) rows.push({ name: '下载更新', text: '下载更新' });
  if (options.installVisible) rows.push({ name: '退出并安装', text: '退出并安装' });
  const locator = nodes => ({
    page: () => page, nodes,
    filter({ hasText, visible } = {}) { return locator(nodes.filter(node => (!hasText || hasText.test(node.text)) && (visible === undefined || node.visible !== false))); },
    or(other) { return locator([...new Set([...nodes, ...other.nodes])]); },
    count: async () => nodes.length,
    isDisabled: async () => { assert.equal(nodes.length, 1); return nodes[0].disabled; },
    click: actions,
  });
  const card = {
    page: () => page,
    count: async () => options.cardCount ?? 1,
    waitFor: async () => { if (options.hidden) throw new Error('card is hidden'); },
    scrollIntoViewIfNeeded: async () => { calls.push('card-scroll'); },
    getByText(name, config) {
      assert.equal(config.exact, true);
      return { waitFor: async () => { if (!text.has(name)) throw new Error('visible expected card text missing'); } };
    },
    getByRole(role, config = {}) {
      assert.equal(role, 'button');
      return locator(rows.filter(row => config.name === undefined || row.name === config.name));
    },
  };
  const evaluate = async (fn, args) => {
    assert.equal(fn, readUnavailableDesktopUpdateState);
    const previous = globalThis.window; globalThis.window = window;
    try { return await fn(args); } finally { globalThis.window = previous; }
  };
  if (!suppliedPage) Object.assign(page, { evaluate, getByRole: (role, config) => {
    assert.equal(role, 'region'); assert.deepEqual(config, { name: '桌面客户端更新', exact: true }); return card;
  } });
  return { page, card, calls, window, evaluate };
}
