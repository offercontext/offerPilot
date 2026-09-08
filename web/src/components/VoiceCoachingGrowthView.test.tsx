// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const services = vi.hoisted(() => ({
  list: vi.fn(),
  trends: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('@/services/voiceCoaching', () => ({
  listVoiceCoachingSnapshots: services.list,
  getVoiceCoachingTrends: services.trends,
  deleteVoiceCoachingSnapshot: services.remove,
}));

const { default: VoiceCoachingGrowthView } = await import('./VoiceCoachingGrowthView');

const snapshot = {
  id: 7,
  attempt_id: 3,
  turn_id: 5,
  application_id: 11,
  event_id: 13,
  question_text: '请介绍一次线上故障处理经历。',
  confirmed_answer_text: '我先确认影响范围，再完成回滚并补充监控。',
  answer_sha256: 'abc',
  measurement_source: 'local_browser_measurement' as const,
  total_duration_ms: 72_000,
  voiced_duration_ms: 45_000,
  pause_count: 3,
  longest_pause_ms: 3_100,
  speech_rate_cpm: 118,
  filler_occurrences: [{ text: '然后', count: 2, transcript_offsets: [2, 8] }],
  reflection_text: '下一次先给结论。',
  focus_kind: 'long_pause_control' as const,
  origin_snapshot_id: null,
  created_at: '2026-08-14T10:00:00',
  source_available: true,
  company_name: '云栖智能',
  position_name: '高级后端工程师',
};

const trends = {
  snapshot_count: 4,
  window_size: 3,
  metrics: {
    total_duration_ms: { current_median: 72_000, previous_median: 80_000, delta: -8_000, source_snapshot_ids: [7], previous_source_snapshot_ids: [1] },
    longest_pause_ms: { current_median: 3_100, previous_median: 4_000, delta: -900, source_snapshot_ids: [7], previous_source_snapshot_ids: [1] },
    speech_rate_cpm: { current_median: 118, previous_median: 105, delta: 13, source_snapshot_ids: [7], previous_source_snapshot_ids: [1] },
    filler_per_minute: { current_median: 1.67, previous_median: 2.5, delta: -0.83, source_snapshot_ids: [7], previous_source_snapshot_ids: [1] },
  },
  recommendation: {
    focus_kind: 'long_pause_control' as const,
    title: '减少长停顿',
    reason: '连续长停顿仍较明显',
    source_snapshot_ids: [7, 6],
    source_snapshot_id: 7,
    application_id: 11,
    event_id: 13,
    question_text: snapshot.question_text,
    source_available: true,
  },
};

let host: HTMLDivElement;
let root: Root;

function button(label: string): HTMLButtonElement {
  const item = [...host.querySelectorAll<HTMLButtonElement>('button')].find((candidate) => candidate.textContent?.includes(label));
  if (!item) throw new Error(`missing button: ${label}`);
  return item;
}

async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  services.list.mockReset().mockResolvedValue([snapshot]);
  services.trends.mockReset().mockResolvedValue(trends);
  services.remove.mockReset().mockResolvedValue(undefined);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

describe('VoiceCoachingGrowthView', () => {
  it('shows deterministic trends, frozen history and a re-practice entry', async () => {
    const onPractice = vi.fn();
    await act(async () => root.render(<VoiceCoachingGrowthView onBack={vi.fn()} onPractice={onPractice} />));
    await flush();
    expect(host.textContent).toContain('表达成长');
    expect(host.textContent).toContain('减少长停顿');
    expect(host.textContent).toContain('最长停顿中位数');
    expect(host.textContent).toContain('不会自动写入知识库');
    expect(host.textContent).toContain('云栖智能 · 高级后端工程师');
    expect(host.textContent).toContain('不代表面试能力、通过率或岗位匹配度');
    act(() => button('针对这项再练一次').click());
    expect(onPractice).toHaveBeenCalledWith(trends.recommendation);
  });

  it('requires an explicit destructive confirmation before deleting', async () => {
    await act(async () => root.render(<VoiceCoachingGrowthView onBack={vi.fn()} onPractice={vi.fn()} />));
    await flush();
    act(() => button('删除').click());
    expect(services.remove).not.toHaveBeenCalled();
    expect(host.textContent).toContain('删除后不可恢复');
    act(() => button('确认删除').click());
    await flush();
    expect(services.remove).toHaveBeenCalledWith(7);
    expect(services.list).toHaveBeenCalledTimes(2);
  });

  it('renders an honest empty state without manufacturing a recommendation', async () => {
    services.list.mockResolvedValue([]);
    services.trends.mockResolvedValue({ ...trends, snapshot_count: 0, recommendation: null });
    await act(async () => root.render(<VoiceCoachingGrowthView onBack={vi.fn()} onPractice={vi.fn()} />));
    await flush();
    expect(host.textContent).toContain('完成一次语音回答并确认保存后');
    expect(host.textContent).not.toContain('针对这项再练一次');
  });

  it('keeps frozen history readable and disables an unavailable source', async () => {
    const longQuestion = `请说明${'一个复杂故障的定位过程'.repeat(30)}`;
    services.list.mockResolvedValue([{ ...snapshot, source_available: false, question_text: longQuestion }]);
    services.trends.mockResolvedValue({
      ...trends,
      recommendation: { ...trends.recommendation, source_available: false },
    });
    await act(async () => root.render(<VoiceCoachingGrowthView onBack={vi.fn()} onPractice={vi.fn()} />));
    await flush();
    expect(host.textContent).toContain(longQuestion);
    expect(host.textContent).toContain('原投递来源已不可见');
    expect(button('针对这项再练一次').disabled).toBe(true);
  });

  it('keeps available history visible when only trends fail to load', async () => {
    services.trends.mockRejectedValue(new Error('trend unavailable'));
    await act(async () => root.render(<VoiceCoachingGrowthView onBack={vi.fn()} onPractice={vi.fn()} />));
    await flush();
    expect(host.textContent).toContain('部分表达记录暂时无法加载');
    expect(host.textContent).toContain(snapshot.question_text);
  });

  it('does not present an empty history or recommendation when history loading fails', async () => {
    services.list.mockRejectedValue(new Error('history unavailable'));
    await act(async () => root.render(<VoiceCoachingGrowthView onBack={vi.fn()} onPractice={vi.fn()} />));
    await flush();

    expect(host.textContent).toContain('表达记录暂时无法加载');
    expect(host.textContent).not.toContain('完成一次语音回答并确认保存后');
    expect(host.textContent).not.toContain('针对这项再练一次');
  });

  it('invalidates a stale recommendation immediately after deletion even when trend refresh fails', async () => {
    services.trends
      .mockResolvedValueOnce(trends)
      .mockRejectedValueOnce(new Error('trend unavailable'));
    services.list.mockResolvedValueOnce([snapshot]).mockResolvedValueOnce([]);
    await act(async () => root.render(<VoiceCoachingGrowthView onBack={vi.fn()} onPractice={vi.fn()} />));
    await flush();
    act(() => button('删除').click());
    act(() => button('确认删除').click());
    await flush();

    expect(host.textContent).not.toContain('针对这项再练一次');
    expect(host.textContent).not.toContain('减少长停顿');
  });
});
