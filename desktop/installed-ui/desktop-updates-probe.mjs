import assert from 'node:assert/strict';
import { button } from './ui-locators.mjs';

const VERSION = '0.1.0-desktop.1';
const REASON = '此验证包尚未配置正式签名更新源。';

// Only the existing state IPC is read. Never invoke check/download/install or
// replace the product bridge, update policy, network, or confirmation handlers.
export async function readUnavailableDesktopUpdateState({ version, reason }) {
  const bridge = window.offerpilotUpdates;
  if (window.offerpilotDesktop?.role !== 'owner' || typeof bridge?.getState !== 'function') {
    return { ownerMatched: false, stateUnavailable: false, versionMatched: false, reasonMatched: false };
  }
  let timer;
  try {
    const state = await Promise.race([bridge.getState(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('desktop update state read timed out')), 5000);
    })]);
    return { ownerMatched: true, stateUnavailable: state?.status === 'unavailable',
      versionMatched: state?.currentVersion === version, reasonMatched: state?.reason === reason };
  } finally { clearTimeout(timer); }
}

export async function verifyUnavailableDesktopUpdates(page) {
  const card = page.getByRole('region', { name: '桌面客户端更新', exact: true });
  assert.equal(await card.count(), 1, 'one real desktop updates card required');
  await card.waitFor({ state: 'visible' });
  await card.getByText('当前无法在线更新', { exact: true }).waitFor({ state: 'visible' });
  await card.getByText(REASON, { exact: true }).waitFor({ state: 'visible' });
  await card.getByText(VERSION, { exact: true }).waitFor({ state: 'visible' });
  const native = await page.evaluate(readUnavailableDesktopUpdateState, { version: VERSION, reason: REASON });
  assert.deepEqual(native, { ownerMatched: true, stateUnavailable: true, versionMatched: true, reasonMatched: true },
    'production owner update state must match the unavailable card');
  const check = button(card, '检查更新');
  assert.equal(await check.count(), 1, 'one check-update control required');
  assert.equal(await check.isDisabled(), true, 'unsigned update channel cannot be checked');
  assert.equal(await button(card, '下载更新').count(), 0, 'unsigned update channel cannot offer downloads');
  assert.equal(await button(card, '退出并安装').count(), 0, 'unsigned update channel cannot offer installation');
  return { mechanism: 'real-owner-preload-state-and-settings-card', status: 'unavailable',
    currentVersion: VERSION, reason: 'signed-update-channel-not-configured', checkControlDisabled: true,
    downloadControlAbsent: true, installControlAbsent: true, updateActionsInvoked: false,
    updateFeedActivated: false, updateDownloadValidated: false, signedUpgradeEndToEndValidated: false };
}
