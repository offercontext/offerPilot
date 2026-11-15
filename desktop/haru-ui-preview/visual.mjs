import assert from 'node:assert/strict';

// Read-only: never paints substitute pixels, patches WebGL or forces readiness.
export function observeLayout() {
  const visible = node => Boolean(node && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
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
    return { loaded: Boolean(image?.complete && image.naturalWidth > 0), alt: image?.alt || '', source: image?.getAttribute('src') || null, hidden: Boolean(image?.closest('[aria-hidden=true]')) };
  });
  const ready = portrait?.getAttribute('data-runtime-state') === 'ready';
  let contextAvailable = false;
  let contextLost = true;
  let contextAlpha = false;
  if (ready && canvas) {
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    contextAvailable = Boolean(gl); contextLost = !gl || gl.isContextLost(); contextAlpha = gl?.getContextAttributes()?.alpha === true;
  }
  const expanded = root?.classList.contains('expanded') || false;
  const avatarBox = rect(avatar); const canvasBox = rect(canvas);
  const chat = document.querySelector('.desktop-haru-chat');
  const transparent = node => !node || ['rgba(0, 0, 0, 0)', 'transparent'].includes(getComputedStyle(node).backgroundColor);
  return {
    width: innerWidth, height: innerHeight, expanded, theme: root?.getAttribute('data-theme'),
    runtimeState: portrait?.getAttribute('data-runtime-state') || 'absent', canvasCount: document.querySelectorAll('.desktop-haru canvas').length,
    backingWidth: canvas?.width || 0, backingHeight: canvas?.height || 0,
    contextAvailable, contextLost, contextAlpha, canvasCssTransparent: transparent(canvas), fallbackVisible: [...(portrait?.querySelectorAll(':scope > span') || [])].some(visible),
    staticImages: document.querySelectorAll('.desktop-haru-static-portrait').length,
    staticImageLoaded: Boolean(staticImage?.complete && staticImage.naturalWidth > 0 && visible(staticImage)),
    staticImageSource: staticImage?.getAttribute('src') || null,
    assistantMessageCount: document.querySelectorAll('.desktop-haru-messages article[data-role=assistant]').length,
    assistantAvatars,
    surfaceWithinViewport: within(rect(root)), avatarWithinViewport: within(avatarBox),
    canvasIntersectsAvatar: Boolean(canvasBox && avatarBox && Math.min(canvasBox.right, avatarBox.right) > Math.max(canvasBox.left, avatarBox.left)
      && Math.min(canvasBox.bottom, avatarBox.bottom) > Math.max(canvasBox.top, avatarBox.top)),
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
export function assertLayout(value, { expanded = true, theme } = {}) {
  assert.equal(value.expanded, expanded);
  if (theme) assert.equal(value.theme, theme);
  assert.equal(value.fallbackVisible, false);
  assert.equal(value.canvasCount, expanded ? 0 : 1);
  assert.equal(value.staticImages, expanded ? 1 : 0);
  if (expanded) {
    assert.equal(value.runtimeState, 'absent');
    assert.equal(value.staticImageLoaded, true);
    assert.match(value.staticImageSource, /haru-window-(?:light|dark)(?:-[A-Za-z0-9_-]+)?\.png$/);
    assert.equal(value.assistantAvatars.length, value.assistantMessageCount);
    for (const avatar of value.assistantAvatars) {
      assert.equal(avatar.loaded, true); assert.equal(avatar.alt, 'Haru 头像');
      assert.equal(avatar.source, value.staticImageSource); assert.equal(avatar.hidden, false);
    }
  } else {
    assert.equal(value.runtimeState, 'ready'); assert.equal(value.contextAvailable, true); assert.equal(value.contextLost, false); assert.equal(value.contextAlpha, true); assert.equal(value.canvasCssTransparent, true);
    assert.ok(value.backingWidth > 0 && value.backingHeight > 0); assert.equal(value.canvasIntersectsAvatar, true);
  }
  for (const key of ['surfaceWithinViewport', 'avatarWithinViewport', 'avatarAboveChat', 'contextAccessible', 'inputAccessible', 'submitAccessible', 'mainWindowAccessible', 'collapsedOnlyModel', 'collapsedTransparent']) {
    assert.equal(value[key], true, `Visual check failed: ${key}`);
  }
  assert.equal(value.documentOverflows, false); assert.equal(value.horizontalChatOverflow, false);
}
export async function settleVisual(page, expanded) {
  if (expanded) await page.waitForFunction(() => {
    const image = document.querySelector('.desktop-haru-static-portrait');
    const messageImages = [...document.querySelectorAll('[data-role=assistant] .desktop-haru-message-avatar img')];
    return image?.complete && image.naturalWidth > 0 && messageImages.every(item => item.complete && item.naturalWidth > 0) && !document.querySelector('.desktop-haru canvas');
  }, undefined, { timeout: 25_000 });
  else await page.locator('.desktop-haru-portrait[data-runtime-state="ready"]').waitFor({ timeout: 25_000 });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)))), undefined, { timeout: 5000 });
}
