import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { dialogIsSettled, waitForDialogSettled, hasPaintedCharacter } from '../dialog-readiness.mjs';

const element = (overrides = {}) => ({ isConnected: true, parentElement: null,
  getBoundingClientRect: () => ({ width: 720, height: 400 }),
  getAnimations: () => [], style: { display: 'block', visibility: 'visible', opacity: '1' }, ...overrides });
test('capture rejects transparent, moving, hidden or detached dialogs and ancestors', () => {
  const original = globalThis.getComputedStyle;
  globalThis.getComputedStyle = node => node.style;
  try {
    assert.equal(dialogIsSettled(element()), true);
    assert.equal(dialogIsSettled(element({ isConnected: false })), false);
    assert.equal(dialogIsSettled(element({ getBoundingClientRect: () => ({ width: 0, height: 0 }) })), false);
    for (const style of [{ opacity: '0' }, { visibility: 'hidden' }, { display: 'none' }]) {
      const hidden = element({ style: { ...element().style, ...style } });
      assert.equal(dialogIsSettled(hidden), false);
      assert.equal(dialogIsSettled(element({ parentElement: hidden })), false);
    }
    for (const playState of ['running', 'pending']) {
      const moving = element({ getAnimations: () => [{ playState }] });
      assert.equal(dialogIsSettled(moving), false);
      assert.equal(dialogIsSettled(element({ parentElement: moving })), false);
      assert.equal(dialogIsSettled(element({ getAnimations: options => options?.subtree ? [{ playState }] : [] })), false);
    }
  } finally { globalThis.getComputedStyle = original; }
});
test('wait requires visible dialog, frame settling and bounded readiness before disposal', async () => {
  const calls = [];
  const handle = { dispose: async () => calls.push('dispose') };
  await waitForDialogSettled({
    waitFor: async options => { assert.equal(options.state, 'visible'); calls.push('visible'); },
    evaluate: async () => calls.push('frames'), elementHandle: async () => handle,
    page: () => ({ waitForFunction: async (predicate, target, options) => {
      assert.equal(predicate, dialogIsSettled); assert.equal(target, handle);
      assert.equal(options.timeout, 15000); calls.push('settled');
    } }),
  });
  assert.deepEqual(calls, ['visible', 'frames', 'settled', 'dispose']);
});
test('both locale guide captures wait for the named dialog to settle', () => {
  const source = fs.readFileSync(new URL('../smoke.mjs', import.meta.url), 'utf8');
  assert.match(source, /const guide = active\.page\.getByRole\('dialog', \{ name: locale === 'zh-CN' \? '安全配置 QQ 邮箱' : 'Secure QQ Mail setup', exact: true \}\);\s+await waitForDialogSettled\(guide\);/);
  assert.ok(source.indexOf('await waitForDialogSettled(guide)') < source.indexOf('`${active.number}-${locale}-mail-safe-connection-guide`'));
});

test('character pixel gate rejects blank or flat frames and accepts a painted model', () => {
  assert.equal(hasPaintedCharacter({ pixels: 10000, opaquePixels: 0, minChannel: 255, maxChannel: 0 }), false);
  assert.equal(hasPaintedCharacter({ pixels: 10000, opaquePixels: 10000, minChannel: 255, maxChannel: 255 }), false);
  assert.equal(hasPaintedCharacter({ pixels: 10000, opaquePixels: 50, minChannel: 0, maxChannel: 255 }), false);
  assert.equal(hasPaintedCharacter({ pixels: 10000, opaquePixels: 2000, minChannel: 20, maxChannel: 230 }), true);
});
test('native captures wait for painted character and closed locale dropdown', () => {
  const source = fs.readFileSync(new URL('../smoke.mjs', import.meta.url), 'utf8');
  assert.ok(source.indexOf('await waitForPaintedCharacter(active.haru)') < source.indexOf('`${number}-${locale}-haru-close-keeps-character`'));
  assert.ok(source.indexOf("locator('.ant-select-dropdown:visible').waitFor({ state: 'hidden' })") < source.indexOf('`${active.number}-${to}-settings-after-real-switch`'));
});
