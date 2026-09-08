/// <reference lib="webworker" />

import { OfflineWhisperRuntime, createTransformersPipeline } from './offlineWhisperRuntime';
import type { OfflineWhisperWorkerRequest, OfflineWhisperWorkerResponse } from './offlineWhisperTypes';

const worker = self as unknown as DedicatedWorkerGlobalScope;
const runtime = new OfflineWhisperRuntime({ createPipeline: createTransformersPipeline });
let activeGeneration = 0;
let cancelledGeneration = 0;

function publish(message: OfflineWhisperWorkerResponse): void {
  if (message.generation !== activeGeneration || message.generation <= cancelledGeneration) return;
  worker.postMessage(message);
}

worker.addEventListener('message', (event: MessageEvent<OfflineWhisperWorkerRequest>) => {
  const request = event.data;
  if (request.type === 'cancel') {
    cancelledGeneration = Math.max(cancelledGeneration, request.generation - 1);
    activeGeneration = request.generation;
    return;
  }
  if (request.type === 'dispose') {
    activeGeneration = request.generation;
    void runtime.dispose().finally(() => worker.close());
    return;
  }
  activeGeneration = request.generation;
  if (request.type === 'prepare') {
    let downloadedBytes = 0;
    void runtime.prepare(request.preferredBackend, ({ loaded, total }) => {
      downloadedBytes = Math.max(downloadedBytes, loaded);
      publish({ type: 'download_progress', generation: request.generation, loaded, total });
    }).then(async (backend) => {
      const cache = typeof caches === 'undefined' ? undefined : await caches.open('offerpilot-offline-whisper-v1');
      const keys = cache ? await cache.keys() : [];
      publish({ type: 'ready', generation: request.generation, backend, cachedBytes: keys.length ? downloadedBytes || undefined : 0 });
    }).catch((error: unknown) => {
      publish({
        type: 'failed',
        generation: request.generation,
        category: 'initialization',
        recoverable: true,
        message: error instanceof Error ? error.message : '离线模型准备失败',
      });
    });
    return;
  }
  publish({ type: 'transcription_progress', generation: request.generation, backend: 'webgpu' });
  void runtime.transcribe(request.audio).then(({ text, backend }) => {
    publish({ type: 'completed', generation: request.generation, text, backend });
  }).catch((error: unknown) => {
    publish({
      type: 'failed',
      generation: request.generation,
      category: 'transcription',
      recoverable: true,
      message: error instanceof Error ? error.message : '离线转写失败',
    });
  });
});
