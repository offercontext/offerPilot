import assert from 'node:assert/strict';
import { markUiStep } from './ui-locators.mjs';

const reloaded = new WeakSet();
export async function reloadOnceWithObserver(qa, page, runtime) {
  await qa.run('STARTUP', 'startup-observed-reload', ['installed startup', 'ordinary reload'], async () => {
    assert.equal(runtime.attached, true, 'runtime observer must already be attached');
    assert.equal(runtime.hasPendingWrite(), false, 'cannot reload an unresolved write');
    assert.equal(reloaded.has(page), false, 'startup reload is strictly one-time');
    reloaded.add(page);
    markUiStep(page, 'startup-reload');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('navigation', { name: '主导航', exact: true }).waitFor();
    await page.waitForFunction(() => ![...document.querySelectorAll('.ant-spin-spinning')].some((node) => node.getClientRects().length > 0));
    await qa.size(1280, 900);
    await qa.capture('startup-observed-reload');
    qa.observed('observer attached before one ordinary reload; real root navigation rendered; no model/runtime substitute');
  }, 'diagnostic');
}
