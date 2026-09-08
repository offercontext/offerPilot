import { useSyncExternalStore } from 'react';
import { OFFLINE_WHISPER_MANIFEST } from './offlineWhisperManifest';
import { OfflineModelStore } from './offlineModelStore';
import { isLikelySilentAudio } from './audioDecoder';
import type {
  OfflineModelState,
  OfflineWhisperBackend,
  OfflineWhisperController,
  OfflineWhisperWorkerLike,
  OfflineWhisperWorkerResponse,
} from './offlineWhisperTypes';

type StoreLike = Pick<OfflineModelStore, 'inspect' | 'checkCapacity' | 'markReady' | 'remove'>;

type ControllerOptions = {
  createWorker?: () => OfflineWhisperWorkerLike;
  store?: StoreLike;
};

type PendingPrepare = {
  generation: number;
  resolve: (backend: OfflineWhisperBackend) => void;
  reject: (error: Error) => void;
};

type PendingTranscription = {
  generation: number;
  resolve: (value: { text: string; backend: OfflineWhisperBackend }) => void;
  reject: (error: Error) => void;
};

function createDefaultWorker(): OfflineWhisperWorkerLike {
  return new Worker(new URL('./offlineWhisper.worker.ts', import.meta.url), { type: 'module', name: 'offerpilot-offline-whisper' });
}

export class OfflineWhisperControllerImpl implements OfflineWhisperController {
  private state: OfflineModelState = { status: 'checking' };
  private readonly listeners = new Set<() => void>();
  private readonly createWorker: () => OfflineWhisperWorkerLike;
  private readonly store: StoreLike;
  private worker?: OfflineWhisperWorkerLike;
  private generation = 0;
  private runtimeBackend?: OfflineWhisperBackend;
  private cacheReady = false;
  private cachedBytes = 0;
  private pendingPrepare?: PendingPrepare;
  private pendingTranscription?: PendingTranscription;

  constructor(options: ControllerOptions = {}) {
    this.createWorker = options.createWorker ?? createDefaultWorker;
    this.store = options.store ?? new OfflineModelStore();
  }

  getState = (): OfflineModelState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private setState(state: OfflineModelState): void {
    this.state = state;
    this.listeners.forEach((listener) => listener());
  }

  private ensureWorker(): OfflineWhisperWorkerLike {
    if (!this.worker) {
      this.worker = this.createWorker();
      this.worker.addEventListener('message', this.onMessage);
    }
    return this.worker;
  }

  private readonly onMessage = (event: MessageEvent<OfflineWhisperWorkerResponse>) => {
    const message = event.data;
    if (message.generation !== this.generation) return;
    if (message.type === 'download_progress') {
      this.setState({ status: 'downloading', receivedBytes: message.loaded, totalBytes: message.total });
      return;
    }
    if (message.type === 'transcription_progress') {
      this.setState({ status: 'transcribing', backend: this.runtimeBackend ?? message.backend, progress: message.progress });
      return;
    }
    if (message.type === 'ready' && this.pendingPrepare?.generation === message.generation) {
      const pending = this.pendingPrepare;
      this.pendingPrepare = undefined;
      const cachedBytes = message.cachedBytes ?? OFFLINE_WHISPER_MANIFEST.approximateBytes;
      void this.store.markReady(cachedBytes).then(() => {
        this.cacheReady = true;
        this.runtimeBackend = message.backend;
        this.cachedBytes = cachedBytes;
        this.setState({ status: 'ready', modelVersion: OFFLINE_WHISPER_MANIFEST.revision, cachedBytes, backend: message.backend });
        pending.resolve(message.backend);
      }).catch(() => {
        this.setState({ status: 'error', recoverable: true, message: '模型已下载，但本地就绪标记保存失败，请重试检查' });
        pending.reject(new Error('模型就绪状态保存失败'));
      });
      return;
    }
    if (message.type === 'completed' && this.pendingTranscription?.generation === message.generation) {
      const pending = this.pendingTranscription;
      this.pendingTranscription = undefined;
      this.setState({
        status: 'ready',
        modelVersion: OFFLINE_WHISPER_MANIFEST.revision,
        cachedBytes: this.cachedBytes || OFFLINE_WHISPER_MANIFEST.approximateBytes,
        backend: message.backend,
      });
      pending.resolve({ text: message.text.trim(), backend: message.backend });
      return;
    }
    if (message.type === 'failed') {
      const error = new Error(message.message || '离线语音转写失败');
      this.pendingPrepare?.reject(error);
      this.pendingTranscription?.reject(error);
      this.pendingPrepare = undefined;
      this.pendingTranscription = undefined;
      this.setState(message.category === 'incompatible'
        ? { status: 'incompatible', reason: error.message }
        : { status: 'error', recoverable: message.recoverable, message: error.message });
    }
  };

  async check(): Promise<void> {
    this.setState({ status: 'checking' });
    const inspected = await this.store.inspect();
    this.cacheReady = inspected.ready;
    this.cachedBytes = inspected.ready ? inspected.cachedBytes : 0;
    this.runtimeBackend = undefined;
    this.setState(inspected.ready
      ? { status: 'ready', modelVersion: OFFLINE_WHISPER_MANIFEST.revision, cachedBytes: inspected.cachedBytes }
      : { status: 'not_downloaded' });
  }

  async prepare(): Promise<OfflineWhisperBackend> {
    if (this.pendingPrepare) throw new Error('模型正在准备中');
    if (!this.cacheReady) {
      const capacity = await this.store.checkCapacity();
      if (capacity === 'insufficient') {
        const error = new Error('浏览器可用存储空间不足，无法下载离线语音模型');
        this.setState({ status: 'error', recoverable: true, message: error.message });
        throw error;
      }
    }
    const generation = ++this.generation;
    const worker = this.ensureWorker();
    this.setState({ status: 'loading', backend: 'webgpu' });
    return new Promise<OfflineWhisperBackend>((resolve, reject) => {
      this.pendingPrepare = { generation, resolve, reject };
      worker.postMessage({ type: 'prepare', generation, preferredBackend: 'webgpu' });
    });
  }

  async transcribe(audio: Float32Array): Promise<{ text: string; backend: OfflineWhisperBackend }> {
    if (this.pendingTranscription) throw new Error('已有转写正在进行');
    if (isLikelySilentAudio(audio)) throw new Error('没有检测到清晰语音，请重录或手工整理文字');
    if (!this.runtimeBackend) {
      if (this.state.status !== 'ready') throw new Error('离线模型尚未准备好');
      await this.prepare();
    }
    const generation = ++this.generation;
    const worker = this.ensureWorker();
    const backend = this.runtimeBackend ?? 'webgpu';
    this.setState({ status: 'transcribing', backend });
    return new Promise((resolve, reject) => {
      this.pendingTranscription = { generation, resolve, reject };
      worker.postMessage({ type: 'transcribe', generation, audio, sampleRate: 16000, language: 'zh' }, [audio.buffer]);
    });
  }

  cancel(): void {
    ++this.generation;
    this.pendingPrepare?.reject(new Error('模型准备已取消'));
    this.pendingTranscription?.reject(new Error('转写已取消'));
    this.pendingPrepare = undefined;
    this.pendingTranscription = undefined;
    this.disposeWorker();
    void this.check();
  }

  async remove(): Promise<void> {
    this.disposeWorker();
    ++this.generation;
    await this.store.remove();
    this.cacheReady = false;
    this.cachedBytes = 0;
    this.setState({ status: 'not_downloaded' });
  }

  private disposeWorker(): void {
    if (!this.worker) return;
    this.worker.removeEventListener('message', this.onMessage);
    this.worker.postMessage({ type: 'dispose', generation: ++this.generation });
    this.worker.terminate();
    this.worker = undefined;
    this.runtimeBackend = undefined;
  }

  dispose(): void {
    this.pendingPrepare?.reject(new Error('模型操作已关闭'));
    this.pendingTranscription?.reject(new Error('转写已关闭'));
    this.pendingPrepare = undefined;
    this.pendingTranscription = undefined;
    this.disposeWorker();
  }
}

export const offlineWhisperController = new OfflineWhisperControllerImpl();

export function useOfflineWhisperState(controller: OfflineWhisperController = offlineWhisperController): OfflineModelState {
  return useSyncExternalStore(controller.subscribe, controller.getState, controller.getState);
}
