import assert from 'node:assert/strict';
import { verifyHaruVisual } from './haru-visual.mjs';
import { selectVisibleOption } from './select-option.mjs';
import { probeIdleMotion } from './haru-idle-motion.mjs';
import { invokeTrayAction } from './haru-probes.mjs';
import { button as visibleButton } from './ui-locators.mjs';

const button = (page, name) => page.getByRole('button', { name, exact: true });
export async function readNativeWindow(app, page) {
  const window = await app.browserWindow(page);
  try {
    return await window.evaluate(win => ({ visible: win.isVisible(), minimized: win.isMinimized(), focused: win.isFocused(),
      bounds: win.getBounds(), backgroundColorRgb: win.getBackgroundColor() }));
  } finally { await window.dispose(); }
}
async function actWindow(app, page, action) {
  const window = await app.browserWindow(page);
  try {
    await window.evaluate((win, action) => {
      if (action === 'minimize') win.minimize();
      else if (action === 'close') win.close();
      else if (action === 'focus') { win.show(); win.focus(); }
    }, action);
  } finally { await window.dispose(); }
}
export function nativeHandles({ BrowserWindow }, ids) {
  const owner = BrowserWindow.fromId(ids.owner), haru = BrowserWindow.fromId(ids.haru);
  if (!owner || !haru || owner === haru || owner.isDestroyed() || haru.isDestroyed()) throw new Error('Owned surfaces required');
  const handle = win => {
    const bytes = win.getNativeWindowHandle();
    if (![4, 8].includes(bytes.length)) throw new Error('Unsupported native handle');
    return (bytes.length === 8 ? bytes.readBigUInt64LE() : BigInt(bytes.readUInt32LE())).toString();
  };
  return { pid: process.pid, owner: handle(owner), haru: handle(haru) };
}
export function assertRestoredWindow(before, after, mode) {
  if (mode === 'minimized') assert.equal(before.minimized, true);
  else if (mode === 'hidden-after-close') assert.equal(before.visible, false);
  else { assert.equal(mode, 'background'); assert.equal(before.visible, true); assert.equal(before.focused, false); }
  assert.equal(after.visible, true); assert.equal(after.minimized, false); assert.equal(after.focused, true);
}

// Installed production main/backend/renderer only. No controller replacement,
// backend fixtures, synthetic history publication or credential configuration.
export async function probeInstalledHaruWindows({ app, page, haru, ids, waitUntil, nativeInput, capture, checkpoint, setStage, recordMotion = async () => {} }) {
  const result = { realInstalledWindows: true, realOsPointer: false, themeChecks: [], restores: [],
    historyAndDrafts: 'deferred-to-same-exe-offline-mock', nativeOsCompositionCaptured: false,
    osAlphaCompositionRequiresHumanReview: true };
  const state = () => haru.evaluate(() => window.offerpilotDesktop.getState());
  const frame = async (label, expanded, theme) => {
    setStage(`haru-frame-${label}`);
    await actWindow(app, haru, 'focus');
    return verifyHaruVisual(haru, async visual => {
      await capture(label, haru, { omitBackground: !expanded, visual });
    }, { requireExpanded: expanded, theme });
  };
  const exitPilot = async () => {
    setStage('haru-exit-pilot');
    await button(page, '退出沉浸模式，返回原页面').click();
    await page.waitForURL(url => url.searchParams.get('view') === 'dashboard');
    await page.getByRole('navigation', { name: '主导航', exact: true }).waitFor();
  };
  const enterPilot = async () => {
    setStage('haru-enter-pilot');
    await button(haru, '打开 OfferPilot 主窗口').click();
    await page.waitForURL(url => url.searchParams.get('view') === 'pilot');
    await page.locator('.op-pilot-page-host').waitFor({ state: 'visible' });
    await waitUntil(async () => { const value = await readNativeWindow(app, page); return value.visible && !value.minimized && value.focused; });
  };
  await frame('collapsed-live2d', false);
  const initialReducedMotion = await haru.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  result.originalMotionPolicy = await probeIdleMotion({ app, haru, capture, label: 'idle-original-policy', reducedMotion: initialReducedMotion });
  try {
    // Browser media emulation affects only this test renderer, never OS preferences.
    await haru.emulateMedia({ reducedMotion: 'no-preference' });
    result.idleMotion = await probeIdleMotion({ app, haru, capture, label: 'idle-motion', reducedMotion: false });
    await haru.emulateMedia({ reducedMotion: 'reduce' });
    result.reducedMotion = await probeIdleMotion({ app, haru, capture, label: 'idle-reduced-motion', reducedMotion: true });
    await haru.emulateMedia({ reducedMotion: 'no-preference' });
    result.resumedMotion = await probeIdleMotion({ app, haru, capture, label: 'idle-resumed-motion', reducedMotion: false });
  } finally { await haru.emulateMedia({ reducedMotion: null }); }
  await checkpoint('haru-live-reduced-motion-change', { original: result.originalMotionPolicy, animated: result.idleMotion, reduced: result.reducedMotion, resumed: result.resumedMotion, browserMediaEmulationOnly: true, osPreferenceModified: false });
  const beforeDrag = await readNativeWindow(app, haru);
  const avatar = await haru.locator('.desktop-haru-avatar').boundingBox();
  assert.ok(avatar);
  const viewport = await haru.evaluate(() => ({ width: innerWidth, height: innerHeight }));
  const handles = await app.evaluate(nativeHandles, ids);
  const drag = await nativeInput('Drag', handles, { x: (avatar.x + avatar.width / 2) / viewport.width, y: (avatar.y + avatar.height / 2) / viewport.height });
  assert.equal(drag.ownedNativeDrag, true);
  await waitUntil(async () => { const after = await readNativeWindow(app, haru); return Math.abs(after.bounds.x - beforeDrag.bounds.x) + Math.abs(after.bounds.y - beforeDrag.bounds.y) >= 10; });
  assert.equal((await state()).expanded, false, 'native drag must not expand Haru');
  result.realOsPointer = true; result.drag = { before: beforeDrag.bounds, after: (await readNativeWindow(app, haru)).bounds };
  await frame('after-native-drag', false);
  await checkpoint('haru-native-drag', result.drag);

  await button(haru, '展开 Haru 对话').click();
  await frame('expanded-static', true);
  const originalTheme = await page.locator('html').getAttribute('data-theme');
  assert.ok(['light', 'dark'].includes(originalTheme));
  for (const theme of ['light', 'dark']) {
    if (await page.locator('html').getAttribute('data-theme') !== theme) await button(page, '切换明暗模式').click();
    await haru.waitForFunction(theme => document.querySelector('.desktop-haru')?.getAttribute('data-theme') === theme, theme);
    await frame(`expanded-${theme}`, true, theme);
    await button(haru, '关闭 Haru 对话').click();
    await waitUntil(async () => !(await state()).expanded);
    assert.equal((await readNativeWindow(app, haru)).visible, true, 'closing chat keeps Haru visible');
    await frame(`closed-chat-${theme}`, false, theme);
    await button(haru, '展开 Haru 对话').click();
    await frame(`reopened-after-close-${theme}`, true, theme);
    await app.evaluate(invokeTrayAction, 'hide');
    await waitUntil(async () => !(await readNativeWindow(app, haru)).visible && !(await state()).visible);
    assert.equal(haru.isClosed(), false);
    assert.equal(await haru.locator('.desktop-haru canvas').count(), 0, 'hidden expanded static view must not create a runtime');
    await app.evaluate(invokeTrayAction, 'show');
    await waitUntil(async () => (await readNativeWindow(app, haru)).visible);
    await frame(`reshown-${theme}`, true, theme);
    await button(haru, '收起 Haru 对话').click();
    await frame(`collapsed-${theme}`, false, theme);
    // Hiding the collapsed model must dispose its actual runtime and canvas.
    await app.evaluate(invokeTrayAction, 'hide');
    await waitUntil(async () => !(await readNativeWindow(app, haru)).visible);
    await haru.locator('.desktop-haru-portrait[data-runtime-state="hidden"]').waitFor({ state: 'attached' });
    assert.equal(await haru.locator('.desktop-haru canvas').count(), 0);
    await app.evaluate(invokeTrayAction, 'show');
    await frame(`reshown-collapsed-${theme}`, false, theme);
    await button(haru, '展开 Haru 对话').click();
    await frame(`reopened-${theme}`, true, theme);
    result.themeChecks.push({ theme, expandedStatic: true, collapsedLive2d: true, hiddenRuntimeDisposed: true });
  }
  if (await page.locator('html').getAttribute('data-theme') !== originalTheme) await button(page, '切换明暗模式').click();
  await haru.waitForFunction(theme => document.querySelector('.desktop-haru')?.getAttribute('data-theme') === theme, originalTheme);
  await checkpoint('haru-installed-themes-and-three-states', { themeChecks: result.themeChecks });

  setStage('haru-missing-ai-configuration');
  await waitUntil(async () => (await state()).snapshot?.aiConfigured === false);
  const draft = '安装验证：配置后继续这份草稿';
  const input = haru.locator('#desktop-haru-input');
  assert.equal(await input.isEnabled(), true);
  assert.equal(await button(haru, '发送').isDisabled(), true);
  await input.fill(draft);
  let chatPosts = 0;
  const observeRequest = request => { if (request.method() === 'POST' && /\/api\/chat(?:\/|$)/.test(new URL(request.url()).pathname)) chatPosts++; };
  page.on('request', observeRequest);
  try {
    await input.press('Enter');
    for (const mode of ['minimized', 'hidden-after-close']) {
      setStage(`haru-missing-ai-restore-${mode}`);
      await actWindow(app, page, mode === 'minimized' ? 'minimize' : 'close');
      await waitUntil(async () => { const value = await readNativeWindow(app, page); return mode === 'minimized' ? value.minimized : !value.visible; });
      await button(haru, '去配置 AI').click();
      await page.getByRole('region', { name: 'AI 设置', exact: true }).waitFor();
      await waitUntil(async () => { const value = await readNativeWindow(app, page); return value.visible && !value.minimized && value.focused; });
      assert.equal(await input.inputValue(), draft);
      await capture(`missing-ai-settings-${mode}`, page);
      setStage(`haru-missing-ai-return-${mode}`);
      await visibleButton(page.getByRole('region', { name: 'AI 设置', exact: true }), '返回设置').click();
      setStage(`haru-missing-ai-dashboard-${mode}`);
      await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('button', { name: '今日', exact: true }).click();
      await page.waitForURL(url => url.searchParams.get('view') === 'dashboard');
    }
    await frame('missing-ai-guidance-and-draft', true);
    assert.equal(await input.inputValue(), draft);
    assert.equal(chatPosts, 0, 'No-key Enter must not submit a provider request');
    assert.equal((await state()).snapshot.aiConfigured, false);
    await input.fill('');
    result.missingAI = { draftEditable: true, sendDisabled: true, chatPosts, settingsRestores: ['minimized', 'hidden-after-close'], draftPreserved: true, credentialsConfigured: false };
    await checkpoint('haru-missing-ai-real-settings-navigation', result.missingAI);
  } finally { page.off('request', observeRequest); }

  setStage('haru-explicit-animation-preference');
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('button', { name: '设置', exact: true }).click();
  const animationSelect = page.getByRole('combobox', { name: 'Haru 动画级别', exact: true });
  const previousAnimation = await animationSelect.evaluate(element => element.closest('.ant-select')?.querySelector('.ant-select-selection-item')?.getAttribute('title'));
  assert.ok(['完整', '简洁', '关闭'].includes(previousAnimation));
  let animationFailure;
  const noteAnimation = async (policy, details) => {
    try { await recordMotion(policy, details); }
    catch (error) { if (!animationFailure) animationFailure = error; }
  };
  try {
    // Playwright's initial default and the underlying system preference can differ.
    // Isolate the explicit app setting using only this renderer's media override.
    await recordMotion('setting-test-media', { before: await haru.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches), emulatedReducedMotion: false, osPreferenceModified: false });
    await haru.emulateMedia({ reducedMotion: 'no-preference' });
    await selectVisibleOption(page, animationSelect, '关闭');
    await waitUntil(async () => (await state()).snapshot.animationLevel === 'off');
    await button(haru, '收起 Haru 对话').click();
    await frame('explicit-animation-off', false);
    setStage('haru-probe-explicit-animation-off');
    result.explicitAnimationOff = await probeIdleMotion({ app, haru, capture, label: 'preference-off', reducedMotion: false, animationOff: true,
      record: details => recordMotion('off', details) });
    await selectVisibleOption(page, animationSelect, '完整');
    await waitUntil(async () => (await state()).snapshot.animationLevel === 'full');
    setStage('haru-probe-explicit-animation-restored');
    result.explicitAnimationResumed = await probeIdleMotion({ app, haru, capture, label: 'preference-restored', reducedMotion: false,
      record: details => recordMotion('restored', details) });
  } catch (error) {
    animationFailure = error;
    await noteAnimation('probe-failure', { errorClass: ['AssertionError', 'TimeoutError'].includes(error?.name) ? error.name : 'OtherError' });
  } finally {
    await noteAnimation('restore-original', { phase: 'begin' });
    try {
      await selectVisibleOption(page, animationSelect, previousAnimation);
      await noteAnimation('restore-original', { phase: 'completed' });
    }
    catch (error) {
      if (!animationFailure) animationFailure = error;
      await noteAnimation('restore-original', { phase: 'failed', errorClass: ['AssertionError', 'TimeoutError'].includes(error?.name) ? error.name : 'OtherError' });
    }
    try {
      await haru.emulateMedia({ reducedMotion: null });
      await noteAnimation('restore-media', { phase: 'completed', observedReducedMotion: await haru.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches), osPreferenceModified: false });
    } catch (error) { if (!animationFailure) animationFailure = error; }
  }
  if (animationFailure) throw animationFailure;
  await button(haru, '展开 Haru 对话').click();
  await page.getByRole('navigation', { name: '主导航', exact: true }).getByRole('button', { name: '今日', exact: true }).click();
  await page.waitForURL(url => url.searchParams.get('view') === 'dashboard');
  await checkpoint('haru-owner-animation-setting', { off: result.explicitAnimationOff, restored: result.explicitAnimationResumed, preferenceRestored: true });

  const baseline = await state();
  await enterPilot();
  for (const mode of ['minimized', 'hidden-after-close', 'background']) {
    await exitPilot();
    if (mode === 'minimized') {
      await actWindow(app, page, 'minimize'); await waitUntil(async () => (await readNativeWindow(app, page)).minimized);
    } else if (mode === 'hidden-after-close') {
      await actWindow(app, page, 'close'); await waitUntil(async () => !(await readNativeWindow(app, page)).visible);
    } else {
      await actWindow(app, haru, 'focus'); await waitUntil(async () => !(await readNativeWindow(app, page)).focused);
    }
    const before = await readNativeWindow(app, page);
    await enterPilot();
    const after = await readNativeWindow(app, page);
    assertRestoredWindow(before, after, mode);
    const restored = await state();
    assert.equal(restored.generation, baseline.generation); assert.equal(restored.snapshot.conversationId, baseline.snapshot.conversationId);
    assert.equal(page.isClosed(), false);
    result.restores.push({ mode, before, after, sameGeneration: true, sameConversationId: true });
    await capture(`owner-restored-${mode}`, page);
  }
  for (let index = 0; index < 3; index++) await enterPilot();
  assert.equal((await state()).snapshot.conversationId, baseline.snapshot.conversationId);
  await checkpoint('haru-real-pilot-navigation-and-restores', { restores: result.restores, repeatedHandoffs: 3,
    persistedConversationExercised: false });
  await exitPilot();
  await button(haru, '收起 Haru 对话').click();
  await frame('final-collapsed', false);

  // Last: a native capture failure preserves all earlier functional evidence,
  // but still fails the installed run. Only this process's own window pixels.
  const ownerWindow = await app.browserWindow(page);
  const previous = await ownerWindow.evaluate(win => ({ bounds: win.getBounds(), maximized: win.isMaximized() }));
  try {
    await ownerWindow.evaluate(win => { win.show(); win.maximize(); });
    await waitUntil(() => ownerWindow.evaluate(win => win.isMaximized()));
    await frame('before-os-composition', false);
    const composition = await nativeInput('Capture', handles);
    assert.equal(composition.nativeScreenCapture, true); assert.equal(composition.imageEdited, false);
    assert.equal(composition.ownWindowSamples, 25); assert.ok(composition.haruWindowSamples > 0);
    result.nativeOsCompositionCaptured = true; result.osComposition = composition;
  } finally {
    await ownerWindow.evaluate((win, previous) => { if (!previous.maximized) { win.unmaximize(); win.setBounds(previous.bounds); } }, previous);
    await waitUntil(() => ownerWindow.evaluate((win, previous) => win.isMaximized() === previous.maximized
      && Object.entries(previous.bounds).every(([key, value]) => Math.abs(win.getBounds()[key] - value) <= 1), previous));
    await ownerWindow.dispose();
  }
  await checkpoint('haru-owned-native-composition', result.osComposition);
  return result;
}
