// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setLocale, t } from '@/i18n';
import type { MissionMetric } from '@/lib/missionControl';
import WeeklyMissionPanel, { deriveWeeklySummary } from './WeeklyMissionPanel';

const metrics: MissionMetric[] = [
  { kind: 'applications', label: '本周投递', current: 3, target: 4, state: 'on_track', reason: '已新增 3 个投递。', targetView: 'board' },
  { kind: 'materials', label: '材料准备', current: 3, target: 4, state: 'watch', reason: '3/4 份材料已就绪。', targetView: 'board' },
  { kind: 'interviews', label: '面试练习', current: 2, state: 'behind', reason: '本周已安排 2 场。', targetView: 'calendar' },
  { kind: 'offers', label: '待跟进投递', current: 1, target: 1, state: 'blocked', reason: '1 个 Offer 即将截止。', targetView: 'offers' },
];

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  setLocale('zh-CN');
});

describe('WeeklyMissionPanel', () => {
  it('summarizes heterogeneous mission states without inventing a completion percentage', () => {
    expect(deriveWeeklySummary([])).toBe('暂无可汇总的关键事项');
    expect(deriveWeeklySummary(metrics)).toBe('1 项阻塞，2 项需要关注');
    expect(deriveWeeklySummary(metrics, ['interviews'])).toBe('1 项阻塞，1 项需要关注，部分数据暂不可用');
  });

  it('renders the progress summary and navigates through an existing metric row', () => {
    const navigate = vi.fn();
    act(() => root.render(<WeeklyMissionPanel metrics={metrics} onNavigate={navigate} />));

    expect(container.textContent).toContain('本周求职进度');
    expect(container.textContent).toContain('关键事项：1 项阻塞，2 项需要关注');
    expect(container.querySelector('[role="progressbar"]')).toBeNull();
    expect(container.textContent).toContain('3 / 4');
    expect(container.textContent).toContain('已安排 2 场');

    const materialRow = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('材料准备'));
    act(() => materialRow?.click());
    expect(navigate).toHaveBeenCalledWith('board');
  });

  it.each([0, 1, 2])('uses grammatical English interview counts for %i', (count) => {
    setLocale('en-US');
    const reason = t('本周有 {interviewsThisWeek} 个面试或测评日程。', { interviewsThisWeek: count });
    act(() => root.render(<WeeklyMissionPanel metrics={[{
      kind: 'interviews', label: 'Interviews this week', current: count,
      state: 'on_track', reason, targetView: 'calendar',
    }]} onNavigate={vi.fn()} />));
    const row = container.querySelector('button');
    expect(row?.textContent).toContain(`${count} ${count === 1 ? 'interview' : 'interviews'}`);
    expect(row?.textContent).toContain(`Interviews or assessments scheduled this week: ${count}.`);
    if (count === 1) expect(row?.textContent).not.toContain('1 interviews');
  });

  it.each([
    ['practice', '1 question to review'],
    ['followups', '1 follow-up'],
    ['offers', '1 approaching deadline'],
  ] as const)('uses a singular English value for %s', (kind, value) => {
    setLocale('en-US');
    act(() => root.render(<WeeklyMissionPanel metrics={[{
      kind, label: kind, current: 1, state: 'watch', reason: '', targetView: 'board',
    }]} onNavigate={vi.fn()} />));
    expect(container.querySelector('button')?.textContent).toContain(value);
  });

  it('marks query-dependent rows as unavailable instead of treating unknown facts as on track', () => {
    act(() => root.render(
      <WeeklyMissionPanel metrics={metrics} unavailableKinds={['interviews']} onNavigate={vi.fn()} />,
    ));

    expect(container.textContent).toContain('关键事项：1 项阻塞，1 项需要关注，部分数据暂不可用');
    const interviewRow = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('面试练习'));
    expect(interviewRow?.textContent).toContain('数据暂不可用');
    expect(interviewRow?.textContent).toContain('未知');
    expect(interviewRow?.textContent).not.toContain('本周已安排 2 场');
    expect(interviewRow?.className).toContain('metric-unavailable');
    expect(interviewRow?.className).not.toContain('metric-behind');
  });
});
