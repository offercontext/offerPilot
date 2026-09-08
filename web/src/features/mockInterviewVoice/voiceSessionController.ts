import { VoiceActivityDetector } from './voiceActivityDetector';
import { mergeTranscriptSegments, type TranscriptSegment } from './voiceTranscriptSegments';

export type VoiceSessionState =
  | { status: 'idle' }
  | { status: 'waiting_for_speech'; elapsedMs: number }
  | { status: 'recording'; elapsedMs: number; voicedMs: number; transcriptionMode: 'segment' | 'batch' }
  | { status: 'speech_paused'; elapsedMs: number; pauseMs: number; transcriptionMode: 'segment' | 'batch' }
  | { status: 'finalizing' }
  | { status: 'transcribing'; mode: 'segment' | 'final'; progress?: number }
  | { status: 'reviewing'; transcript: string; temporarySegments: TranscriptSegment[] }
  | { status: 'error'; recoverable: boolean; message: string };

type Dependencies = {
  now: () => number;
  transcribe: (pcm: Float32Array) => Promise<string>;
  cancelTranscription?: () => void;
  onState: (state: VoiceSessionState) => void;
  onInterimTranscript: (text: string) => void;
  chunkMs?: number;
  overlapMs?: number;
  maxDurationMs?: number;
  calibrationMs?: number;
};

export interface VoiceSessionController {
  start(generation: number, sampleRate: number): void;
  acceptFrame(frame: Float32Array, atMs: number): void;
  acceptLevels(levels: { atMs: number; durationMs: number; rms: number; peak: number }): void;
  finish(): Promise<void>;
  pause(): void;
  resume(): void;
  cancel(): void;
  dispose(): void;
  getVoicedRanges(): ReadonlyArray<readonly [number, number]>;
  getMode(): 'segment' | 'batch';
}

function concat(left: Float32Array, right: Float32Array): Float32Array<ArrayBuffer> {
  const result = new Float32Array(left.length + right.length);
  result.set(left);
  result.set(right, left.length);
  return result;
}

function levels(frame: Float32Array): { rms: number; peak: number } {
  if (!frame.length) return { rms: 0, peak: 0 };
  let squareSum = 0;
  let peak = 0;
  for (const raw of frame) {
    const value = Number.isFinite(raw) ? raw : 0;
    squareSum += value * value;
    peak = Math.max(peak, Math.abs(value));
  }
  return { rms: Math.sqrt(squareSum / frame.length), peak };
}

export function createVoiceSessionController(dependencies: Dependencies): VoiceSessionController {
  const chunkMs = dependencies.chunkMs ?? 20_000;
  const overlapMs = Math.min(dependencies.overlapMs ?? 2_000, chunkMs / 2);
  const maxDurationMs = dependencies.maxDurationMs ?? 300_000;
  let detector = new VoiceActivityDetector({ calibrationMs: dependencies.calibrationMs });
  let generation = 0;
  let sampleRate = 16_000;
  let elapsedMs = 0;
  let voicedMs = 0;
  let paused = false;
  let finalizing = false;
  let disposed = false;
  let mode: 'segment' | 'batch' = 'segment';
  let buffered: Float32Array<ArrayBufferLike> = new Float32Array();
  let sequence = 0;
  let running = false;
  let runningPromise: Promise<void> | undefined;
  let queue: Array<{ pcm: Float32Array; segment: TranscriptSegment }> = [];
  let segments: TranscriptSegment[] = [];
  let voicedRanges: Array<[number, number]> = [];

  const emitRecording = () => dependencies.onState({ status: 'recording', elapsedMs, voicedMs, transcriptionMode: mode });

  const acceptActivity = (atMs: number, durationMs: number, rms: number, peak: number) => {
    elapsedMs = Math.max(elapsedMs, atMs + durationMs);
    const events = detector.accept({ atMs, durationMs, rms, peak });
    const active = rms >= detector.threshold || peak >= detector.threshold * 2;
    if (active) {
      voicedMs += durationMs;
      const previous = voicedRanges[voicedRanges.length - 1];
      if (previous && atMs <= previous[1]) previous[1] = Math.max(previous[1], atMs + durationMs);
      else voicedRanges.push([atMs, atMs + durationMs]);
    }
    const longPause = events.find((event) => event.type === 'long_pause');
    if (longPause?.type === 'long_pause') {
      dependencies.onState({ status: 'speech_paused', elapsedMs, pauseMs: longPause.toMs - longPause.fromMs, transcriptionMode: mode });
    } else if (events.every((event) => event.type === 'calibrating') && !voicedRanges.length) {
      dependencies.onState({ status: 'waiting_for_speech', elapsedMs });
    } else {
      emitRecording();
    }
    if (elapsedMs >= maxDurationMs) {
      finalizing = true;
      dependencies.onState({ status: 'finalizing' });
    }
  };

  const runNext = () => {
    if (running || mode === 'batch' || queue.length === 0 || disposed) return;
    const item = queue.shift()!;
    const expectedGeneration = generation;
    const inferenceStartedAt = dependencies.now();
    running = true;
    dependencies.onState({ status: 'transcribing', mode: 'segment' });
    runningPromise = dependencies.transcribe(item.pcm).then((text) => {
      if (disposed || expectedGeneration !== generation || mode === 'batch') return;
      const inferenceMs = Math.max(0, dependencies.now() - inferenceStartedAt);
      if (inferenceMs > chunkMs) {
        mode = 'batch';
        queue = [];
        emitRecording();
        return;
      }
      segments = [...segments, { ...item.segment, text }];
      dependencies.onInterimTranscript(mergeTranscriptSegments(segments, generation));
    }).catch(() => {
      if (expectedGeneration === generation && !disposed) {
        mode = 'batch';
        queue = [];
        dependencies.onState({ status: 'error', recoverable: true, message: '临时字幕不可用，回答结束后将批量整理。' });
      }
    }).finally(() => {
      running = false;
      runningPromise = undefined;
      runNext();
    });
  };

  const enqueueChunks = () => {
    if (mode === 'batch') return;
    const chunkSamples = Math.max(1, Math.round(sampleRate * chunkMs / 1_000));
    const overlapSamples = Math.max(0, Math.round(sampleRate * overlapMs / 1_000));
    while (buffered.length >= chunkSamples) {
      const pcm = buffered.slice(0, chunkSamples);
      const startMs = sequence * (chunkMs - overlapMs);
      queue.push({
        pcm,
        segment: { sequence: ++sequence, generation, startMs, endMs: startMs + chunkMs, text: '' },
      });
      buffered = buffered.slice(chunkSamples - overlapSamples);
      if (running && queue.length > 1) {
        mode = 'batch';
        queue = [];
        emitRecording();
        return;
      }
      runNext();
    }
  };

  const reset = (nextGeneration: number, nextSampleRate: number) => {
    generation = nextGeneration;
    sampleRate = nextSampleRate;
    elapsedMs = 0;
    voicedMs = 0;
    paused = false;
    finalizing = false;
    mode = 'segment';
    buffered = new Float32Array();
    sequence = 0;
    queue = [];
    segments = [];
    voicedRanges = [];
    detector = new VoiceActivityDetector({ calibrationMs: dependencies.calibrationMs });
  };

  return {
    start(nextGeneration, nextSampleRate) {
      if (!Number.isFinite(nextSampleRate) || nextSampleRate <= 0) throw new Error('sample rate must be positive');
      disposed = false;
      reset(nextGeneration, nextSampleRate);
      dependencies.onState({ status: 'waiting_for_speech', elapsedMs: 0 });
    },
    acceptFrame(frame, atMs) {
      if (disposed || paused || finalizing || frame.length === 0) return;
      const durationMs = frame.length / sampleRate * 1_000;
      buffered = concat(buffered, frame);
      const { rms, peak } = levels(frame);
      acceptActivity(atMs, durationMs, rms, peak);
      if (finalizing) return;
      enqueueChunks();
    },
    acceptLevels(levelFrame) {
      if (disposed || paused || finalizing) return;
      acceptActivity(levelFrame.atMs, levelFrame.durationMs, levelFrame.rms, levelFrame.peak);
    },
    async finish() {
      if (disposed) return;
      finalizing = true;
      dependencies.onState({ status: 'finalizing' });
      while (!disposed && mode === 'segment') {
        if (runningPromise) {
          await runningPromise;
          continue;
        }
        if (queue.length > 0) {
          runNext();
          continue;
        }
        break;
      }
      if (disposed) return;
      dependencies.onState({ status: 'reviewing', transcript: mergeTranscriptSegments(segments, generation), temporarySegments: [...segments] });
    },
    pause() { paused = true; },
    resume() { paused = false; },
    cancel() {
      generation += 1;
      queue = [];
      buffered = new Float32Array();
      dependencies.cancelTranscription?.();
      dependencies.onState({ status: 'idle' });
    },
    dispose() {
      disposed = true;
      generation += 1;
      queue = [];
      buffered = new Float32Array();
      dependencies.cancelTranscription?.();
    },
    getVoicedRanges: () => voicedRanges.map((range) => [...range] as [number, number]),
    getMode: () => mode,
  };
}
