import { describe, expect, it } from 'vitest';
import { downmixAndResample, isLikelySilentAudio, validateAudioDuration } from './audioDecoder';

describe('offline audio decoder', () => {
  it('downmixes stereo and resamples deterministically to 16 kHz', () => {
    const left = new Float32Array([1, 0, -1, 0]);
    const right = new Float32Array([0, 1, 0, -1]);
    const result = downmixAndResample([left, right], 32000, 16000);
    expect(Array.from(result)).toEqual([0.5, -0.5]);
  });

  it('accepts five minutes and rejects longer audio', () => {
    expect(validateAudioDuration(300)).toBe(true);
    expect(validateAudioDuration(300.001)).toBe(false);
    expect(validateAudioDuration(Number.NaN)).toBe(false);
  });

  it('detects silence without rejecting ordinary speech-level samples', () => {
    expect(isLikelySilentAudio(new Float32Array(32000))).toBe(true);
    expect(isLikelySilentAudio(new Float32Array([0, 0.001, -0.001, 0]))).toBe(true);
    expect(isLikelySilentAudio(new Float32Array([0, 0.08, -0.07, 0.04]))).toBe(false);
  });
});
