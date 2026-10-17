import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { observeHaruVisual, assertHaruVisual, verifyHaruVisual } from '../haru-visual.mjs';
const expanded = { expanded: true, theme: 'light', fallbackVisible: false, canvasCount: 0, staticImages: 1, runtimeState: 'absent',
  staticImageLoaded: true, staticImageSource: 'haru-window-light-abc123.png', surfaceWithinViewport: true, avatarWithinViewport: true,
  assistantMessageCount: 1, assistantAvatars: [{ loaded: true, alt: 'Haru 头像', source: 'haru-window-light-abc123.png', hidden: false }],
  avatarAboveChat: true, contextAccessible: true, inputAccessible: true, submitAccessible: true, mainWindowAccessible: true,
  collapsedOnlyModel: true, collapsedTransparent: true, documentOverflows: false, horizontalChatOverflow: false };
const collapsed = { ...expanded, expanded: false, canvasCount: 1, staticImages: 0, runtimeState: 'ready', contextAvailable: true,
  contextLost: false, contextAlpha: true, canvasCssTransparent: true, backingWidth: 260, backingHeight: 340, canvasIntersectsAvatar: true,
  canvasWithinPortrait: true, portraitWithinViewport: true, canvasVisible: true };
test('expanded installed Haru requires its loaded static image and accessible controls, never a canvas', () => {
  assertHaruVisual(expanded, { theme: 'light' });
  for (const patch of [{ canvasCount: 1 }, { staticImageLoaded: false }, { runtimeState: 'ready' }, { inputAccessible: false },
    { contextAccessible: false }, { mainWindowAccessible: false }, { avatarAboveChat: false }, { documentOverflows: true },
    { staticImageSource: 'private-url' }, { theme: 'dark' }, { expanded: false }]) assert.throws(() => assertHaruVisual({ ...expanded, ...patch }));
});
test('message avatars must match the loaded theme image and retain accessible Haru alt text', () => {
  assert.throws(() => assertHaruVisual({ ...expanded, assistantAvatars: [] }));
  for (const patch of [{ loaded: false }, { alt: null }, { hidden: true }, { source: 'haru-window-dark-a.png' }]) {
    assert.throws(() => assertHaruVisual({ ...expanded, assistantAvatars: [{ ...expanded.assistantAvatars[0], ...patch }] }));
  }
});
test('collapsed installed Haru requires only a healthy alpha Live2D canvas, no controls or image substitute', () => {
  assertHaruVisual(collapsed, { requireExpanded: false });
  for (const patch of [{ staticImages: 1 }, { fallbackVisible: true }, { contextLost: true }, { contextAvailable: false },
    { contextAlpha: false }, { canvasCssTransparent: false }, { collapsedOnlyModel: false }, { collapsedTransparent: false },
    { backingWidth: 0 }, { backingHeight: 20000 }, { canvasIntersectsAvatar: false }, { canvasWithinPortrait: false },
    { portraitWithinViewport: false }, { runtimeState: 'loading' }, { expanded: true }]) {
    assert.throws(() => assertHaruVisual({ ...collapsed, ...patch }, { requireExpanded: false }));
  }
});
test('read-only observation never creates a context for hidden/unready canvas or emits arbitrary strings', () => {
  let calls = 0;
  const box = { left: 0, top: 0, right: 260, bottom: 340, width: 260, height: 340 };
  const canvas = { getClientRects: () => [{}], getBoundingClientRect: () => box, width: 260, height: 340,
    getContext: () => { calls++; throw new Error('must not allocate'); } };
  const portrait = { getAttribute: () => 'untrusted-secret', querySelector: () => canvas, querySelectorAll: () => [], getBoundingClientRect: () => box };
  const root = { children: [{}], classList: { contains: () => false }, getAttribute: () => 'private-theme',
    querySelector: () => null, getBoundingClientRect: () => box };
  const document = { querySelector: selector => selector === '.desktop-haru-portrait' ? portrait : selector === '.desktop-haru' ? root : selector === '.desktop-haru-avatar' ? root : null,
    querySelectorAll: () => [], documentElement: { scrollWidth: 260, scrollHeight: 340 }, body: {}, elementFromPoint: () => null };
  const value = vm.runInNewContext(`(${observeHaruVisual.toString()})()`, { document, innerWidth: 260, innerHeight: 340,
    getComputedStyle: () => ({ visibility: 'visible', opacity: '1', backgroundColor: 'transparent' }) });
  assert.equal(calls, 0); assert.equal(value.runtimeState, 'unknown'); assert.equal(value.theme, 'unknown');
  assert.doesNotMatch(JSON.stringify(value), /untrusted-secret|private-theme/);
});
test('actual observation rejects a hidden/transparent canvas or ancestor without opening its WebGL context', () => {
  function observe(hidden) {
    let contextReads = 0;
    const box = { left: 0, top: 0, right: 260, bottom: 340, width: 260, height: 340 };
    const element = () => ({ getClientRects: () => [{}], getBoundingClientRect: () => box,
      style: { visibility: 'visible', opacity: '1', backgroundColor: 'transparent' } });
    const html = { ...element(), scrollWidth: 260, scrollHeight: 340 };
    const body = { ...element(), parentElement: html };
    const root = { ...element(), parentElement: body, classList: { contains: () => false }, getAttribute: () => 'light', querySelector: () => null };
    const avatar = { ...element(), parentElement: root };
    const portrait = { ...element(), parentElement: avatar, getAttribute: () => 'ready', querySelectorAll: () => [] };
    const canvas = { ...element(), parentElement: portrait, width: 260, height: 340, getContext: () => {
      contextReads++; return { isContextLost: () => false, getContextAttributes: () => ({ alpha: true }) };
    } };
    root.children = [avatar]; portrait.querySelector = () => canvas;
    if (hidden === 'visibility') canvas.style.visibility = 'hidden';
    if (hidden === 'opacity') canvas.style.opacity = '0';
    if (hidden === 'ancestor-opacity') avatar.style.opacity = '0';
    if (hidden === 'no-rect') canvas.getClientRects = () => [];
    if (hidden === 'zero-backing') canvas.width = 0;
    const document = { documentElement: html, body,
      querySelector: selector => ({ '.desktop-haru': root, '.desktop-haru-avatar': avatar, '.desktop-haru-portrait': portrait })[selector] || null,
      querySelectorAll: selector => selector === '.desktop-haru canvas' ? [canvas] : [], elementFromPoint: () => avatar };
    const value = vm.runInNewContext(`(${observeHaruVisual.toString()})()`, { document, innerWidth: 260, innerHeight: 340,
      getComputedStyle: node => node.style });
    return { value, contextReads };
  }
  const good = observe(); assertHaruVisual(good.value, { requireExpanded: false }); assert.equal(good.contextReads, 1);
  for (const hidden of ['visibility', 'opacity', 'ancestor-opacity', 'no-rect', 'zero-backing']) {
    const actual = observe(hidden);
    if (hidden !== 'zero-backing') assert.equal(actual.value.canvasVisible, false);
    assert.equal(actual.contextReads, 0);
    assert.throws(() => assertHaruVisual(actual.value, { requireExpanded: false }));
  }
});
test('static readiness does not wait for nonexistent Live2D and still retains failed pixels', async () => {
  let captures = 0; const waits = [];
  const page = { locator: () => { throw new Error('expanded must not wait for canvas'); },
    waitForFunction: async (_fn, _arg, options) => waits.push(options.timeout), evaluate: async () => expanded };
  assert.equal(await verifyHaruVisual(page, async () => { captures++; }), expanded);
  assert.equal(captures, 1); assert.deepEqual(waits, [25000, 5000]);
  page.evaluate = async () => ({ ...expanded, staticImageLoaded: false });
  await assert.rejects(verifyHaruVisual(page, async () => { captures++; }));
  assert.equal(captures, 2);
});
for (const failAt of ['ready', 'frame']) test(`${failAt} timeout preserves one actual Haru failure screenshot`, async () => {
  let captures = 0;
  const page = { locator: selector => { assert.equal(selector, '.desktop-haru-portrait[data-runtime-state="ready"]'); return { waitFor: async () => {
    if (failAt === 'ready') throw new Error('bounded ready timeout');
  } }; }, evaluate: async () => collapsed, waitForFunction: async (_fn, _arg, options) => {
    assert.equal(options.timeout, 5000); throw new Error('bounded frame timeout');
  } };
  await assert.rejects(verifyHaruVisual(page, async () => { captures++; }, { requireExpanded: false }), /bounded/);
  assert.equal(captures, 1);
});
function assertHaruCaptureBeforeTeardown(rawSource) {
 // Git may check out JavaScript with CRLF on Windows. Normalize only newline
 // encoding; keep the awaited capture and teardown ordering requirements intact.
 const source=rawSource.replace(/\r\n/g,'\n');
 const start=source.indexOf('} catch (error) {\n  // Keep failures failed');
 const screenshot=source.indexOf('await current.haru.screenshot(',start);
 const capture=source.indexOf("report.haruFailureScreenshot = 'screens/haru-failure.png'",start);
 const close=source.indexOf('current.app.close()',start);
 assert.ok(start>0 && screenshot>start && capture>screenshot && close>capture);
 assert.match(source.slice(screenshot,close),/current\.haru\.screenshot\([^]*timeout: 15000/);
}
test('smoke failure captures the actual Haru window before app teardown even when a transition prerequisite fails',async()=>{
 const fs=await import('node:fs/promises');
 assertHaruCaptureBeforeTeardown(await fs.readFile(new URL('../smoke.mjs',import.meta.url),'utf8'));
});
for (const [name,newline] of [['LF','\n'],['CRLF','\r\n']]) {
 test(`Haru capture ordering accepts ${name} source and rejects early teardown or missing capture`,async()=>{
  const fs=await import('node:fs/promises');
  const source=(await fs.readFile(new URL('../smoke.mjs',import.meta.url),'utf8')).replace(/\r\n/g,'\n');
  const encode=value=>value.replace(/\n/g,newline);
  assertHaruCaptureBeforeTeardown(encode(source));
  const early=source.replace('  if (current?.haru && !current.haru.isClosed()) {',
    '  await current.app.close();\n  if (current?.haru && !current.haru.isClosed()) {');
  assert.notEqual(early,source,'negative fixture must insert an early teardown');
  assert.throws(()=>assertHaruCaptureBeforeTeardown(encode(early)));
  const missing=source.replace(/      await current\.haru\.screenshot\([^\n]+\);\n/,'');
  assert.notEqual(missing,source,'negative fixture must remove the actual screenshot call');
  assert.throws(()=>assertHaruCaptureBeforeTeardown(encode(missing)));
 });
}
