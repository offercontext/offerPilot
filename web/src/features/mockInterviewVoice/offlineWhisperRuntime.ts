import { OFFLINE_WHISPER_MANIFEST } from './offlineWhisperManifest';
import type { OfflineWhisperBackend } from './offlineWhisperTypes';

export type WhisperPipelineOutput = { text?: string } | Array<{ text?: string }>;

export interface WhisperPipelineLike {
  (audio: Float32Array, options?: Record<string, unknown>): Promise<WhisperPipelineOutput>;
  dispose?: () => void | Promise<void>;
}

export type PipelineProgress = { loaded: number; total?: number };

export type OfflineWhisperRuntimeDependencies = {
  createPipeline(
    backend: OfflineWhisperBackend,
    onProgress?: (progress: PipelineProgress) => void,
  ): Promise<WhisperPipelineLike>;
};

function outputText(output: WhisperPipelineOutput): string {
  return (Array.isArray(output) ? output.map((item) => item.text ?? '').join(' ') : output.text ?? '').trim();
}

class RepetitiveTranscriptError extends Error {}

export function isLikelyRepetitiveTranscript(text: string): boolean {
  const normalized = text.replace(/\s+/gu, '');
  if (normalized.length < 80) return false;
  const inspectedLength = Math.min(normalized.length, 4000);
  for (let start = 0; start < Math.min(inspectedLength, 256); start += 1) {
    for (let unitLength = 6; unitLength <= 32 && start + unitLength * 4 <= inspectedLength; unitLength += 1) {
      const unit = normalized.slice(start, start + unitLength);
      let repeatCount = 1;
      while (
        start + (repeatCount + 1) * unitLength <= inspectedLength
        && normalized.slice(start + repeatCount * unitLength, start + (repeatCount + 1) * unitLength) === unit
      ) repeatCount += 1;
      const covered = repeatCount * unitLength;
      if (repeatCount >= 4 && covered >= Math.min(80, normalized.length * 0.5)) return true;
    }
  }
  return false;
}

function safeOutputText(output: WhisperPipelineOutput): string {
  const text = outputText(output);
  if (isLikelyRepetitiveTranscript(text)) {
    throw new RepetitiveTranscriptError('转写结果包含异常重复内容，请重录或手工整理文字');
  }
  return text;
}

export class OfflineWhisperRuntime {
  private pipeline?: WhisperPipelineLike;
  private backend?: OfflineWhisperBackend;
  private wasmFallbackUsed = false;

  constructor(private readonly dependencies: OfflineWhisperRuntimeDependencies) {}

  private async destroyPipeline(): Promise<void> {
    const current = this.pipeline;
    this.pipeline = undefined;
    this.backend = undefined;
    if (current?.dispose) await current.dispose();
  }

  private async create(backend: OfflineWhisperBackend, onProgress?: (progress: PipelineProgress) => void): Promise<void> {
    const candidate = await this.dependencies.createPipeline(backend, onProgress);
    this.pipeline = candidate;
    this.backend = backend;
  }

  async prepare(
    preferredBackend: OfflineWhisperBackend,
    onProgress?: (progress: PipelineProgress) => void,
  ): Promise<OfflineWhisperBackend> {
    if (this.pipeline && this.backend) return this.backend;
    try {
      await this.create(preferredBackend, onProgress);
    } catch (error) {
      if (preferredBackend !== 'webgpu' || this.wasmFallbackUsed) throw error;
      this.wasmFallbackUsed = true;
      await this.destroyPipeline();
      await this.create('wasm', onProgress);
    }
    return this.backend!;
  }

  async transcribe(audio: Float32Array): Promise<{ text: string; backend: OfflineWhisperBackend }> {
    const backend = await this.prepare(this.backend ?? 'webgpu');
    try {
      const result = await this.pipeline!(audio, {
        language: 'zh',
        task: 'transcribe',
        chunk_length_s: 30,
        stride_length_s: 5,
        return_timestamps: false,
      });
      return { text: safeOutputText(result), backend };
    } catch (error) {
      if (error instanceof RepetitiveTranscriptError) throw error;
      if (backend !== 'webgpu' || this.wasmFallbackUsed) throw error;
      this.wasmFallbackUsed = true;
      await this.destroyPipeline();
      await this.create('wasm');
      const result = await this.pipeline!(audio, {
        language: 'zh',
        task: 'transcribe',
        chunk_length_s: 30,
        stride_length_s: 5,
        return_timestamps: false,
      });
      return { text: safeOutputText(result), backend: 'wasm' };
    }
  }

  async dispose(): Promise<void> {
    await this.destroyPipeline();
    this.wasmFallbackUsed = false;
  }
}

export async function createTransformersPipeline(
  backend: OfflineWhisperBackend,
  onProgress?: (progress: PipelineProgress) => void,
): Promise<WhisperPipelineLike> {
  const transformers = await import('@huggingface/transformers');
  const { env, pipeline } = transformers;
  if (typeof caches !== 'undefined') {
    env.useBrowserCache = false;
    env.useCustomCache = true;
    env.customCache = await caches.open(OFFLINE_WHISPER_MANIFEST.cacheNamespace);
  }
  env.allowRemoteModels = true;
  env.remoteHost = 'https://huggingface.co/';
  const loadedByFile = new Map<string, number>();
  const totalByFile = new Map<string, number>();
  const created = await pipeline('automatic-speech-recognition', OFFLINE_WHISPER_MANIFEST.modelId, {
    revision: OFFLINE_WHISPER_MANIFEST.revision,
    device: backend,
    dtype: {
      encoder_model: 'fp32',
      decoder_model_merged: 'q4',
    },
    progress_callback: (event: unknown) => {
      if (!event || typeof event !== 'object') return;
      const progress = event as { status?: string; file?: string; loaded?: number; total?: number };
      if (progress.status !== 'progress' || !progress.file || !Number.isFinite(progress.loaded)) return;
      loadedByFile.set(progress.file, progress.loaded!);
      if (Number.isFinite(progress.total)) totalByFile.set(progress.file, progress.total!);
      const loaded = Array.from(loadedByFile.values()).reduce((sum, value) => sum + value, 0);
      const totals = Array.from(totalByFile.values());
      const total = totals.length === loadedByFile.size ? totals.reduce((sum, value) => sum + value, 0) : undefined;
      onProgress?.({ loaded, total });
    },
  });
  return created as unknown as WhisperPipelineLike;
}
