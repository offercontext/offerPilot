import assert from 'node:assert/strict';

// Read-only inspection of the real renderer. No image decoding, replacement,
// model stub, raw text, pixel dump or WebGL calls that draw/alter state.
export function observeHaruVisual() {
  const visible = node => {
    if (!node || !node.getClientRects().length) return false;
    for (let parent = node; parent; parent = parent.parentElement) {
      const css = getComputedStyle(parent);
      if (css.visibility !== 'visible' || Number(css.opacity) === 0) return false;
    }
    const box = node.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  const portrait = document.querySelector('.desktop-haru-portrait');
  const state = portrait?.getAttribute('data-runtime-state');
  const runtimeState = ['ready', 'loading', 'failed', 'hidden'].includes(state) ? state : 'unknown';
  const canvases = portrait ? [...portrait.querySelectorAll('canvas')].filter(visible) : [];
  const canvas = canvases.length === 1 ? canvases[0] : null;
  const images = portrait ? [...portrait.querySelectorAll('img')].filter(visible).length : 0;
  const fallbackVisible = Boolean(portrait && [...portrait.querySelectorAll(':scope > span')].some(visible));
  const context = document.querySelector('.desktop-haru-context');
  const input = document.querySelector('#desktop-haru-input');
  const chat = document.querySelector('[aria-label="Haru 对话"]');
  const box = canvas?.getBoundingClientRect();
  const frame = portrait?.getBoundingClientRect();
  let contextAvailable = false;
  let contextLost = true;
  // The product's ready marker means a runtime context already exists. Never
  // call getContext on an uninitialized/loading canvas to fabricate readiness.
  if (runtimeState === 'ready' && canvas && canvas.width > 0 && canvas.height > 0) {
    try {
      const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
      contextAvailable = Boolean(gl);
      contextLost = !gl || gl.isContextLost();
    } catch { /* Missing/lost native context remains a failing observation. */ }
  }
  const unobscured = node => {
    if (!visible(node)) return false;
    const rect = node.getBoundingClientRect();
    const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return top === node || Boolean(top && node.contains(top));
  };
  return { runtimeState, expanded: Boolean(document.querySelector('.desktop-haru.expanded')), visibleCanvases: canvases.length, visibleImages: images, fallbackVisible,
    backingWidth: canvas?.width || 0, backingHeight: canvas?.height || 0, contextAvailable, contextLost,
    canvasWithinPortrait: Boolean(box && frame && box.left >= frame.left - 1 && box.right <= frame.right + 1
      && box.top >= frame.top - 1 && box.bottom <= frame.bottom + 1),
    portraitWithinViewport: Boolean(frame && frame.left >= 0 && frame.right <= innerWidth && frame.top >= 0 && frame.bottom <= innerHeight),
    mediaAboveChat: Boolean(box && frame && visible(chat) && Math.max(box.bottom, frame.bottom) <= chat.getBoundingClientRect().top + 1),
    contextUnobscured: unobscured(context), inputUnobscured: unobscured(input),
    imageQualityRequiresHumanReview: true };
}
export function assertHaruVisual(value, { requireExpanded = true } = {}) {
  assert.equal(value.runtimeState, 'ready', 'Haru runtime must actually finish loading');
  assert.equal(value.visibleCanvases, 1); assert.equal(value.visibleImages, 0);
  assert.equal(value.fallbackVisible, false, 'Haru fallback is not successful model rendering');
  for (const key of ['backingWidth', 'backingHeight']) assert.ok(Number.isFinite(value[key]) && value[key] > 0 && value[key] <= 16384);
  assert.equal(value.contextAvailable, true); assert.equal(value.contextLost, false);
  if (requireExpanded) assert.equal(value.expanded, true, 'Haru conversation must really be expanded');
  for (const key of ['canvasWithinPortrait', 'portraitWithinViewport', ...(value.expanded ? ['mediaAboveChat', 'contextUnobscured', 'inputUnobscured'] : [])]) {
    assert.equal(value[key], true, `Haru visual boundary failed: ${key}`);
  }
}
export async function verifyHaruVisual(page, capture, options) {
  let captured = false;
  try {
    await page.locator('.desktop-haru-portrait[data-runtime-state="ready"]').waitFor();
    // The native window may be hidden between readiness and the next paint.
    // Use Playwright's host-side deadline rather than an unbounded evaluate.
    await page.waitForFunction(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)))),
      undefined, { timeout: 5000 });
    const value = await page.evaluate(observeHaruVisual);
    if (capture) { captured = true; await capture(value); }
    assertHaruVisual(value, options);
    return value;
  } catch (error) {
    // A loading/failed runtime must still retain the actual broken pixels,
    // rather than fail before the requested screenshot callback is reached.
    if (capture && !captured) {
      try { captured = true; await capture(await page.evaluate(observeHaruVisual)); } catch { /* Original validation failure remains a failure. */ }
    }
    throw error;
  }
}
