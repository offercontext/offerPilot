import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assertLayout } from './visual.mjs';
const layout = { expanded: true, theme: 'light', fallbackVisible: false, canvasCount: 0, staticImages: 1, runtimeState: 'absent',
  staticImageLoaded: true, staticImageSource: '/assets/haru-window-light-abc123.png', surfaceWithinViewport: true, avatarWithinViewport: true,
  assistantMessageCount: 1, assistantAvatars: [{ loaded: true, alt: 'Haru 头像', source: '/assets/haru-window-light-abc123.png', hidden: false }],
  avatarAboveChat: true, contextAccessible: true, inputAccessible: true, submitAccessible: true, mainWindowAccessible: true,
  collapsedOnlyModel: true, collapsedTransparent: true, documentOverflows: false, horizontalChatOverflow: false };
test('expanded evidence requires a loaded static image and rejects live canvas or failed image', () => {
  assertLayout(layout, { theme: 'light' });
  for (const patch of [{ canvasCount: 1 }, { staticImageLoaded: false }, { runtimeState: 'ready' }, { inputAccessible: false }]) {
    assert.throws(() => assertLayout({ ...layout, ...patch }));
  }
});
test('assistant avatar evidence rejects missing, unloaded, inaccessible or mismatched images', () => {
  assert.throws(() => assertLayout({ ...layout, assistantAvatars: [] }));
  for (const patch of [{ loaded: false }, { alt: '' }, { hidden: true }, { source: '/assets/haru-window-dark-abc123.png' }]) {
    assert.throws(() => assertLayout({ ...layout, assistantAvatars: [{ ...layout.assistantAvatars[0], ...patch }] }));
  }
});
test('collapsed evidence rejects fallback/static replacement and visible chrome', () => {
  const value = { ...layout, expanded: false, canvasCount: 1, staticImages: 0, runtimeState: 'ready',
    contextAvailable: true, contextLost: false, contextAlpha: true, canvasCssTransparent: true, backingWidth: 100, backingHeight: 200, canvasIntersectsAvatar: true };
  assertLayout(value, { expanded: false });
  for (const patch of [{ staticImages: 1 }, { fallbackVisible: true }, { contextLost: true }, { collapsedOnlyModel: false }, { collapsedTransparent: false }]) {
    assert.throws(() => assertLayout({ ...value, ...patch }, { expanded: false }));
  }
});
