import { describe, expect, it, vi } from 'vitest';
import { OfflineWhisperControllerImpl } from './offlineWhisperController';
import type { OfflineWhisperWorkerLike } from './offlineWhisperTypes';

class FakeWorker implements OfflineWhisperWorkerLike {
  listeners = new Set<(event: MessageEvent) => void>();
  messages: unknown[] = [];
  postMessage(message: unknown) { this.messages.push(message); }
  addEventListener(_type: 'message', listener: (event: MessageEvent) => void) { this.listeners.add(listener); }
  removeEventListener(_type: 'message', listener: (event: MessageEvent) => void) { this.listeners.delete(listener); }
  terminate = vi.fn();
  emit(data: unknown) { this.listeners.forEach((listener) => listener({ data } as MessageEvent)); }
}

describe('OfflineWhisperController', () => {
  it('publishes honest download progress and marks ready only after worker completion', async () => {
    const worker = new FakeWorker();
    const store = {
      inspect: vi.fn(async () => ({ ready: false, cachedBytes: 0 })),
      checkCapacity: vi.fn(async () => 'sufficient' as const),
      markReady: vi.fn(async () => undefined),
      remove: vi.fn(async () => undefined),
    };
    const controller = new OfflineWhisperControllerImpl({ createWorker: () => worker, store });
    const preparing = controller.prepare();
    await Promise.resolve();
    worker.emit({ type: 'download_progress', generation: 1, loaded: 20, total: 100 });
    expect(controller.getState()).toEqual({ status: 'downloading', receivedBytes: 20, totalBytes: 100 });
    worker.emit({ type: 'ready', generation: 1, backend: 'webgpu', cachedBytes: 100 });
    await expect(preparing).resolves.toBe('webgpu');
    expect(store.markReady).toHaveBeenCalledWith(100);
    expect(controller.getState()).toMatchObject({ status: 'ready', backend: 'webgpu' });
  });

  it('blocks download when capacity is known to be insufficient', async () => {
    const worker = new FakeWorker();
    const controller = new OfflineWhisperControllerImpl({
      createWorker: () => worker,
      store: {
        inspect: vi.fn(async () => ({ ready: false, cachedBytes: 0 })),
        checkCapacity: vi.fn(async () => 'insufficient' as const),
        markReady: vi.fn(async () => undefined),
        remove: vi.fn(async () => undefined),
      },
    });
    await expect(controller.prepare()).rejects.toThrow('存储空间不足');
    expect(worker.messages).toHaveLength(0);
  });

  it('removes cached data and ignores late worker messages', async () => {
    const worker = new FakeWorker();
    const store = {
      inspect: vi.fn(async () => ({ ready: true, cachedBytes: 100 })),
      checkCapacity: vi.fn(async () => 'sufficient' as const),
      markReady: vi.fn(async () => undefined),
      remove: vi.fn(async () => undefined),
    };
    const controller = new OfflineWhisperControllerImpl({ createWorker: () => worker, store });
    await controller.check();
    await controller.remove();
    worker.emit({ type: 'ready', generation: 1, backend: 'wasm', cachedBytes: 100 });
    expect(store.remove).toHaveBeenCalledOnce();
    expect(controller.getState()).toEqual({ status: 'not_downloaded' });
  });

  it('rehydrates the cached pipeline before the first transcription after reload', async () => {
    const worker = new FakeWorker();
    const store = {
      inspect: vi.fn(async () => ({ ready: true, cachedBytes: 321 })),
      checkCapacity: vi.fn(async () => 'insufficient' as const),
      markReady: vi.fn(async () => undefined),
      remove: vi.fn(async () => undefined),
    };
    const controller = new OfflineWhisperControllerImpl({ createWorker: () => worker, store });
    await controller.check();

    const transcription = controller.transcribe(new Float32Array([0.1, 0.2]));
    await Promise.resolve();
    expect(worker.messages).toEqual([
      { type: 'prepare', generation: 1, preferredBackend: 'webgpu' },
    ]);

    worker.emit({ type: 'ready', generation: 1, backend: 'wasm', cachedBytes: 321 });
    await vi.waitFor(() => {
      expect(worker.messages[1]).toMatchObject({ type: 'transcribe', generation: 2, sampleRate: 16000, language: 'zh' });
    });

    worker.emit({ type: 'transcription_progress', generation: 2, backend: 'webgpu', progress: 0.5 });
    expect(controller.getState()).toEqual({ status: 'transcribing', backend: 'wasm', progress: 0.5 });

    worker.emit({ type: 'completed', generation: 2, text: ' 筱哲的回答 ', backend: 'wasm' });
    await expect(transcription).resolves.toEqual({ text: '筱哲的回答', backend: 'wasm' });
    expect(store.checkCapacity).not.toHaveBeenCalled();
    expect(controller.getState()).toEqual({
      status: 'ready',
      modelVersion: expect.any(String),
      cachedBytes: 321,
      backend: 'wasm',
    });
  });

  it('rejects silence before loading or calling the worker', async () => {
    const worker = new FakeWorker();
    const store = {
      inspect: vi.fn(async () => ({ ready: true, cachedBytes: 321 })),
      checkCapacity: vi.fn(async () => 'sufficient' as const),
      markReady: vi.fn(async () => undefined),
      remove: vi.fn(async () => undefined),
    };
    const controller = new OfflineWhisperControllerImpl({ createWorker: () => worker, store });
    await controller.check();
    await expect(controller.transcribe(new Float32Array(16000))).rejects.toThrow('没有检测到清晰语音');
    expect(worker.messages).toHaveLength(0);
  });

  it('terminates active model work when the user cancels', async () => {
    const worker = new FakeWorker();
    const store = {
      inspect: vi.fn(async () => ({ ready: false, cachedBytes: 0 })),
      checkCapacity: vi.fn(async () => 'sufficient' as const),
      markReady: vi.fn(async () => undefined),
      remove: vi.fn(async () => undefined),
    };
    const controller = new OfflineWhisperControllerImpl({ createWorker: () => worker, store });
    const preparing = controller.prepare();
    await Promise.resolve();

    controller.cancel();

    await expect(preparing).rejects.toThrow('模型准备已取消');
    expect(worker.terminate).toHaveBeenCalledOnce();
    await vi.waitFor(() => expect(controller.getState()).toEqual({ status: 'not_downloaded' }));
  });
});
