export type OfflineWhisperManifest = {
  readonly schemaVersion: 1;
  readonly modelId: 'onnx-community/whisper-small';
  readonly revision: string;
  readonly displayName: 'Whisper 多语言均衡模型';
  readonly approximateBytes: number;
  readonly maxAudioSeconds: 300;
  readonly license: 'apache-2.0';
  readonly sourceUrl: string;
  readonly cacheNamespace: string;
};

export const OFFLINE_WHISPER_MANIFEST: OfflineWhisperManifest = Object.freeze({
  schemaVersion: 1,
  modelId: 'onnx-community/whisper-small',
  revision: '461d552a09349d5d0d0779b40dd79800eaa3e35a',
  displayName: 'Whisper 多语言均衡模型',
  approximateBytes: 561 * 1024 * 1024,
  maxAudioSeconds: 300,
  license: 'apache-2.0',
  sourceUrl: 'https://huggingface.co/onnx-community/whisper-small',
  cacheNamespace: 'offerpilot-offline-whisper-v1',
});

export function validateOfflineWhisperManifest(value: OfflineWhisperManifest): boolean {
  let source: URL;
  try {
    source = new URL(value.sourceUrl);
  } catch {
    return false;
  }
  return value.schemaVersion === 1
    && value.modelId === 'onnx-community/whisper-small'
    && /^[0-9a-f]{40}$/.test(value.revision)
    && source.protocol === 'https:'
    && source.hostname === 'huggingface.co'
    && Number.isSafeInteger(value.approximateBytes)
    && value.approximateBytes > 0
    && value.maxAudioSeconds === 300
    && value.license === 'apache-2.0';
}

export function formatModelSize(bytes = OFFLINE_WHISPER_MANIFEST.approximateBytes): string {
  return `约 ${Math.round(bytes / 1024 / 1024)} MB`;
}
