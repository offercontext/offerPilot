// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const service = vi.hoisted(() => ({ plans: vi.fn(), feedback: vi.fn() }));
vi.mock('@/services/adaptiveInterviewPractice', () => ({ listAdaptivePracticePlans: service.plans }));
vi.mock('@/features/reviewReadiness/service', () => ({ getEventReadinessFeedback: service.feedback }));
import InterviewPracticeHome from './InterviewPracticeHome';
let host: HTMLDivElement;
let root: Root;
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });
const click = (label: string) => act(() => [...host.querySelectorAll('button')].find((button) => button.textContent === label)?.click());
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  service.plans.mockReset().mockResolvedValue([]);
  service.feedback.mockReset().mockResolvedValue({ application_id: 7, event_id: 9, items: [] });
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
describe('InterviewPracticeHome', () => {
  it('exposes distinct actions immediately without creating any practice or starting an engine', async () => {
    const open = vi.fn(); const due = vi.fn();
    act(() => root.render(<InterviewPracticeHome onOpenPractice={open} onOpenQuestions={due} dueCount={3} />));
    await flush();
    expect(host.textContent).toContain('快速模拟');
    expect(host.textContent).toContain('复盘重点练习');
    expect(host.textContent).toContain('3 道到期题');
    expect(open).not.toHaveBeenCalled(); expect(due).not.toHaveBeenCalled();
    click('快速模拟'); expect(open).toHaveBeenLastCalledWith('quick');
    click('复盘重点练习'); expect(open).toHaveBeenLastCalledWith('review');
    click('复习到期题'); expect(due).toHaveBeenCalledOnce();
  });
  it('keeps a confirmed focus bound to its exact upcoming event and a continuation to its original pair', async () => {
    service.plans.mockResolvedValue([{ id: 1, title: '继续重点', status: 'in_progress', practice_state: 'in_progress', origin_contract: 'confirmed_readiness_signal_v1', readiness_signal_version_id: 44, target_application_event_id: 22 }]);
    service.feedback.mockResolvedValue({ application_id: 7, event_id: 9, items: [{ versionId: 55, title: '已确认目标', state: 'available', practiceState: 'not_started' }, { versionId: 56, title: '已撤回目标', state: 'retracted', practiceState: 'not_started' }] });
    const focused = vi.fn();
    act(() => root.render(<InterviewPracticeHome target={{ applicationId: 7, eventId: 9, label: '真实目标' }} onOpenFocus={focused} />));
    await flush();
    expect(service.feedback).toHaveBeenCalledWith(7, 9);
    click('继续上次练习'); expect(focused).toHaveBeenLastCalledWith({ signalVersionId: 44, targetEventId: 22 });
    click('练习这个重点'); expect(focused).toHaveBeenLastCalledWith({ signalVersionId: 55, targetEventId: 9 });
    expect(host.textContent).not.toContain('已撤回目标');
  });
  it('does not relabel missing or failed reads as zero and ignores late target responses', async () => {
    service.plans.mockRejectedValue(new Error('offline'));
    let resolveOld!: (value: unknown) => void;
    service.feedback.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    act(() => root.render(<InterviewPracticeHome target={{ applicationId: 7, eventId: 9, label: '旧目标' }} dueError />));
    await flush();
    act(() => root.render(<InterviewPracticeHome dueError />));
    await act(async () => resolveOld({ application_id: 7, event_id: 9, items: [{ versionId: 55, title: '迟到目标', state: 'available', practiceState: 'not_started' }] }));
    expect(host.textContent).not.toContain('迟到目标');
    expect(host.textContent).toContain('练习记录暂时无法读取');
    expect(host.textContent).toContain('到期题目暂时无法读取');
    expect(host.textContent).not.toContain('0 道到期题');
  });
});
