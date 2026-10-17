// Run from the repository root:
// node --disallow-code-generation-from-strings web/src/features/mockInterviewVoice/offlineWhisperAssets.smoke.mjs
// This tests local ORT/WASM inference, not browser CSP, WebGPU, or Whisper quality.
import assert from 'node:assert/strict';
import * as ort from 'onnxruntime-web/webgpu';

assert.throws(() => new Function('return 1'), EvalError, 'Run with JS code generation disabled');
// Synthetic ONNX Identity graph: float32 x[2] -> y[2], opset 13, IR version 8.
const model = Buffer.from(
  'CAg6RwoQCgF4EgF5IghJZGVudGl0eRIRU3ludGhldGljSWRlbnRpdHlaDwoBeBIKCggIARIECgIIAmIPCgF5EgoKCAgBEgQKAggCQgQKABAN',
  'base64',
);
ort.env.wasm.wasmPaths = {
  mjs: import.meta.resolve('onnxruntime-web/ort-wasm-simd-threaded.asyncify.mjs'),
  wasm: import.meta.resolve('onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm'),
};
ort.env.wasm.numThreads = 1;
ort.env.wasm.proxy = false;
const session = await ort.InferenceSession.create(model, { executionProviders: ['wasm'] });
try {
  const output = await session.run({ x: new ort.Tensor('float32', new Float32Array([3, 7]), [2]) });
  assert.deepEqual(Array.from(output.y.data), [3, 7]);
  console.log('PASS: local synthetic ONNX inference with JS code generation disabled');
} finally {
  await session.release();
}
