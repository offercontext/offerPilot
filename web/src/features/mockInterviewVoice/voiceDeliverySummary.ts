export type VoiceDeliverySummary = {
  totalDurationMs: number;
  voicedDurationMs: number;
  pauseCount: number;
  longestPauseMs: number;
  speechRateCpm?: number;
  fillerOccurrences: Array<{ text: string; count: number; transcriptOffsets: number[] }>;
  pauseRanges: Array<readonly [number, number]>;
  source: 'local_audio_and_confirmed_transcript';
};

export type VoiceDeliverySummaryInput = {
  startedAtMs: number;
  endedAtMs: number;
  voicedRanges: ReadonlyArray<readonly [number, number]>;
  transcript: string;
};

export const DEFAULT_FILLER_LEXICON = Object.freeze(['就是说', '然后', '那个', '嗯', '呃']);

function finite(value: number, fallback: number): number {
  return Number.isFinite(value) ? value : fallback;
}

function normalizedRanges(input: VoiceDeliverySummaryInput): Array<[number, number]> {
  const start = finite(input.startedAtMs, 0);
  const end = Math.max(start, finite(input.endedAtMs, start));
  const ranges = input.voicedRanges
    .filter(([from, to]) => Number.isFinite(from) && Number.isFinite(to) && to > from)
    .map(([from, to]) => [Math.max(start, from), Math.min(end, to)] as [number, number])
    .filter(([from, to]) => to > from)
    .sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  const merged: Array<[number, number]> = [];
  for (const range of ranges) {
    const previous = merged[merged.length - 1];
    if (previous && range[0] <= previous[1]) previous[1] = Math.max(previous[1], range[1]);
    else merged.push([...range]);
  }
  return merged;
}

function countEffectiveCodePoints(text: string): number {
  return Array.from(text).filter((value) => !/[\s\p{P}]/u.test(value)).length;
}

function fillerOccurrences(text: string, lexicon: readonly string[]): VoiceDeliverySummary['fillerOccurrences'] {
  const points = Array.from(text);
  const words = [...new Set(lexicon.filter(Boolean))]
    .map((word) => ({ word, points: Array.from(word) }))
    .sort((left, right) => right.points.length - left.points.length || left.word.localeCompare(right.word, 'zh-CN'));
  const matches = new Map<string, number[]>();
  for (let index = 0; index < points.length;) {
    const match = words.find(({ points: wordPoints }) => wordPoints.every((point, offset) => points[index + offset] === point));
    if (!match) {
      index += 1;
      continue;
    }
    const offsets = matches.get(match.word) ?? [];
    offsets.push(index);
    matches.set(match.word, offsets);
    index += match.points.length;
  }
  return words
    .filter(({ word }) => matches.has(word))
    .map(({ word }) => ({ text: word, count: matches.get(word)!.length, transcriptOffsets: matches.get(word)! }))
    .sort((left, right) => left.transcriptOffsets[0] - right.transcriptOffsets[0] || left.text.localeCompare(right.text, 'zh-CN'));
}

export function buildVoiceDeliverySummary(
  input: VoiceDeliverySummaryInput,
  lexicon: readonly string[] = DEFAULT_FILLER_LEXICON,
): VoiceDeliverySummary {
  const startedAtMs = finite(input.startedAtMs, 0);
  const endedAtMs = Math.max(startedAtMs, finite(input.endedAtMs, startedAtMs));
  const totalDurationMs = endedAtMs - startedAtMs;
  const ranges = normalizedRanges({ ...input, startedAtMs, endedAtMs });
  const voicedDurationMs = ranges.reduce((total, [from, to]) => total + (to - from), 0);
  const pauseRanges = ranges.slice(1).map((range, index) => [ranges[index][1], range[0]] as const)
    .filter(([from, to]) => to - from >= 800);
  const effectiveCodePoints = countEffectiveCodePoints(input.transcript);
  const speechRateCpm = totalDurationMs >= 5_000 && effectiveCodePoints > 0
    ? Math.round(effectiveCodePoints / (totalDurationMs / 60_000))
    : undefined;
  return {
    totalDurationMs,
    voicedDurationMs,
    pauseCount: pauseRanges.length,
    longestPauseMs: pauseRanges.reduce((longest, [from, to]) => Math.max(longest, to - from), 0),
    ...(speechRateCpm === undefined ? {} : { speechRateCpm }),
    fillerOccurrences: fillerOccurrences(input.transcript, lexicon),
    pauseRanges,
    source: 'local_audio_and_confirmed_transcript',
  };
}
