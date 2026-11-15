import assert from 'node:assert/strict';

// Read-only: never paints substitute pixels, patches WebGL or forces readiness.
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
  // Never publish arbitrary renderer URLs/text through installed evidence.
  const imageSource = node => {
    const source = node?.getAttribute('src') || '';
    const matched = source.match(/(?:^|\/)haru-window-(?:light|dark)(?:-[A-Za-z0-9_-]+)?\.png$/);
    return matched ? matched[0].replace(/^\//, '') : null;
  };
  const rect = node => node?.getBoundingClientRect();
  const within = box => Boolean(box && box.left >= -1 && box.top >= -1 && box.right <= innerWidth + 1 && box.bottom <= innerHeight + 1);
  const accessible = node => {
    if (!visible(node)) return false;
    const box = rect(node);
    const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return within(box) && Boolean(top && (node === top || node.contains(top)));
  };
  const root = document.querySelector('.desktop-haru');
  const avatar = document.querySelector('.desktop-haru-avatar');
  const portrait = document.querySelector('.desktop-haru-portrait');
  const canvas = portrait?.querySelector('canvas');
  const staticImage = document.querySelector('.desktop-haru-static-portrait');
  const assistantAvatars = [...document.querySelectorAll('[data-role=assistant] .desktop-haru-message-avatar')].map(node => {
    const image = node.querySelector('img');
    return { loaded: Boolean(image?.complete && image.naturalWidth > 0), alt: image?.alt === 'Haru 头像' ? 'Haru 头像' : null, source: imageSource(image), hidden: Boolean(image?.closest('[aria-hidden=true]')) };
  });
  const ready = portrait?.getAttribute('data-runtime-state') === 'ready';
  const canvasVisible = visible(canvas);
  let contextAvailable = false;
  let contextLost = true;
  let contextAlpha = false;
  if (ready && canvasVisible && canvas.width > 0 && canvas.height > 0) {
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    contextAvailable = Boolean(gl); contextLost = !gl || gl.isContextLost(); contextAlpha = gl?.getContextAttributes()?.alpha === true;
  }
  const expanded = root?.classList.contains('expanded') || false;
  const avatarBox = rect(avatar); const canvasBox = rect(canvas); const portraitBox = rect(portrait);
  const chat = document.querySelector('.desktop-haru-chat');
  const transparent = node => !node || ['rgba(0, 0, 0, 0)', 'transparent'].includes(getComputedStyle(node).backgroundColor);
  return {
    width: innerWidth, height: innerHeight, expanded, theme: ['light', 'dark'].includes(root?.getAttribute('data-theme')) ? root.getAttribute('data-theme') : 'unknown',
    runtimeState: !portrait ? 'absent' : ['ready', 'loading', 'failed', 'hidden'].includes(portrait.getAttribute('data-runtime-state')) ? portrait.getAttribute('data-runtime-state') : 'unknown', canvasCount: document.querySelectorAll('.desktop-haru canvas').length,
    backingWidth: canvas?.width || 0, backingHeight: canvas?.height || 0, canvasVisible,
    contextAvailable, contextLost, contextAlpha, canvasCssTransparent: transparent(canvas), fallbackVisible: [...(portrait?.querySelectorAll(':scope > span') || [])].some(visible),
    staticImages: document.querySelectorAll('.desktop-haru-static-portrait').length,
    staticImageLoaded: Boolean(staticImage?.complete && staticImage.naturalWidth > 0 && visible(staticImage)),
    staticImageSource: imageSource(staticImage),
    assistantMessageCount: document.querySelectorAll('.desktop-haru-messages article[data-role=assistant]').length,
    assistantAvatars,
    surfaceWithinViewport: within(rect(root)), avatarWithinViewport: within(avatarBox),
    canvasIntersectsAvatar: Boolean(canvasBox && avatarBox && Math.min(canvasBox.right, avatarBox.right) > Math.max(canvasBox.left, avatarBox.left)
      && Math.min(canvasBox.bottom, avatarBox.bottom) > Math.max(canvasBox.top, avatarBox.top)),
    canvasWithinPortrait: Boolean(canvasBox && portraitBox && canvasBox.left >= portraitBox.left - 1 && canvasBox.right <= portraitBox.right + 1
      && canvasBox.top >= portraitBox.top - 1 && canvasBox.bottom <= portraitBox.bottom + 1),
    portraitWithinViewport: within(portraitBox),
    avatarAboveChat: !expanded || Boolean(avatarBox && rect(chat) && avatarBox.bottom <= rect(chat).top + 2),
    contextAccessible: !expanded || accessible(document.querySelector('.desktop-haru-context-toggle')),
    inputAccessible: !expanded || accessible(document.querySelector('#desktop-haru-input')),
    submitAccessible: !expanded || accessible(document.querySelector('.desktop-haru-submit')),
    mainWindowAccessible: !expanded || accessible(document.querySelector('[aria-label="打开 OfferPilot 主窗口"]')),
    collapsedOnlyModel: expanded || Boolean(root?.children.length === 1 && avatar && !root.querySelector('header,nav,.desktop-haru-chat,.desktop-haru-speech,.desktop-haru-presence,.desktop-haru-expand-hint,img')),
    collapsedTransparent: expanded || [document.documentElement, document.body, root, avatar].every(transparent),
    documentOverflows: document.documentElement.scrollWidth > innerWidth + 1 || document.documentElement.scrollHeight > innerHeight + 1,
    horizontalChatOverflow: Boolean(chat && chat.scrollWidth > chat.clientWidth + 1),
    messagesScroll: Boolean(document.querySelector('.desktop-haru-messages')?.scrollHeight > document.querySelector('.desktop-haru-messages')?.clientHeight),
    humanPortraitReviewRequired: true,
  };
}
export function assertHaruVisual(value, { requireExpanded: expanded = true, theme } = {}) {
  assert.equal(value.expanded, expanded);
  assert.ok(['light', 'dark'].includes(value.theme));
  if (theme) assert.equal(value.theme, theme);
  assert.equal(value.fallbackVisible, false);
  assert.equal(value.canvasCount, expanded ? 0 : 1);
  assert.equal(value.staticImages, expanded ? 1 : 0);
  if (expanded) {
    assert.equal(value.runtimeState, 'absent');
    assert.equal(value.staticImageLoaded, true);
    assert.match(value.staticImageSource, new RegExp(`^haru-window-${value.theme}(?:-[A-Za-z0-9_-]+)?\\.png$`));
    assert.equal(value.assistantAvatars.length, value.assistantMessageCount);
    for (const avatar of value.assistantAvatars) {
      assert.equal(avatar.loaded, true); assert.equal(avatar.alt, 'Haru 头像');
      assert.equal(avatar.source, value.staticImageSource); assert.equal(avatar.hidden, false);
    }
  } else {
    assert.equal(value.canvasVisible, true);
    assert.equal(value.runtimeState, 'ready'); assert.equal(value.contextAvailable, true); assert.equal(value.contextLost, false); assert.equal(value.contextAlpha, true); assert.equal(value.canvasCssTransparent, true);
    assert.ok([value.backingWidth, value.backingHeight].every(size => Number.isFinite(size) && size > 0 && size <= 16384)); assert.equal(value.canvasIntersectsAvatar, true);
    assert.equal(value.canvasWithinPortrait, true); assert.equal(value.portraitWithinViewport, true);
  }
  for (const key of ['surfaceWithinViewport', 'avatarWithinViewport', 'avatarAboveChat', 'contextAccessible', 'inputAccessible', 'submitAccessible', 'mainWindowAccessible', 'collapsedOnlyModel', 'collapsedTransparent']) {
    assert.equal(value[key], true, `Visual check failed: ${key}`);
  }
  assert.equal(value.documentOverflows, false); assert.equal(value.horizontalChatOverflow, false);
}
async function settleVisual(page, expanded) {
  if (expanded) await page.waitForFunction(() => {
    const image = document.querySelector('.desktop-haru-static-portrait');
    const messageImages = [...document.querySelectorAll('[data-role=assistant] .desktop-haru-message-avatar img')];
    return image?.complete && image.naturalWidth > 0 && messageImages.every(item => item.complete && item.naturalWidth > 0) && !document.querySelector('.desktop-haru canvas');
  }, undefined, { timeout: 25_000 });
  else await page.locator('.desktop-haru-portrait[data-runtime-state="ready"]').waitFor({ timeout: 25_000 });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)))), undefined, { timeout: 5000 });
}

export async function verifyHaruVisual(page, capture, options = {}) {
  let captured = false;
  try {
    await settleVisual(page, options.requireExpanded !== false);
    const value = await page.evaluate(observeHaruVisual);
    if (capture) { captured = true; await capture(value); }
    assertHaruVisual(value, options);
    return value;
  } catch (error) {
    // Preserve the actual failed frame before teardown; failure stays failed.
    if (capture && !captured) {
      try { await capture(await page.evaluate(observeHaruVisual)); } catch { /* Original failure wins. */ }
    }
    throw error;
  }
}
