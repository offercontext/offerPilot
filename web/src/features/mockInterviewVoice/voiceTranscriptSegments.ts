export type TranscriptSegment = {
  sequence: number;
  generation: number;
  startMs: number;
  endMs: number;
  text: string;
};

function overlapLength(left: string[], right: string[]): number {
  const limit = Math.min(left.length, right.length);
  for (let length = limit; length > 0; length -= 1) {
    let equal = true;
    for (let index = 0; index < length; index += 1) {
      if (left[left.length - length + index] !== right[index]) { equal = false; break; }
    }
    if (equal) return length;
  }
  return 0;
}

export function mergeTranscriptSegments(segments: readonly TranscriptSegment[], generation: number): string {
  return segments
    .filter((segment) => segment.generation === generation && segment.text.trim())
    .slice()
    .sort((left, right) => left.sequence - right.sequence || left.startMs - right.startMs)
    .reduce((result, segment) => {
      const left = Array.from(result);
      const right = Array.from(segment.text.trim());
      const overlap = overlapLength(left, right);
      return `${result}${right.slice(overlap).join('')}`;
    }, '');
}
