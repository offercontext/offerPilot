// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import NextStepSuggestions from './NextStepSuggestions';
import type { NextStepSuggestions as Suggestions, ReadonlyDestination, SuggestionSessionState } from '@/lib/nextStepSuggestions';

const firstCandidate = {
  id: 'first',
  stateKey: 'state-first',
  title: '第一项行动',
  reason: '第一项理由',
  destination: { kind: 'application_detail' as const, applicationId: 1 },
  sources: [{ label: '当前使用事件', status: 'current' as const }],
};

const secondCandidate = {
  id: 'second',
  stateKey: 'state-second',
  title: '第二项行动',
  reason: '第二项理由',
  destination: { kind: 'pilot_opportunity_fit' as const, applicationId: 1 },
  sources: [],
};

function suggestions(candidates: Suggestions['candidates'] = [firstCandidate, secondCandidate]): Suggestions {
  return { candidates, sourceRisks: [] };
}

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function render(
  applicationId: number,
  value: Suggestions = suggestions(),
  sessionState: SuggestionSessionState | null = null,
  onSetDisposition = vi.fn(),
  onNavigate = vi.fn(),
  isNavigationAvailable = () => true,
  onNavigateReadonly?: (destination: ReadonlyDestination) => void,
  isReadonlyNavigationAvailable = () => false,
) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root?.render(
      <NextStepSuggestions
        applicationId={applicationId}
        suggestions={value}
        sessionState={sessionState}
        onSetDisposition={onSetDisposition}
        onNavigate={onNavigate}
        isNavigationAvailable={isNavigationAvailable}
        onNavigateReadonly={onNavigateReadonly}
        isReadonlyNavigationAvailable={isReadonlyNavigationAvailable}
      />,
    );
  });
  return { view: container, onSetDisposition, onNavigate };
}

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = null;
  container = null;
});

describe('destination safety', () => {
  it('disables destinations that cannot preserve their required context', () => {
    const { view, onNavigate } = render(1, suggestions([{
      ...firstCandidate,
      destination: { kind: 'interview_review_history', applicationId: 1, eventId: 4, reviewId: 9 },
    }]), null, vi.fn(), vi.fn(), () => false);

    const navigate = view?.querySelector('button');
    expect(navigate).toHaveProperty('disabled', true);
    act(() => navigate?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(onNavigate).not.toHaveBeenCalled();
  });
});

describe('NextStepSuggestions', () => {
  it('renders the first candidate in priority order and not an arbitrary later candidate', () => {
    const { view } = render(1);

    expect(view?.textContent).toContain('第一项行动');
    expect(view?.textContent).not.toContain('第二项行动');
  });

  it('passes the exact primary destination without issuing a write request', () => {
    const onNavigate = vi.fn();
    const { view } = render(7, suggestions([{
      ...firstCandidate,
      destination: { kind: 'interview_event', applicationId: 7, eventId: 31 },
    }]), null, vi.fn(), onNavigate);
    const forward = view?.querySelector('button');

    act(() => forward?.dispatchEvent(new MouseEvent('click', { bubbles: true })));

    expect(onNavigate).toHaveBeenCalledWith({ kind: 'interview_event', applicationId: 7, eventId: 31 });
  });

  it('binds session disposition callbacks to applicationId', () => {
    const onSetDisposition = vi.fn();
    const { view } = render(7, suggestions([{
      ...firstCandidate,
      destination: { kind: 'application_detail', applicationId: 7 },
    }]), null, onSetDisposition);

    const snooze = [...(view?.querySelectorAll('button') ?? [])].find((button) => button.textContent === '稍后处理');
    act(() => snooze?.dispatchEvent(new MouseEvent('click', { bubbles: true })));

    expect(onSetDisposition).toHaveBeenCalledWith(7, 'first', {
      stateKey: 'state-first',
      disposition: 'snoozed',
    });
  });

  it('does not let two applications with the same suggestionId share state', () => {
    const onSetDisposition = vi.fn();
    const second = {
      ...firstCandidate,
      destination: { kind: 'application_detail' as const, applicationId: 2 },
    };
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root?.render(
        <>
          <NextStepSuggestions
            applicationId={1}
            suggestions={suggestions([firstCandidate])}
            sessionState={null}
            onSetDisposition={onSetDisposition}
            onNavigate={vi.fn()}
          />
          <NextStepSuggestions
            applicationId={2}
            suggestions={suggestions([second])}
            sessionState={null}
            onSetDisposition={onSetDisposition}
            onNavigate={vi.fn()}
          />
        </>,
      );
    });

    const buttons = [...(container.querySelectorAll('button') ?? [])].filter((button) => button.textContent === '稍后处理');
    act(() => buttons[1]?.dispatchEvent(new MouseEvent('click', { bubbles: true })));

    expect(onSetDisposition).toHaveBeenCalledWith(2, 'first', expect.objectContaining({ stateKey: 'state-first' }));
  });

  it('keeps source risks visible and non-clickable when no readonly destination exists', () => {
    const onNavigate = vi.fn();
    const { view } = render(1, {
      candidates: [],
      sourceRisks: [{
        id: 'risk-1',
        stateKey: 'risk-state',
        title: '来源已变化',
        reason: '请查看当前事实',
        sources: [{ label: '已冻结来源', status: 'changed' }],
      }],
    }, null, vi.fn(), onNavigate);

    expect(view?.textContent).toContain('来源已变化');
    const risk = view?.querySelector('[data-testid="source-risk"]');
    act(() => risk?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it('does not reuse the primary navigation for a source risk', () => {
    const onNavigate = vi.fn();
    const { view } = render(1, {
      candidates: [],
      sourceRisks: [{
        id: 'risk-interview',
        stateKey: 'risk-interview-state',
        title: '来源已变化',
        reason: '请先核对面试来源。',
        sources: [],
        readonlyDestination: { kind: 'interview_event', applicationId: 1, eventId: 9 },
      }],
    }, null, vi.fn(), onNavigate);

    expect(view?.textContent).toContain('来源已变化');
    expect(view?.textContent).toContain('暂不可打开');
    act(() => view?.querySelector('[data-testid="source-risk"]')?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it('uses a separate readonly adapter and preserves opportunity-fit history context', () => {
    const onNavigateReadonly = vi.fn();
    const { view } = render(1, {
      candidates: [],
      sourceRisks: [{
        id: 'risk-fit',
        stateKey: 'risk-fit-state',
        title: '岗位评估来源已变化',
        reason: '请查看历史评估。',
        sources: [],
        readonlyDestination: { kind: 'opportunity_fit_history', applicationId: 1, reviewId: 27 },
      }],
    }, null, vi.fn(), vi.fn(), () => true, onNavigateReadonly, () => true);
    act(() => view?.querySelector('button')?.dispatchEvent(new MouseEvent('click', { bubbles: true })));

    expect(onNavigateReadonly).toHaveBeenCalledWith({
      kind: 'opportunity_fit_history',
      applicationId: 1,
      reviewId: 27,
    });
  });

  it('only shows the candidate again when a saved stateKey is stale', () => {
    const { view } = render(1, suggestions([firstCandidate]), { stateKey: 'old-state', disposition: 'ignored' });

    expect(view?.textContent).toContain('第一项行动');
  });
});
