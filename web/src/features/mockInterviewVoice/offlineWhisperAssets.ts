import asyncifyModuleUrl from 'onnxruntime-web/ort-wasm-simd-threaded.asyncify.mjs?url';
import asyncifyWasmUrl from 'onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm?url';
import standardModuleUrl from 'onnxruntime-web/ort-wasm-simd-threaded.mjs?url';
import standardWasmUrl from 'onnxruntime-web/ort-wasm-simd-threaded.wasm?url';
import type { env as transformersEnv } from '@huggingface/transformers';

// Match Transformers.js 4.2's Safari selection without importing its private API.
function isSafari(): boolean {
  return typeof navigator !== 'undefined'
    && (navigator.vendor ?? '').includes('Apple')
    && !/CriOS|FxiOS|EdgiOS|OPiOS|mercury|brave|Chrome|Android/i.test(navigator.userAgent);
}

/** Keep executable runtime assets in the build, matched to the locked ONNX version. */
export function configureOfflineWhisperAssets(
  env: typeof transformersEnv,
  safari = isSafari(),
  crossOriginIsolated = globalThis.crossOriginIsolated === true,
): void {
  env.backends.onnx.wasm!.wasmPaths = safari
    ? { mjs: standardModuleUrl, wasm: standardWasmUrl }
    : { mjs: asyncifyModuleUrl, wasm: asyncifyWasmUrl };
  // Transformers' WASM cache rewrites the factory into a blob: script. Model
  // caching is separate and must stay enabled for downloaded/offline models.
  env.useWasmCache = false;
  env.backends.onnx.wasm!.proxy = false;
  if (!crossOriginIsolated) env.backends.onnx.wasm!.numThreads = 1;
}
