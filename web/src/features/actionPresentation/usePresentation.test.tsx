// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UITurn } from '@/components/ChatPanel/model';
import type { PilotPresentationSnapshot } from './contracts';
import { usePilotPresentation } from './usePilotPresentation';

const api = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock('./service', () => ({ getPilotPresentation: api.read }));
let root: Root;
let host: HTMLDivElement;
const turns: UITurn[] = [{ id: 'message:1', role: 'user', content: '原始消息' }];
const snapshot = (conversationId: number, content: string): PilotPresentationSnapshot => ({
  schema_version: 1, conversation_id: conversationId,
  items: [{ schema_version: 1, item_id: `message:${conversationId + 10}`, kind: 'assistant_message', message_id: conversationId + 10, operation_id: null, content, action: null }],
});
function Owner({ conversationId, loading = false, shell = 'pilot', localTurns = turns }: { conversationId: number; loading?: boolean; shell?: string; localTurns?: UITurn[] }) {
  const { displayTurns, refreshPresentation } = usePilotPresentation(conversationId, localTurns, null, loading);
  return <div data-shell={shell}><button aria-label="refresh" onClick={refreshPresentation} />{displayTurns.map((turn) => <p key={turn.id} data-turn-id={turn.id} data-actions={turn.action?.available_actions.join(',')}>{turn.content}</p>)}</div>;
}
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  api.read.mockReset(); host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

describe('presentation request ownership', () => {
  it('discards an old conversation response that arrives after the new one', async () => {
    let first!: (value: PilotPresentationSnapshot) => void;
    let second!: (value: PilotPresentationSnapshot) => void;
    api.read.mockImplementationOnce(() => new Promise((resolve) => { first = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { second = resolve; }));
    act(() => root.render(<Owner conversationId={1} />));
    act(() => root.render(<Owner conversationId={2} />));
    await act(async () => second(snapshot(2, '新会话')));
    expect(host.textContent).toBe('新会话');
    await act(async () => first(snapshot(1, '旧会话')));
    expect(host.textContent).toBe('新会话');
  });
  it('shares the same read on a shell switch and does not fetch while execution is active', async () => {
    api.read.mockResolvedValue(snapshot(1, '稳定消息'));
    await act(async () => root.render(<Owner conversationId={1} />));
    await act(async () => root.render(<Owner conversationId={1} shell="haru" />));
    expect(api.read).toHaveBeenCalledTimes(1);
    expect(host.textContent).toBe('稳定消息');
    act(() => root.render(<Owner conversationId={1} loading shell="haru" />));
    expect(api.read).toHaveBeenCalledTimes(1);
    expect(host.textContent).toBe('稳定消息');
  });
  it('preserves original messages when display loading fails without retrying execution', async () => {
    api.read.mockRejectedValue(new Error('offline'));
    await act(async () => root.render(<Owner conversationId={1} />));
    expect(host.textContent).toBe('原始消息');
    expect(api.read).toHaveBeenCalledTimes(1);
  });
  it('falls back to the latest persisted messages after a cached display refresh fails and can recover', async () => {
    let fail!: (reason: Error) => void;
    api.read.mockResolvedValueOnce(snapshot(1, '旧展示'))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }))
      .mockResolvedValueOnce(snapshot(1, '恢复展示'));
    await act(async () => root.render(<Owner conversationId={1} />));
    const latest: UITurn[] = [...turns,
      { id: 'message:20', role: 'user', content: '最新问题' },
      { id: 'message:21', role: 'assistant', content: '最新回复' }];
    act(() => root.render(<Owner conversationId={1} localTurns={latest} />));
    expect(host.textContent).toBe('旧展示');
    await act(async () => fail(new Error('offline')));
    expect(host.textContent).toBe('原始消息最新问题最新回复');
    expect(api.read).toHaveBeenCalledTimes(2);
    await act(async () => (host.querySelector('button') as HTMLButtonElement).click());
    expect(host.textContent).toBe('恢复展示');
    expect(api.read).toHaveBeenCalledTimes(3);
  });
  it('does not clear a newer display when an obsolete refresh rejects', async () => {
    let fail!: (reason: Error) => void;
    api.read.mockResolvedValueOnce(snapshot(1, '旧展示'))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }))
      .mockResolvedValueOnce(snapshot(2, '新会话展示'));
    await act(async () => root.render(<Owner conversationId={1} />));
    act(() => (host.querySelector('button') as HTMLButtonElement).click());
    await act(async () => root.render(<Owner conversationId={2} />));
    await act(async () => fail(new Error('obsolete request')));
    expect(host.textContent).toBe('新会话展示');
  });
  it('keeps the same operation node during execution and shows the new streaming message without restoring mutation commands', async () => {
    const saved = snapshot(1, '稳定消息');
    saved.items.push({ schema_version: 1, item_id: 'agent_operation:op-1', kind: 'action', message_id: null,
      operation_id: 'op-1', content: '', action: { schema_version: 1, source_kind: 'agent', operation_id: 'op-1',
        source_revision: 'source', presentation_revision: 'view', title: '保存', target: null, summary: '已保存', source_label: 'Pilot',
        decision: 'modified', execution: 'committed', evidence: 'verified', undo: 'available', available_actions: ['undo', 'refresh'] } });
    api.read.mockResolvedValue(saved);
    await act(async () => root.render(<Owner conversationId={1} />));
    const node = host.querySelector('[data-turn-id="agent_operation:op-1"]');
    expect(node?.getAttribute('data-actions')).toBe('undo,refresh');
    act(() => root.render(<Owner conversationId={1} loading localTurns={[...turns, { id: 'transient:1:2:assistant', role: 'assistant', content: '新的流式回复' }]} />));
    expect(host.querySelector('[data-turn-id="agent_operation:op-1"]')).toBe(node);
    expect(node?.getAttribute('data-actions')).toBe('refresh');
    expect(host.textContent).toContain('新的流式回复');
    expect(api.read).toHaveBeenCalledTimes(1);
  });
  it('refreshes only the display read and removes cached mutation capabilities until it settles', async () => {
    api.read.mockResolvedValueOnce(snapshot(1, '稳定消息')).mockImplementationOnce(() => new Promise(() => {}));
    await act(async () => root.render(<Owner conversationId={1} />));
    act(() => (host.querySelector('button') as HTMLButtonElement).click());
    expect(api.read).toHaveBeenCalledTimes(2);
    expect(host.textContent).toBe('稳定消息');
  });
});
