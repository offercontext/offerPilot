export type OfflineWhisperBackend = 'webgpu' | 'wasm';

export type OfflineModelState =
  | { status: 'not_downloaded' }
  | { status: 'checking' }
  | { status: 'downloading'; receivedBytes: number; totalBytes?: number }
  | { status: 'ready'; modelVersion: string; cachedBytes: number; backend?: OfflineWhisperBackend }
  | { status: 'loading'; backend: OfflineWhisperBackend }
  | { status: 'transcribing'; backend: OfflineWhisperBackend; progress?: number }
  | { status: 'incompatible'; reason: string }
  | { status: 'error'; recoverable: boolean; message: string };

export type OfflineWhisperWorkerRequest =
  | { type: 'prepare'; generation: number; preferredBackend: OfflineWhisperBackend }
  | { type: 'transcribe'; generation: number; audio: Float32Array; sampleRate: 16000; language: 'zh' }
  | { type: 'cancel'; generation: number }
  | { type: 'dispose'; generation: number };

export type OfflineWhisperWorkerFailureCategory =
  | 'download'
  | 'incompatible'
  | 'initialization'
  | 'transcription'
  | 'cancelled';

export type OfflineWhisperWorkerResponse =
  | { type: 'download_progress'; generation: number; loaded: number; total?: number }
  | { type: 'ready'; generation: number; backend: OfflineWhisperBackend; cachedBytes?: number }
  | { type: 'transcription_progress'; generation: number; progress?: number; backend: OfflineWhisperBackend }
  | { type: 'completed'; generation: number; text: string; backend: OfflineWhisperBackend }
  | { type: 'failed'; generation: number; category: OfflineWhisperWorkerFailureCategory; recoverable: boolean; message?: string };

export interface OfflineWhisperWorkerLike {
  postMessage(message: OfflineWhisperWorkerRequest, transfer?: Transferable[]): void;
  addEventListener(type: 'message', listener: (event: MessageEvent<OfflineWhisperWorkerResponse>) => void): void;
  removeEventListener(type: 'message', listener: (event: MessageEvent<OfflineWhisperWorkerResponse>) => void): void;
  terminate(): void;
}

export interface OfflineWhisperController {
  getState(): OfflineModelState;
  subscribe(listener: () => void): () => void;
  check(): Promise<void>;
  prepare(): Promise<OfflineWhisperBackend>;
  transcribe(audio: Float32Array): Promise<{ text: string; backend: OfflineWhisperBackend }>;
  cancel(): void;
  remove(): Promise<void>;
  dispose(): void;
}
