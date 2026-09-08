import { describe, expect, it, vi } from 'vitest';
import { OfflineWhisperCoordinator, chooseTranscriptionPath } from './offlineWhisperCoordinator';
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

describe('offline Whisper coordinator', () => {
  it('prefers a non-empty native transcript and otherwise selects Whisper only when ready', () => {
    expect(chooseTranscriptionPath(' 已有文字 ', true)).toBe('native');
    expect(chooseTranscriptionPath('', true)).toBe('whisper');
    expect(chooseTranscriptionPath('', false)).toBe('manual');
  });

  it('drops late generations and completes the active request', async () => {
    const worker = new FakeWorker();
    const coordinator = new OfflineWhisperCoordinator(() => worker);
    const first = coordinator.transcribe(new Float32Array([0.1]));
    const second = coordinator.transcribe(new Float32Array([0.2]));
    worker.emit({ type: 'completed', generation: 1, text: '旧结果', backend: 'wasm' });
    worker.emit({ type: 'completed', generation: 2, text: '新结果', backend: 'webgpu' });
    await expect(second).resolves.toEqual({ text: '新结果', backend: 'webgpu' });
    await expect(first).rejects.toThrow('转写已被新的操作替代');
  });

  it('cancels and disposes without accepting a late result', async () => {
    const worker = new FakeWorker();
    const coordinator = new OfflineWhisperCoordinator(() => worker);
    const pending = coordinator.transcribe(new Float32Array([0.1]));
    coordinator.cancel();
    worker.emit({ type: 'completed', generation: 1, text: '迟到结果', backend: 'wasm' });
    await expect(pending).rejects.toThrow('转写已取消');
    coordinator.dispose();
    expect(worker.terminate).toHaveBeenCalledOnce();
  });
});
