/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage } from '@/types/chat';
import { mergePresentationTurns } from '@/features/actionPresentation/model';
import MessageBubble from './MessageBubble';
import { buildTurns, reloadConversationTurns, type ToolStep, type UITurn } from './model';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

let root: Root | undefined;

function renderBubble(turn: UITurn, onAction = vi.fn(), taskCardsEnabled = true) {
  const container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  const rerender = (nextTurn: UITurn) => act(() =>
    root?.render(
      <MessageBubble
        turn={nextTurn}
        index={0}
        actionsDisabled={false}
        onAction={onAction}
        taskCardsEnabled={taskCardsEnabled}
      />,
    ),
  );
  rerender(turn);
  return { container, onAction, rerender };
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

function storedMessage(id: number, role: ChatMessage['role'], content: string): ChatMessage {
  return { id, conversation_id: 42, role, content, created_at: '2026-10-24T00:00:00Z' };
}

describe('MessageBubble task cards', () => {
  it('renders a persisted legacy conclusion and residual evidence once, then dispatches only on click', () => {
    const [turn] = buildTurns([storedMessage(1, 'assistant', [
      'Supporting evidence.', '', '## 结论', '', 'Structured conclusion.',
      '', '## 下一步', '', '- review application',
    ].join('\n'))]);
    const onAction = vi.fn();
    const { container } = renderBubble(turn, onAction);

    expect(container.querySelector('article')).not.toBeNull();
    expect(occurrences(container.textContent ?? '', turn.presentation!.conclusion)).toBe(1);
    expect(occurrences(container.textContent ?? '', turn.presentation!.detailMarkdown)).toBe(1);

    const action = container.querySelector<HTMLButtonElement>('button[aria-label="继续：review application"]');
    expect(action).not.toBeNull();
    expect(occurrences(container.textContent ?? '', 'review application')).toBe(1);
    expect(onAction).not.toHaveBeenCalled();
    act(() => action?.click());
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith('review application');
  });

  it('keeps a heading-free work reply and its persisted tool-result fallback in one expandable task card', async () => {
    const content = '尚未查到匹配岗位，不能据此判断投递失败。\n\n- 放宽职位名称后再比较。';
    const turns = await reloadConversationTurns(42, async () => [
      storedMessage(1, 'user', '比较产品经理岗位'),
      { ...storedMessage(2, 'assistant', ''), tool_calls: JSON.stringify([
        { id: 'read-applications', name: 'list_applications', args: { query: '产品经理' } },
      ]) },
      { ...storedMessage(3, 'tool', '[]'), tool_call_id: 'read-applications' },
      storedMessage(4, 'assistant', content),
    ]);
    const turn = turns![1];
    expect(turn.presentation).toBeUndefined();
    expect(turn.content).toBe(content);
    const { container, onAction } = renderBubble(turn);

    const card = container.querySelector('article');
    expect(card).not.toBeNull();
    expect(card?.querySelector('[aria-label="AI 操作摘要"]')).not.toBeNull();
    expect(container.querySelectorAll('[aria-label="AI 操作摘要"]')).toHaveLength(1);
    expect(container.textContent).toContain('尚未查到匹配岗位，不能据此判断投递失败。');
    expect(container.querySelector('section[aria-label="下一步"]')).toBeNull();
    const toggle = card?.querySelector<HTMLButtonElement>('[aria-label="AI 操作摘要"] button');
    act(() => toggle?.click());
    expect(toggle?.getAttribute('aria-expanded')).toBe('true');
    expect(container.textContent).toContain('工具返回：没有匹配结果');
    expect(occurrences(container.textContent ?? '', '放宽职位名称后再比较。')).toBe(1);
    expect(onAction).not.toHaveBeenCalled();
  });

  it('preserves substantive work and ordinary lists across incremental render, stored reload, and timeline projection', async () => {
    const analysis = '两份岗位的技术要求重叠，但第二份更看重跨团队交付。';
    const items = ['第一份：补充高并发项目的量化结果。', '第二份：说明你如何推动跨团队协作。'];
    const caution = '下一步可以先改写项目经历；这里的“确认”只是建议，不代表已批准或保存。';
    const deltas = [analysis, `\n\n- ${items[0]}`, `\n- ${items[1]}\n\n${caution}`];
    let content = '';
    const { container, onAction, rerender } = renderBubble({ role: 'assistant', content });

    // ChatPanel supplies accumulated assistant_delta content to this renderer
    // before the final response is rebuilt from persisted messages.
    for (const delta of deltas) {
      content += delta;
      rerender({ id: 'transient:42:1:assistant', role: 'assistant', content });
      expect(occurrences(container.textContent ?? '', analysis)).toBe(1);
      for (const item of items.filter((item) => content.includes(item))) {
        expect(occurrences(container.textContent ?? '', item)).toBe(1);
      }
      expect(container.querySelector('article')).toBeNull();
      expect(container.querySelector('button')).toBeNull();
    }
    const rendered = container.textContent;
    expect(container.textContent).toContain(caution);
    expect(Array.from(container.querySelectorAll('ul > li'), (item) => item.textContent)).toEqual(items);

    const turns = await reloadConversationTurns(42, async () => [
      storedMessage(1, 'user', '请分析这两个岗位的差异，不要替我修改记录'),
      storedMessage(2, 'assistant', content),
    ]);
    const reloaded = turns![1];
    const projected = mergePresentationTurns(turns!, {
      schema_version: 1,
      conversation_id: 42,
      items: [{ schema_version: 1, item_id: 'message:2', kind: 'assistant_message',
        message_id: 2, operation_id: null, content, action: null }],
    })[0];
    for (const turn of [reloaded, projected]) {
      expect(turn.content).toBe(content);
      expect(turn.presentation).toBeUndefined();
      expect(turn.action).toBeUndefined();
      rerender(turn);
      expect(container.textContent).toBe(rendered);
      expect(container.querySelector('article')).toBeNull();
      expect(container.querySelector('button')).toBeNull();
      expect(Array.from(container.querySelectorAll('ul > li'), (item) => item.textContent)).toEqual(items);
    }
    expect(onAction).not.toHaveBeenCalled();
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
