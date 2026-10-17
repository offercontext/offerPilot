import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { describeNativeFrame, verifyFrameSequence } from '../haru-idle-motion.mjs';

const frames = Array.from({ length: 6 }, (_, index) => ({ width: 260, height: 340, opaque: 12000, digest: String(index).repeat(64) }));
test('motion proof rejects frozen, blank, missing and resized frames', () => {
  assert.equal(verifyFrameSequence(frames, false).changes, 5);
  const frozen = frames.map(frame => ({ ...frame, digest: frames[0].digest }));
  assert.equal(verifyFrameSequence(frozen, true).changes, 0);
  assert.throws(() => verifyFrameSequence(frozen, false));
  assert.throws(() => verifyFrameSequence(frames, true));
  assert.throws(() => verifyFrameSequence(frames.slice(1), false));
  assert.throws(() => verifyFrameSequence(frames.map(frame => ({ ...frame, opaque: 0 })), false));
  assert.throws(() => verifyFrameSequence(frames.map((frame, index) => ({ ...frame, width: 260 + index })), false));
});

test('native decoder returns exact bounded bitmap bytes without global require and rejects decode/layout errors', () => {
  const bitmap = Buffer.alloc(16); bitmap[3] = 255; bitmap[7] = 33;
  const image = { isEmpty: () => false, getSize: () => ({ width: 2, height: 2 }), toBitmap: () => bitmap };
  const nativeImage = { createFromBuffer: bytes => { assert.equal(bytes.toString(), 'png'); return image; } };
  const decode = vm.runInNewContext(`(${describeNativeFrame.toString()})`, { Buffer });
  const result = decode({ nativeImage }, Buffer.from('png').toString('base64'));
  assert.equal(result.opaque, 2);
  assert.deepEqual(Buffer.from(result.bitmapBase64, 'base64'), bitmap);
  image.isEmpty = () => true;
  assert.throws(() => decode({ nativeImage }, 'cG5n'), /Empty native frame/);
  image.isEmpty = () => false;
  image.toBitmap = () => Buffer.alloc(4);
  assert.throws(() => decode({ nativeImage }, 'cG5n'), /Unexpected bitmap layout/);
  image.getSize = () => ({ width: 2049, height: 2 });
  assert.throws(() => decode({ nativeImage }, 'cG5n'), /Unexpected frame dimensions/);
});
