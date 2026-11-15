/** Browser predicate: visibility alone can pass while AntD's enter motion is transparent. */
export function dialogIsSettled(element) {
  if (!element?.isConnected) return false;
  const rect = element.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  for (let node = element; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility !== 'visible' || Number(style.opacity) < 0.99) return false;
    if (node.getAnimations().some(animation => animation.playState === 'running' || animation.playState === 'pending')) return false;
  }
  return !element.getAnimations({ subtree: true }).some(animation => animation.playState === 'running' || animation.playState === 'pending');
}

export async function waitForDialogSettled(dialog) {
  await dialog.waitFor({ state: 'visible' });
  // Let the browser register enter-motion styles before inspecting animations.
  await dialog.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const element = await dialog.elementHandle();
  if (!element) throw new Error('Expected dialog disappeared before capture');
  try {
    await dialog.page().waitForFunction(dialogIsSettled, element, { timeout: 15_000, polling: 'raf' });
  } finally {
    await element.dispose();
  }
}

/** Reject an empty/transparent or flat placeholder frame, without inspecting user data. */
export function hasPaintedCharacter({ pixels, opaquePixels, minChannel, maxChannel }) {
  return pixels > 0 && opaquePixels >= Math.max(100, pixels * 0.005) && maxChannel - minChannel >= 30;
}

export async function waitForPaintedCharacter(page) {
  const canvas = page.locator('.desktop-haru-portrait[data-runtime-state="ready"] canvas');
  await canvas.waitFor({ state: 'visible', timeout: 30_000 });
  const deadline = Date.now() + 30_000;
  let consecutive = 0;
  while (Date.now() < deadline) {
    // Read actual composited pixels, not merely the existence of a WebGL canvas.
    const png = await canvas.screenshot({ omitBackground: true });
    const frame = await page.evaluate(async base64 => {
      const bytes = Uint8Array.from(atob(base64), char => char.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      const scratch = new OffscreenCanvas(bitmap.width, bitmap.height);
      const context = scratch.getContext('2d');
      context.drawImage(bitmap, 0, 0);
      const data = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
      let opaquePixels = 0, minChannel = 255, maxChannel = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] < 64) continue;
        opaquePixels++;
        const brightness = (data[i] + data[i + 1] + data[i + 2]) / 3;
        minChannel = Math.min(minChannel, brightness); maxChannel = Math.max(maxChannel, brightness);
      }
      bitmap.close();
      return { pixels: data.length / 4, opaquePixels, minChannel, maxChannel };
    }, png.toString('base64'));
    consecutive = hasPaintedCharacter(frame) ? consecutive + 1 : 0;
    if (consecutive >= 2) return;
    await page.waitForTimeout(100);
  }
  throw new Error('Haru character did not paint two nonempty frames before capture');
}
