import { describe, expect, it } from 'vitest';
import { buildVoiceDeliverySummary } from './voiceDeliverySummary';

describe('buildVoiceDeliverySummary', () => {
  it('derives duration, voiced time, pauses, speech rate and stable filler offsets', () => {
    const result = buildVoiceDeliverySummary({
      startedAtMs: 0,
      endedAtMs: 60_000,
      voicedRanges: [[1_000, 9_000], [10_500, 20_000], [20_400, 25_000]],
      transcript: '嗯我先定位日志，然后完成回滚。',
    });

    expect(result).toEqual({
      totalDurationMs: 60_000,
      voicedDurationMs: 22_100,
      pauseCount: 1,
      longestPauseMs: 1_500,
      speechRateCpm: 13,
      fillerOccurrences: [
        { text: '嗯', count: 1, transcriptOffsets: [0] },
        { text: '然后', count: 1, transcriptOffsets: [8] },
      ],
      pauseRanges: [[9_000, 10_500]],
      source: 'local_audio_and_confirmed_transcript',
    });
  });

  it('uses code points, longest matching and does not normalize Unicode', () => {
    const result = buildVoiceDeliverySummary({
      startedAtMs: 0,
      endedAtMs: 10_000,
      voicedRanges: [[0, 10_000]],
      transcript: '😀就是说e\u0301那个',
    }, ['说', '就是说', '那个']);

    expect(result.speechRateCpm).toBe(48);
    expect(result.fillerOccurrences).toEqual([
      { text: '就是说', count: 1, transcriptOffsets: [1] },
      { text: '那个', count: 1, transcriptOffsets: [6] },
    ]);
  });

  it('returns no rate below five seconds or without effective text and sanitizes ranges', () => {
    const short = buildVoiceDeliverySummary({
      startedAtMs: 100,
      endedAtMs: 5_099,
      voicedRanges: [[-5, 2_000], [1_500, 9_000], [Number.NaN, 3_000]],
      transcript: '回答',
    });
    const blank = buildVoiceDeliverySummary({
      startedAtMs: 0,
      endedAtMs: 8_000,
      voicedRanges: [],
      transcript: '，。 ',
    });

    expect(short.voicedDurationMs).toBe(4_999);
    expect(short.speechRateCpm).toBeUndefined();
    expect(blank.speechRateCpm).toBeUndefined();
    expect(short).not.toHaveProperty('score');
  });
});
