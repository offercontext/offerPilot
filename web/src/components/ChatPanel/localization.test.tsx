// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setLocale } from '@/i18n';
import type { ChatMessage, PendingAction } from '@/types/chat';
import Composer from './Composer';
import ProposalCard from './ProposalCard';
import ProcessTimeline from './ProcessTimeline';
import ThinkingIndicator from './ThinkingIndicator';
import { capabilitiesForMode } from './capabilities';
import { buildTurns, evidenceIdentity, displayPilotUiMessage } from './model';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
let root: Root | undefined;
let host: HTMLDivElement;

function render(ui: React.ReactNode) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(ui));
  return host;
}
function button(text: string) {
  return [...host.querySelectorAll('button')].find((item) => item.textContent?.trim() === text)!;
}
function change(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}
beforeEach(() => setLocale('zh-CN'));
afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  host?.remove();
  setLocale('zh-CN');
});

describe('Pilot live locale switching', () => {
  it('updates composer controls and capability search without resetting or sending a draft', () => {
    const send = vi.fn();
    render(<Composer capabilities={capabilitiesForMode(false)} onSend={send} />);
    const input = host.querySelector('textarea')!;
    act(() => change(input, '保留我的中文草稿'));
    act(() => setLocale('en-US'));
    expect(host.querySelector('textarea')).toBe(input);
    expect(input.value).toBe('保留我的中文草稿');
    expect(input.placeholder).toBe('Ask Pilot, or type / to browse actions');
    expect(host.querySelector('[aria-label="Send"]')).not.toBeNull();
    expect(send).not.toHaveBeenCalled();
    act(() => change(input, '/application'));
    expect(host.textContent).toContain('View application progress');
    act(() => setLocale('zh-CN'));
    expect(input.value).toBe('/application');
    expect(host.querySelector('[role="listbox"]')).toBeNull();
  });

  it('keeps edited proposal fields, rejection feedback, and confirmation identity across switches', () => {
    const confirm = vi.fn();
    const cancel = vi.fn();
    const action: PendingAction = {
      tool_name: 'update_note', human: '保留 AI 的原始建议', confirmation_token: 'same-token',
      args: { id: 7, notes: '原始备注' }, editable_fields: [{ field: 'notes', type: 'long_text' }],
    };
    render(<ProposalCard action={action} loading={false} evidence={[]} onConfirm={confirm} onCancel={cancel} />);
    act(() => [...host.querySelectorAll('button')].find((el) => el.textContent?.includes('编辑建议'))!.click());
    const editor = host.querySelector('textarea')!;
    act(() => change(editor, '用户修改后的备注'));
    act(() => setLocale('en-US'));
    expect(host.querySelector('textarea')).toBe(editor);
    expect(editor.value).toBe('用户修改后的备注');
    expect(host.textContent).toContain('保留 AI 的原始建议');
    act(() => button('Reject proposal').click());
    const feedback = host.querySelector<HTMLTextAreaElement>('textarea[id$="-feedback"]')!;
    act(() => change(feedback, '先保留我的判断'));
    act(() => setLocale('zh-CN'));
    expect(host.querySelector('textarea[id$="-feedback"]')).toBe(feedback);
    expect(feedback.value).toBe('先保留我的判断');
    expect(confirm).not.toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
    act(() => button('返回审核').click());
    act(() => button('确认更新').click());
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveBeenCalledWith({ notes: '用户修改后的备注' });
  });

  it('localizes already-built evidence without changing user content or disclosure identity', () => {
    const messages = [
      { id: 1, role: 'assistant', tool_calls: JSON.stringify([{ name: 'list_resume_matches', args: {} }]), content: '' },
      { id: 2, role: 'tool', content: JSON.stringify([{ id: 9, resume_id: 6, application_id: 7, jd_text: '用户的岗位原文' }]) },
    ] as ChatMessage[];
    const steps = buildTurns(messages)[0].steps!;
    const identity = evidenceIdentity(steps[0].evidence![0]);
    render(<ProcessTimeline steps={steps} />);
    act(() => host.querySelector('button')!.click());
    expect(host.textContent).toContain('简历匹配 #9');
    act(() => setLocale('en-US'));
    expect(host.querySelector('button')!.getAttribute('aria-expanded')).toBe('true');
    expect(host.textContent).toContain('Resume match #9');
    expect(host.textContent).toContain('Resume #6 · Application #7');
    expect(host.textContent).toContain('用户的岗位原文');
    expect(evidenceIdentity(steps[0].evidence![0])).toBe(identity);
    act(() => setLocale('zh-CN'));
    expect(host.textContent).toContain('简历匹配 #9');
  });

  it('updates an ongoing loading label without changing the dynamic source summary', () => {
    render(<ThinkingIndicator label="正在处理：用户自定义内容" />);
    act(() => setLocale('en-US'));
    expect(host.textContent).toContain('Working on: 用户自定义内容');
    act(() => setLocale('zh-CN'));
    expect(host.textContent).toContain('正在处理：用户自定义内容');
  });
});

it('re-localizes retained authored runtime failures without rewriting custom errors', () => {
  setLocale('en-US');
  const english = displayPilotUiMessage('连接已断开，任务仍可能在运行。重新打开对话可查看已保存的结果。');
  expect(english).not.toContain('连接');
  setLocale('zh-CN');
  expect(displayPilotUiMessage(english)).toBe('连接已断开，任务仍可能在运行。重新打开对话可查看已保存的结果。');
  expect(displayPilotUiMessage('My custom failure / 用户文本')).toBe('My custom failure / 用户文本');
});
