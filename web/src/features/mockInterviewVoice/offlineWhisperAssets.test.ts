import { describe, expect, it } from 'vitest';
import type { env as transformersEnv } from '@huggingface/transformers';
import { configureOfflineWhisperAssets } from './offlineWhisperAssets';

function environment() {
  return {
    backends: { onnx: { wasm: { wasmPaths: 'https://cdn.jsdelivr.net/ort/', numThreads: 4, proxy: true } } },
    useWasmCache: true,
    useCustomCache: true,
    customCache: { match: async () => undefined, put: async () => undefined },
  } as unknown as typeof transformersEnv;
}

describe('offline Whisper packaged assets', () => {
  it('replaces remote executables and disables blob module caching without touching model caching', () => {
    const env = environment();
    const cache = env.customCache;
    configureOfflineWhisperAssets(env, false, false);
    const paths = env.backends.onnx.wasm!.wasmPaths as { mjs: string; wasm: string };
    expect(paths.mjs).toContain('ort-wasm-simd-threaded.asyncify.mjs');
    expect(paths.wasm).toContain('ort-wasm-simd-threaded.asyncify.wasm');
    for (const path of Object.values(paths)) expect(path).not.toMatch(/^(https?:|blob:|data:)/);
    expect(env.useWasmCache).toBe(false);
    expect(env.useCustomCache).toBe(true);
    expect(env.customCache).toBe(cache);
    expect(env.backends.onnx.wasm!.numThreads).toBe(1);
    expect(env.backends.onnx.wasm!.proxy).toBe(false);
  });

  it('preserves the upstream Safari binary selection and isolated thread configuration', () => {
    const env = environment();
    configureOfflineWhisperAssets(env, true, true);
    const paths = env.backends.onnx.wasm!.wasmPaths as { mjs: string; wasm: string };
    expect(paths.mjs).toContain('ort-wasm-simd-threaded.mjs');
    expect(paths.wasm).toContain('ort-wasm-simd-threaded.wasm');
    expect(env.backends.onnx.wasm!.numThreads).toBe(4);
  });
});
