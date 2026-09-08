import type {
  OfflineWhisperBackend,
  OfflineWhisperWorkerLike,
  OfflineWhisperWorkerResponse,
} from './offlineWhisperTypes';

export type TranscriptionPath = 'native' | 'whisper' | 'manual';

export function chooseTranscriptionPath(nativeTranscript: string, modelReady: boolean): TranscriptionPath {
  if (nativeTranscript.trim()) return 'native';
  return modelReady ? 'whisper' : 'manual';
}

type Pending = {
  generation: number;
  resolve: (value: { text: string; backend: OfflineWhisperBackend }) => void;
  reject: (reason: Error) => void;
};

export class OfflineWhisperCoordinator {
  private worker?: OfflineWhisperWorkerLike;
  private generation = 0;
  private pending?: Pending;
  private disposed = false;

  constructor(private readonly createWorker: () => OfflineWhisperWorkerLike) {}

  private readonly onMessage = (event: MessageEvent<OfflineWhisperWorkerResponse>) => {
    const message = event.data;
    if (!this.pending || message.generation !== this.pending.generation) return;
    if (message.type === 'completed') {
      const pending = this.pending;
      this.pending = undefined;
      pending.resolve({ text: message.text.trim(), backend: message.backend });
    } else if (message.type === 'failed') {
      const pending = this.pending;
      this.pending = undefined;
      pending.reject(new Error(message.message || '离线转写失败'));
    }
  };

  private ensureWorker(): OfflineWhisperWorkerLike {
    if (this.disposed) throw new Error('离线转写已关闭');
    if (!this.worker) {
      this.worker = this.createWorker();
      this.worker.addEventListener('message', this.onMessage);
    }
    return this.worker;
  }

  transcribe(audio: Float32Array): Promise<{ text: string; backend: OfflineWhisperBackend }> {
    const worker = this.ensureWorker();
    const generation = ++this.generation;
    if (this.pending) this.pending.reject(new Error('转写已被新的操作替代'));
    return new Promise((resolve, reject) => {
      this.pending = { generation, resolve, reject };
      worker.postMessage({ type: 'transcribe', generation, audio, sampleRate: 16000, language: 'zh' }, [audio.buffer]);
    });
  }

  cancel(): void {
    const generation = ++this.generation;
    if (this.pending) {
      this.pending.reject(new Error('转写已取消'));
      this.pending = undefined;
    }
    this.worker?.postMessage({ type: 'cancel', generation });
  }

  dispose(): void {
    if (this.disposed) return;
    this.cancel();
    this.disposed = true;
    if (this.worker) {
      this.worker.removeEventListener('message', this.onMessage);
      this.worker.postMessage({ type: 'dispose', generation: ++this.generation });
      this.worker.terminate();
      this.worker = undefined;
    }
  }
}
