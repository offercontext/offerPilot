import { describe, expect, it } from 'vitest';
import { VoiceActivityDetector } from './voiceActivityDetector';

describe('VoiceActivityDetector', () => {
  it('calibrates noise, requires sustained speech and distinguishes pause lengths', () => {
    const detector = new VoiceActivityDetector();
    const events = [
      detector.accept({ atMs: 0, durationMs: 400, rms: 0.004, peak: 0.01 }),
      detector.accept({ atMs: 400, durationMs: 400, rms: 0.006, peak: 0.02 }),
      detector.accept({ atMs: 800, durationMs: 80, rms: 0.03, peak: 0.08 }),
      detector.accept({ atMs: 880, durationMs: 80, rms: 0.03, peak: 0.08 }),
      detector.accept({ atMs: 960, durationMs: 600, rms: 0.03, peak: 0.08 }),
      detector.accept({ atMs: 1_560, durationMs: 1_200, rms: 0.001, peak: 0.002 }),
      detector.accept({ atMs: 2_760, durationMs: 1_400, rms: 0.001, peak: 0.002 }),
    ].flat();

    expect(detector.threshold).toBeCloseTo(0.015);
    expect(events.map((event) => event.type)).toEqual([
      'calibrating', 'calibrating', 'speech_started', 'speech_continued', 'short_pause', 'long_pause',
    ]);
  });

  it('uses an injected threshold policy and ignores invalid or out-of-order frames', () => {
    const detector = new VoiceActivityDetector({
      calibrationMs: 0,
      threshold: () => 0.2,
      speechStartMs: 100,
    });

    expect(detector.accept({ atMs: 100, durationMs: 100, rms: Number.NaN, peak: 1 })).toEqual([]);
    expect(detector.accept({ atMs: 100, durationMs: 100, rms: 0.3, peak: 0.4 })[0]?.type).toBe('speech_started');
    expect(detector.accept({ atMs: 50, durationMs: 20, rms: 0.3, peak: 0.4 })).toEqual([]);
  });
});
