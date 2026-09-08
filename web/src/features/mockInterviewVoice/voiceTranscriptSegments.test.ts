import { describe, expect, it } from 'vitest';
import { mergeTranscriptSegments } from './voiceTranscriptSegments';

describe('mergeTranscriptSegments', () => {
  it('sorts by sequence, rejects stale generations and removes overlap by code point', () => {
    expect(mergeTranscriptSegments([
      { sequence: 2, generation: 4, startMs: 18_000, endMs: 38_000, text: '完成回滚😀并复盘' },
      { sequence: 1, generation: 4, startMs: 0, endMs: 20_000, text: '我先定位日志，完成回滚😀' },
      { sequence: 3, generation: 3, startMs: 36_000, endMs: 50_000, text: '旧结果' },
    ], 4)).toBe('我先定位日志，完成回滚😀并复盘');
  });

  it('is stable for empty, duplicate and disjoint segments', () => {
    expect(mergeTranscriptSegments([], 1)).toBe('');
    expect(mergeTranscriptSegments([
      { sequence: 1, generation: 1, startMs: 0, endMs: 1, text: '同一句' },
      { sequence: 2, generation: 1, startMs: 1, endMs: 2, text: '同一句' },
      { sequence: 3, generation: 1, startMs: 2, endMs: 3, text: '下一句' },
    ], 1)).toBe('同一句下一句');
  });
});
