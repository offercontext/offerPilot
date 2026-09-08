// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { VoiceCoachingPendingReview } from '@/types/voiceCoaching';
import VoiceCoachingSnapshotSaveCard from './VoiceCoachingSnapshotSaveCard';

let host: HTMLDivElement;
let root: Root;

const review: VoiceCoachingPendingReview = {
  turnNo: 1,
  summary: {
    totalDurationMs: 72_000,
    voicedDurationMs: 25_000,
    pauseCount: 2,
    longestPauseMs: 3_100,
    speechRateCpm: 118,
    fillerOccurrences: [{ text: '然后', count: 2, transcriptOffsets: [0, 8] }],
  },
  reflectionText: '',
  focusKind: 'long_pause_control',
  originSnapshotId: null,
  idempotencyKey: 'voice-ui-save-key-0001',
  saveState: 'idle',
  snapshotId: null,
};

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

function click(label: string): void {
  const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
    .find((item) => item.textContent?.includes(label));
  if (!button) throw new Error(`missing button: ${label}`);
  act(() => button.click());
}

describe('VoiceCoachingSnapshotSaveCard', () => {
  it('shows local measurements and requires a second explicit save', async () => {
    const onSave = vi.fn();
    const onSkip = vi.fn();
    await act(async () => root.render(
      <VoiceCoachingSnapshotSaveCard
        review={review}
        onChange={vi.fn()}
        onSave={onSave}
        onSkip={onSkip}
      />,
    ));

    expect(host.textContent).toContain('保存本次表达复盘');
    expect(host.textContent).toContain('01:12');
    expect(host.textContent).toContain('最长停顿');
    expect(host.textContent).toContain('3.1 秒');
    expect(host.textContent).toContain('仅保存本机测量结果和你确认的文字');
    expect(onSave).not.toHaveBeenCalled();
    click('确认保存');
    expect(onSave).toHaveBeenCalledOnce();
    click('暂不保存');
    expect(onSkip).toHaveBeenCalledOnce();
  });

  it('freezes editing after an unknown result and only offers the original retry', async () => {
    const onSave = vi.fn();
    await act(async () => root.render(
      <VoiceCoachingSnapshotSaveCard
        review={{ ...review, saveState: 'unknown' }}
        onChange={vi.fn()}
        onSave={onSave}
        onSkip={vi.fn()}
      />,
    ));

    expect(host.textContent).toContain('保存结果待确认');
    const textarea = host.querySelector('textarea');
    expect(textarea?.disabled).toBe(true);
    click('使用原保存请求重试');
    expect(onSave).toHaveBeenCalledOnce();
    expect([...host.querySelectorAll('button')].some((button) => button.textContent?.includes('暂不保存'))).toBe(false);
  });

  it('renders a stable saved state without another write action', async () => {
    await act(async () => root.render(
      <VoiceCoachingSnapshotSaveCard
        review={{ ...review, saveState: 'saved', snapshotId: 7 }}
        onChange={vi.fn()}
        onSave={vi.fn()}
        onSkip={vi.fn()}
      />,
    ));
    expect(host.textContent).toContain('表达复盘已保存');
    expect(host.querySelector('button')).toBeNull();
  });

  it('offers an explicit no-focus choice', async () => {
    const onChange = vi.fn();
    await act(async () => root.render(
      <VoiceCoachingSnapshotSaveCard
        review={review}
        onChange={onChange}
        onSave={vi.fn()}
        onSkip={vi.fn()}
      />,
    ));
    click('暂不设置重点');
    expect(onChange).toHaveBeenCalledWith({ focusKind: null });
  });

  it('does not retry a deterministic conflict', async () => {
    await act(async () => root.render(
      <VoiceCoachingSnapshotSaveCard
        review={{ ...review, saveState: 'conflict' }}
        onChange={vi.fn()}
        onSave={vi.fn()}
        onSkip={vi.fn()}
      />,
    ));
    expect(host.textContent).toContain('已有另一份表达复盘');
    expect([...host.querySelectorAll('button')].some((item) => item.textContent?.includes('重试'))).toBe(false);
  });
});
