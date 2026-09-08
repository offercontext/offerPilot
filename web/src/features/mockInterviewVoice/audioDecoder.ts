import { OFFLINE_WHISPER_MANIFEST } from './offlineWhisperManifest';

export interface AudioBufferLike {
  readonly numberOfChannels: number;
  readonly sampleRate: number;
  readonly duration: number;
  getChannelData(channel: number): Float32Array;
}

export interface AudioContextLike {
  decodeAudioData(data: ArrayBuffer): Promise<AudioBufferLike>;
  close(): Promise<void>;
}

export function validateAudioDuration(seconds: number): boolean {
  return Number.isFinite(seconds) && seconds > 0 && seconds <= OFFLINE_WHISPER_MANIFEST.maxAudioSeconds;
}

export function isLikelySilentAudio(samples: Float32Array): boolean {
  if (samples.length === 0) return true;
  let squareSum = 0;
  let peak = 0;
  for (const rawSample of samples) {
    const sample = Number.isFinite(rawSample) ? rawSample : 0;
    const absolute = Math.abs(sample);
    peak = Math.max(peak, absolute);
    squareSum += sample * sample;
  }
  const rms = Math.sqrt(squareSum / samples.length);
  return peak < 0.02 && rms < 0.004;
}

export function downmixAndResample(
  channels: readonly Float32Array[],
  sourceRate: number,
  targetRate = 16000,
): Float32Array {
  if (channels.length === 0 || !Number.isFinite(sourceRate) || sourceRate <= 0 || targetRate <= 0) {
    throw new Error('音频数据无效');
  }
  const inputLength = Math.min(...channels.map((channel) => channel.length));
  if (inputLength === 0) throw new Error('音频内容为空');
  const mono = new Float32Array(inputLength);
  for (let index = 0; index < inputLength; index += 1) {
    let sum = 0;
    for (const channel of channels) sum += channel[index] ?? 0;
    mono[index] = sum / channels.length;
  }
  if (sourceRate === targetRate) return mono;
  const outputLength = Math.max(1, Math.round(inputLength * targetRate / sourceRate));
  const output = new Float32Array(outputLength);
  const ratio = sourceRate / targetRate;
  for (let outputIndex = 0; outputIndex < outputLength; outputIndex += 1) {
    const start = outputIndex * ratio;
    const end = Math.min(inputLength, (outputIndex + 1) * ratio);
    const first = Math.floor(start);
    const last = Math.max(first + 1, Math.ceil(end));
    let weighted = 0;
    let weightTotal = 0;
    for (let inputIndex = first; inputIndex < last && inputIndex < inputLength; inputIndex += 1) {
      const weight = Math.max(0, Math.min(end, inputIndex + 1) - Math.max(start, inputIndex));
      weighted += mono[inputIndex] * weight;
      weightTotal += weight;
    }
    output[outputIndex] = weightTotal > 0 ? weighted / weightTotal : mono[Math.min(first, inputLength - 1)];
  }
  return output;
}

export async function decodeAudioBlob(
  blob: Blob,
  createContext: () => AudioContextLike = () => new AudioContext() as unknown as AudioContextLike,
): Promise<Float32Array> {
  if (!blob.size) throw new Error('录音内容为空');
  const context = createContext();
  try {
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    if (!validateAudioDuration(decoded.duration)) {
      throw new Error(decoded.duration > OFFLINE_WHISPER_MANIFEST.maxAudioSeconds
        ? '录音超过 5 分钟，请拆分回答或手工整理文字'
        : '无法识别录音时长');
    }
    const channels = Array.from({ length: decoded.numberOfChannels }, (_, channel) => decoded.getChannelData(channel));
    return downmixAndResample(channels, decoded.sampleRate, 16000);
  } finally {
    await context.close().catch(() => undefined);
  }
}
