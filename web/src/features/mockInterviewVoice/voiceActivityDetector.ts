export type VoiceFrame = { atMs: number; durationMs: number; rms: number; peak: number };
export type VoiceActivityEvent =
  | { type: 'calibrating'; untilMs: number }
  | { type: 'speech_started'; atMs: number }
  | { type: 'speech_continued'; fromMs: number; toMs: number }
  | { type: 'short_pause'; fromMs: number; toMs: number }
  | { type: 'long_pause'; fromMs: number; toMs: number };

type Options = {
  calibrationMs?: number;
  threshold?: (noiseRms: number) => number;
  speechStartMs?: number;
  shortPauseMs?: number;
  longPauseMs?: number;
};

export class VoiceActivityDetector {
  private readonly options: Required<Options>;
  private startedAtMs?: number;
  private lastFrameEnd = -Infinity;
  private calibrationSquareSum = 0;
  private calibrationDuration = 0;
  private candidateSpeechAt?: number;
  private speaking = false;
  private pauseAt?: number;
  private shortPauseEmitted = false;
  private longPauseEmitted = false;
  private currentThreshold = 0.015;

  constructor(options: Options = {}) {
    this.options = {
      calibrationMs: options.calibrationMs ?? 800,
      threshold: options.threshold ?? ((noiseRms) => Math.max(0.015, noiseRms * 3)),
      speechStartMs: options.speechStartMs ?? 160,
      shortPauseMs: options.shortPauseMs ?? 1_200,
      longPauseMs: options.longPauseMs ?? 2_500,
    };
  }

  get threshold(): number { return this.currentThreshold; }

  accept(frame: VoiceFrame): VoiceActivityEvent[] {
    if (![frame.atMs, frame.durationMs, frame.rms, frame.peak].every(Number.isFinite) || frame.durationMs <= 0) return [];
    if (frame.atMs < this.lastFrameEnd) return [];
    this.startedAtMs ??= frame.atMs;
    const end = frame.atMs + frame.durationMs;
    this.lastFrameEnd = end;
    const calibrationEnd = this.startedAtMs + this.options.calibrationMs;
    if (frame.atMs < calibrationEnd) {
      const duration = Math.min(end, calibrationEnd) - frame.atMs;
      this.calibrationSquareSum += frame.rms * frame.rms * duration;
      this.calibrationDuration += duration;
      const noise = this.calibrationDuration > 0 ? Math.sqrt(this.calibrationSquareSum / this.calibrationDuration) : 0;
      this.currentThreshold = this.options.threshold(noise);
      return [{ type: 'calibrating', untilMs: calibrationEnd }];
    }
    this.currentThreshold = this.options.threshold(
      this.calibrationDuration > 0 ? Math.sqrt(this.calibrationSquareSum / this.calibrationDuration) : 0,
    );
    const active = frame.rms >= this.currentThreshold || frame.peak >= this.currentThreshold * 2;
    if (active) {
      const events: VoiceActivityEvent[] = [];
      this.pauseAt = undefined;
      this.shortPauseEmitted = false;
      this.longPauseEmitted = false;
      this.candidateSpeechAt ??= frame.atMs;
      if (!this.speaking && end - this.candidateSpeechAt >= this.options.speechStartMs) {
        this.speaking = true;
        events.push({ type: 'speech_started', atMs: this.candidateSpeechAt });
      } else if (this.speaking) {
        events.push({ type: 'speech_continued', fromMs: frame.atMs, toMs: end });
      }
      return events;
    }
    this.candidateSpeechAt = undefined;
    if (!this.speaking) return [];
    this.pauseAt ??= frame.atMs;
    const pauseMs = end - this.pauseAt;
    if (pauseMs >= this.options.longPauseMs && !this.longPauseEmitted) {
      this.longPauseEmitted = true;
      return [{ type: 'long_pause', fromMs: this.pauseAt, toMs: end }];
    }
    if (pauseMs >= this.options.shortPauseMs && !this.shortPauseEmitted) {
      this.shortPauseEmitted = true;
      return [{ type: 'short_pause', fromMs: this.pauseAt, toMs: end }];
    }
    return [];
  }
}
