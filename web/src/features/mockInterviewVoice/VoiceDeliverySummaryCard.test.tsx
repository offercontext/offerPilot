// @vitest-environment jsdom
import { act, createRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VoiceDeliverySummaryCard from './VoiceDeliverySummaryCard';
import type { VoiceDeliverySummary } from './voiceDeliverySummary';

let root: Root | undefined;
let host: HTMLDivElement | undefined;

const summary: VoiceDeliverySummary = {
  totalDurationMs: 72_000,
  voicedDurationMs: 50_000,
  pauseCount: 2,
  longestPauseMs: 2_800,
  speechRateCpm: 146,
  fillerOccurrences: [{ text: '然后', count: 2, transcriptOffsets: [4, 12] }],
  pauseRanges: [[10_000, 11_000], [30_000, 32_800]],
  source: 'local_audio_and_confirmed_transcript',
};

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  host?.remove();
  root = undefined;
  host = undefined;
});

describe('VoiceDeliverySummaryCard', () => {
  it('shows only measurable delivery facts and calculation guidance', async () => {
    await act(async () => root!.render(<VoiceDeliverySummaryCard summary={summary} />));
    expect(host!.textContent).toContain('表达节奏复盘');
    expect(host!.textContent).toContain('01:12');
    expect(host!.textContent).toContain('146 字/分钟');
    expect(host!.textContent).toContain('不评价表达能力或面试表现');
    expect(host!.textContent).not.toMatch(/综合分|排名|自信|紧张/);
  });

  it('focuses filler text and seeks current-session audio for pauses', async () => {
    const textarea = document.createElement('textarea');
    textarea.value = '这是然后的回答然后';
    const transcriptRef = createRef<HTMLTextAreaElement>();
    Object.defineProperty(transcriptRef, 'current', { value: textarea });
    const audio = document.createElement('audio');
    audio.play = vi.fn(async () => undefined);
    const audioRef = createRef<HTMLAudioElement>();
    Object.defineProperty(audioRef, 'current', { value: audio });
    await act(async () => root!.render(
      <VoiceDeliverySummaryCard summary={summary} transcriptRef={transcriptRef} audioRef={audioRef} />,
    ));

    const filler = Array.from(host!.querySelectorAll('button')).find((button) => button.textContent?.includes('然后'))!;
    act(() => filler.click());
    expect(textarea.selectionStart).toBe(4);
    const pause = Array.from(host!.querySelectorAll('button')).find((button) => button.textContent?.includes('最长停顿'))!;
    act(() => pause.click());
    expect(audio.currentTime).toBe(30);
  });

  it('disables pause navigation after audio is released', async () => {
    await act(async () => root!.render(<VoiceDeliverySummaryCard summary={summary} />));
    const pause = Array.from(host!.querySelectorAll('button')).find((button) => button.textContent?.includes('最长停顿'))!;
    expect(pause.disabled).toBe(true);
  });
});
