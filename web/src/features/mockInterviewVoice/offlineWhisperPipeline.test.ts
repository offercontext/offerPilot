import { afterEach, describe, expect, it, vi } from 'vitest';
import { createTransformersPipeline } from './offlineWhisperRuntime';
import { OFFLINE_WHISPER_MANIFEST } from './offlineWhisperManifest';

const mocked = vi.hoisted(() => ({
  env: {
    backends: { onnx: { wasm: { wasmPaths: {}, numThreads: 0, proxy: true } } },
    useWasmCache: true,
    useCustomCache: false,
    customCache: undefined as unknown,
  },
  pipeline: vi.fn(),
}));
vi.mock('@huggingface/transformers', () => mocked);

afterEach(() => vi.unstubAllGlobals());

describe('offline Whisper pipeline initialization', () => {
  it('applies local executable settings before pipeline construction while preserving the model cache', async () => {
    const cache = { match: vi.fn(), put: vi.fn() };
    const open = vi.fn(async () => cache);
    vi.stubGlobal('caches', { open });
    vi.stubGlobal('navigator', { vendor: '', userAgent: 'Windows Chromium' });
    vi.stubGlobal('crossOriginIsolated', false);
    const pipeline = vi.fn(async () => ({ text: 'synthetic' }));
    mocked.pipeline.mockImplementationOnce(async () => {
      expect(mocked.env.useWasmCache).toBe(false);
      expect(mocked.env.backends.onnx.wasm).toMatchObject({ numThreads: 1, proxy: false });
      expect(mocked.env.backends.onnx.wasm.wasmPaths).toMatchObject({
        mjs: expect.stringContaining('ort-wasm-simd-threaded.asyncify.mjs'),
        wasm: expect.stringContaining('ort-wasm-simd-threaded.asyncify.wasm'),
      });
      expect(mocked.env.useCustomCache).toBe(true);
      expect(mocked.env.customCache).toBe(cache);
      return pipeline;
    });
    await expect(createTransformersPipeline('wasm')).resolves.toBe(pipeline);
    expect(open).toHaveBeenCalledWith(OFFLINE_WHISPER_MANIFEST.cacheNamespace);
    expect(mocked.pipeline).toHaveBeenCalledWith('automatic-speech-recognition', OFFLINE_WHISPER_MANIFEST.modelId,
      expect.objectContaining({ revision: OFFLINE_WHISPER_MANIFEST.revision, device: 'wasm' }));
  });
});
