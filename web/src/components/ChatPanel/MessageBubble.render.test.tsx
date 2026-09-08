/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import MessageBubble from './MessageBubble';
import type { ToolStep, UITurn } from './model';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

let root: Root | undefined;

function renderBubble(turn: UITurn, onAction = vi.fn(), taskCardsEnabled = true) {
  const container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  act(() =>
    root?.render(
      <MessageBubble
        turn={turn}
        index={0}
        actionsDisabled={false}
        onAction={onAction}
        taskCardsEnabled={taskCardsEnabled}
      />,
    ),
  );
  return { container, onAction };
}

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  document.body.replaceChildren();
});

const completedStep: ToolStep = {
  name: 'list_applications',
  detail: 'Product manager',
};

function occurrences(content: string, text: string) {
  return content.split(text).length - 1;
}

describe('MessageBubble task cards', () => {
  it('renders a structured assistant conclusion and its residual evidence once, then dispatches its action once', () => {
    const turn: UITurn = {
      role: 'assistant',
      content: 'Supporting evidence.',
      presentation: {
        conclusion: 'Structured conclusion.',
        actions: ['review application'],
        detailMarkdown: 'Supporting evidence.',
      },
    };
    const onAction = vi.fn();
    const { container } = renderBubble(turn, onAction);

    expect(container.querySelector('article')).not.toBeNull();
    expect(occurrences(container.textContent ?? '', turn.presentation!.conclusion)).toBe(1);
    expect(occurrences(container.textContent ?? '', turn.presentation!.detailMarkdown)).toBe(1);

    const action = container.querySelector<HTMLButtonElement>('button[aria-label="继续：review application"]');
    expect(action).not.toBeNull();
    act(() => action?.click());
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith('review application');
  });

  it('renders a plain assistant turn with steps in one task-card timeline rather than a legacy timeline', () => {
    const { container } = renderBubble({
      role: 'assistant',
      content: 'Tool result details.',
      steps: [completedStep],
    });

    const card = container.querySelector('article');
    expect(card).not.toBeNull();
    expect(card?.querySelector('[aria-label="AI 操作摘要"]')).not.toBeNull();
    expect(container.querySelectorAll('[aria-label="AI 操作摘要"]')).toHaveLength(1);
  });

  it('does not infer a frozen source from ordinary tool evidence', () => {
    const { container } = renderBubble({
      role: 'assistant',
      content: 'Tool result details.',
      steps: [{
        ...completedStep,
        evidence: [{
          id: 'query-result-1',
          kind: 'application',
          title: '普通查询结果',
          meta: '当次返回',
          snippet: '仅当次可验证记录',
          source: 'applications',
        }],
      }],
    });

    expect(container.textContent).not.toContain('已冻结来源');
  });

  it('keeps an ordinary assistant response as a markdown bubble without a task card', () => {
    const { container } = renderBubble({ role: 'assistant', content: 'A normal response.' });

    expect(container.querySelector('article')).toBeNull();
    expect(container.textContent).toContain('A normal response.');
  });

  it('renders an assistant confirmation table with the full-width table treatment', () => {
    const { container } = renderBubble({
      role: 'assistant',
      content: [
        '请先核对确认卡：',
        '',
        '| 项目 | 内容 |',
        '| --- | --- |',
        '| 公司 | 云栖智能（演示） |',
        '| 岗位 | AI 应用工程师 |',
      ].join('\n'),
    });

    const table = container.querySelector<HTMLTableElement>('table');
    expect(table).not.toBeNull();
    expect(table?.className).toContain('markdownTable');
  });

  it('uses the full-width table treatment for GFM tables without outer pipes', () => {
    const { container } = renderBubble({
      role: 'assistant',
      content: [
        '请先核对确认卡：',
        '',
        '项目 | 内容',
        '--- | ---',
        '公司 | 云栖智能（演示）',
        '岗位 | AI 应用工程师',
      ].join('\n'),
    });

    const table = container.querySelector<HTMLTableElement>('table');
    expect(table).not.toBeNull();
    expect(table?.className).toContain('markdownTable');
  });

  it('does not treat a fenced table-like code sample as a confirmation table', () => {
    const { container } = renderBubble({
      role: 'assistant',
      content: ['```text', '| 项目 | 内容 |', '| --- | --- |', '```'].join('\n'),
    });

    expect(container.querySelector('table')).toBeNull();
    expect(container.querySelector('pre')).not.toBeNull();
  });

  it.each([
    ['a longer closing fence', ['```text', '| 项目 | 内容 |', '| --- | --- |', '````']],
    ['an unclosed fence', ['```text', '| 项目 | 内容 |', '| --- | --- |']],
  ])('does not treat a table-like code sample with %s as a confirmation table', (_, lines) => {
    const { container } = renderBubble({
      role: 'assistant',
      content: lines.join('\n'),
    });

    expect(container.querySelector('table')).toBeNull();
  });

  it('keeps non-Pilot callers on the legacy timeline when task cards are disabled', () => {
    const { container } = renderBubble(
      {
        role: 'assistant',
        content: 'Tool result details.',
        steps: [completedStep],
      },
      vi.fn(),
      false,
    );

    expect(container.querySelector('article')).toBeNull();
    expect(container.querySelectorAll('[aria-label="AI 操作摘要"]')).toHaveLength(1);
  });
});
