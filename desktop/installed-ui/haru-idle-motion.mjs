import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

// Decode the exact saved PNG using Electron's native decoder. No model/ticker
// replacement, canvas painting, forced frame progression or screenshot editing.
export function describeNativeFrame({ nativeImage }, base64) {
  const image = nativeImage.createFromBuffer(Buffer.from(base64, 'base64'));
  if (image.isEmpty()) throw new Error('Empty native frame');
  const size = image.getSize();
  if (![size.width, size.height].every(value => Number.isInteger(value) && value > 0 && value <= 2048)) throw new Error('Unexpected frame dimensions');
  const bitmap = image.toBitmap();
  if (bitmap.length !== size.width * size.height * 4) throw new Error('Unexpected bitmap layout');
  let opaque = 0;
  for (let offset = 3; offset < bitmap.length; offset += 4) if (bitmap[offset] > 32) opaque++;
  return { ...size, opaque, bitmapBase64: bitmap.toString('base64') };
}

export function verifyFrameSequence(frames, staticExpected) {
  assert.equal(frames.length, 6);
  for (const frame of frames) {
    assert.equal(frame.width, frames[0].width);
    assert.equal(frame.height, frames[0].height);
    assert.ok(frame.opaque > 1000, 'A blank or unloaded model cannot prove motion');
    assert.match(frame.digest, /^[a-f0-9]{64}$/);
  }
  const changes = frames.slice(1).filter((frame, index) => frame.digest !== frames[index].digest).length;
  if (staticExpected) assert.equal(changes, 0, 'Reduced motion or animation off must stay static');
  else assert.ok(changes >= 2, 'Visible idle model must change across multiple intervals');
  return { frameCount: frames.length, changes, staticExpected, frames };
}

export async function probeIdleMotion({ app, haru, capture, label, reducedMotion, animationOff = false, record = async () => {} }) {
  await record({ phase: 'await-ready', expectedReducedMotion: reducedMotion, animationOff });
  await haru.locator('.desktop-haru.collapsed .desktop-haru-portrait[data-runtime-state="ready"] canvas').waitFor();
  const observedReducedMotion = await haru.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches);
  const unexpectedNodes = await haru.locator('.desktop-haru-chat, .desktop-haru img').count();
  await record({ phase: 'preconditions', expectedReducedMotion: reducedMotion, observedReducedMotion, animationOff, unexpectedNodes });
  assert.equal(observedReducedMotion, reducedMotion);
  assert.equal(unexpectedNodes, 0);
  const frames = [];
  for (let index = 0; index < 6; index++) {
    await new Promise(resolve => setTimeout(resolve, 400));
    // Preference changes may mount a fresh canvas asynchronously. Every saved
    // frame must follow real runtime readiness, not a formerly ready canvas.
    await haru.locator('.desktop-haru.collapsed .desktop-haru-portrait[data-runtime-state="ready"] canvas').waitFor();
    const bytes = await capture(`${label}-${index}`, haru, { omitBackground: true });
    const { bitmapBase64, ...frame } = await app.evaluate(describeNativeFrame, bytes.toString('base64'));
    const bitmap = Buffer.from(bitmapBase64, 'base64');
    assert.ok(bitmap.length <= 2048 * 2048 * 4);
    assert.equal(bitmap.length, frame.width * frame.height * 4);
    frames.push({ ...frame, digest: createHash('sha256').update(bitmap).digest('hex') });
  }
  await record({ phase: 'frames', frames, expectedReducedMotion: reducedMotion, animationOff });
  return { ...verifyFrameSequence(frames, reducedMotion || animationOff), rendererReducedMotion: reducedMotion, animationOff };
}
