// @vitest-environment jsdom
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionCard } from './ActionCard';
import { mergePresentationTurns, withTransportUncertainty } from './model';
import type { ActionPresentationV1 } from './contracts';
import { agentActionCommands, pendingPresentationActions } from './commands';

let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
function render(node: ReactNode) { act(() => root.render(node)); return { rerender: render }; }
const screen = {
  getByText: (text: string) => [...host.querySelectorAll('*')].find((el) => el.textContent === text) ?? null,
  queryByText: (text: string) => [...host.querySelectorAll('*')].find((el) => el.textContent === text) ?? null,
  queryByRole: (_role: string) => host.querySelector('button'),
  getByRole: (_role: string, options: { name: string }) => [...host.querySelectorAll('button')].find((el) => el.textContent === options.name)!,
};
const fireEvent = { click: (el: HTMLElement) => act(() => el.click()) };
const saved: ActionPresentationV1 = {
  schema_version: 1, source_kind: 'agent', operation_id: 'op-1',
  source_revision: 'source-1', presentation_revision: 'view-1',
  title: '修改 Offer', target: '测试岗位', summary: '已保存薪资：8000', source_label: 'Pilot',
  decision: 'modified', execution: 'committed', evidence: 'verified', undo: 'available',
  available_actions: ['undo'],
};

describe('shared action presentation', () => {
  it('denies a known operation omitted by a current snapshot but preserves legacy transport fallback', () => {
    expect(pendingPresentationActions(null, 'op-1')).toBeUndefined();
    expect(pendingPresentationActions({ schema_version: 1, conversation_id: 1, items: [] }, 'op-1')).toEqual([]);
    expect(pendingPresentationActions({ schema_version: 99, conversation_id: 1, items: [] }, 'op-1')).toEqual([]);
    const mismatched = { schema_version: 1, conversation_id: 1, items: [{ schema_version: 1, item_id: 'agent_operation:op-1', kind: 'action' as const,
      message_id: null, operation_id: 'op-1', content: '', action: { ...saved, operation_id: 'foreign' } }] };
    expect(pendingPresentationActions(mismatched, 'op-1')).toEqual([]);
    expect(mergePresentationTurns([], mismatched)).toEqual([]);
  });
  it('replaces same-ID local content and private tool evidence with the authorized snapshot', () => {
    const local = [{ id: 'message:7', role: 'assistant' as const, content: '旧敏感正文', steps: [{ name: 'private', resultText: '私有记录' }] }];
    const snapshot = { schema_version: 1, conversation_id: 3, items: [{ schema_version: 1, item_id: 'message:7', kind: 'assistant_message' as const,
      message_id: 7, operation_id: null, content: '内容已隐藏', action: null }] };
    const result = mergePresentationTurns(local, snapshot);
    expect(result[0].content).toBe('内容已隐藏');
    expect(result[0].steps).toBeUndefined();
    expect(mergePresentationTurns(local, { ...snapshot, items: [] })).toEqual([]);
  });
  it.each(['__proto__', 'future_command'])('safely rejects unknown server command %s', (command) => {
    render(<ActionCard action={{ ...saved, available_actions: [command] as ActionPresentationV1['available_actions'] }} />);
    expect(screen.getByText('操作展示暂不可用')).not.toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });
  it('never binds an old operation card to the current undo owner', () => {
    const undoOperation = vi.fn().mockResolvedValue(undefined);
    expect(agentActionCommands(saved, { lastUndo: { parent_operation_id: 'different' }, undoOperation })).toEqual({});
    agentActionCommands(saved, { lastUndo: { parent_operation_id: saved.operation_id }, undoOperation }).undo?.();
    expect(undoOperation).toHaveBeenCalledWith(saved.operation_id);
  });
  it('marks an unacknowledged decision unknown without erasing a proven commit', () => {
    const pending = { ...saved, execution: 'not_started' as const, decision: 'undecided' as const };
    expect(withTransportUncertainty(pending, true).execution).toBe('unknown');
    expect(withTransportUncertainty(saved, true, true)).toMatchObject({ execution: 'committed', undo: 'unknown' });
    expect(saved.undo).toBe('available');
  });
  it('shows committed and undo conflict independently without rewriting the original receipt', () => {
    render(<ActionCard action={{ ...saved, undo: 'conflict', available_actions: [] }} />);
    expect(screen.getByText('已保存')).not.toBeNull();
    expect(screen.getByText('撤销冲突')).not.toBeNull();
    expect(screen.getByText(saved.summary)).not.toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });
  it('requires both a current server action and a local command owner', () => {
    const invoke = vi.fn();
    const { rerender } = render(<ActionCard action={saved} />);
    expect(screen.queryByRole('button')).toBeNull();
    rerender(<ActionCard action={saved} commands={{ undo: invoke }} />);
    fireEvent.click(screen.getByRole('button', { name: '撤销本次操作' }));
    expect(invoke).toHaveBeenCalledTimes(1);
    rerender(<ActionCard action={{ ...saved, available_actions: [] }} commands={{ undo: invoke }} />);
    expect(screen.queryByRole('button')).toBeNull();
  });
  it('does not show unsupported or contradictory state as success', () => {
    const { rerender } = render(<ActionCard action={{ ...saved, schema_version: 99 }} commands={{ undo: vi.fn() }} />);
    expect(screen.getByText('操作展示暂不可用')).not.toBeNull();
    expect(screen.queryByText('已保存')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
    rerender(<ActionCard action={{ ...saved, decision: 'rejected' }} />);
    expect(screen.queryByText('已保存')).toBeNull();
  });
  it('preserves message identity and replaces the same pending operation with its committed card', () => {
    const turns = [{ id: 'message:7', role: 'assistant' as const, content: '原消息' }];
    const item = { schema_version: 1 as const, item_id: 'agent_operation:op-1', kind: 'action' as const,
      message_id: null, operation_id: 'op-1', content: '', action: saved };
    const snapshot = { schema_version: 1 as const, conversation_id: 3, items: [
      { ...item, item_id: 'message:7', kind: 'assistant_message' as const, message_id: 7, operation_id: null, content: '原消息', action: null },
      item,
    ] };
    const result = mergePresentationTurns(turns, snapshot);
    expect(result.map((turn) => turn.id)).toEqual(['message:7', 'agent_operation:op-1']);
    expect(result[1].action?.execution).toBe('committed');
  });
});
