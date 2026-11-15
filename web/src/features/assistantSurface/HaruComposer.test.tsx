// @vitest-environment jsdom
import { act, createRef, type ComponentProps } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setLocale } from '@/i18n';
import HaruComposer from './HaruComposer';

vi.mock('@/components/ChatPanel/ChatImagePreview', () => ({ default: () => <span>图片预览</span> }));
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement, root: Root;
const ready = { key: 'one', scopeKey: 'new:workspace:', scope: {}, name: '截图.png', status: 'ready' as const };
function mount(overrides: Partial<ComponentProps<typeof HaruComposer>> = {}) {
  const props = { value: '', onChange: vi.fn(), onSend: vi.fn(), disabled: false,
    inputRef: createRef<HTMLTextAreaElement>(), drafts: [ready], onAdd: vi.fn(), onRemove: vi.fn(), onRetry: vi.fn(), ...overrides };
  act(() => root.render(<HaruComposer {...props} />));
  return props;
}
const send = () => host.querySelector<HTMLButtonElement>('[aria-label="发送消息"]')!;
beforeEach(() => { host = document.createElement('div'); document.body.append(host); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); setLocale('zh-CN'); });

describe('Haru image composer', () => {
  it('translates image controls without losing the current draft', () => {
    mount({ value: '用户的草稿', drafts: [{ ...ready, status: 'error' }] });
    const composer = host.querySelector('textarea')!;
    act(() => setLocale('en-US'));
    expect(composer.value).toBe('用户的草稿');
    expect(host.querySelector('[aria-label="Add images"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Retry image 1"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Images to send"]')).not.toBeNull();
  });
  it('allows image-only submission but never submits while an upload failed or is in progress', () => {
    const props = mount();
    expect(send().disabled).toBe(false);
    act(() => send().click());
    expect(props.onSend).toHaveBeenCalledOnce();
    for (const status of ['uploading', 'error'] as const) {
      const blocked = mount({ value: '附带文字', drafts: [{ ...ready, status, error: status === 'error' ? '网络中断' : undefined }] });
      expect(send().disabled).toBe(true);
      act(() => host.querySelector('textarea')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
      expect(blocked.onSend).not.toHaveBeenCalled();
    }
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('截图.png：网络中断');
  });
  it('preserves mixed clipboard text and routes selected, pasted and dropped images through the same callback', () => {
    const props = mount({ drafts: [] });
    const file = new File(['png'], '截图.png', { type: 'image/png' });
    const selection = host.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(selection, 'files', { value: [file] });
    act(() => selection.dispatchEvent(new Event('change', { bubbles: true })));
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(paste, 'clipboardData', { value: { files: [file], getData: () => '同时粘贴的文字' } });
    act(() => host.querySelector('textarea')!.dispatchEvent(paste));
    expect(paste.defaultPrevented).toBe(false);
    const drop = new Event('drop', { bubbles: true, cancelable: true });
    Object.defineProperty(drop, 'dataTransfer', { value: { files: [file] } });
    act(() => host.querySelector('footer')!.dispatchEvent(drop));
    expect(drop.defaultPrevented).toBe(true);
    expect(vi.mocked(props.onAdd).mock.calls).toEqual([[ [file] ], [ [file] ], [ [file] ]]);
  });
  it('blocks image entry for an unsupported model without blocking an image-free text message', () => {
    const props = mount({ value: '你好', drafts: [], imageDisabledReason: '当前模型不接受图片' });
    expect(host.querySelector<HTMLButtonElement>('[aria-label="添加图片"]')!.disabled).toBe(true);
    expect(send().disabled).toBe(false);
    const event = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', { value: { files: [new File(['x'], 'x.png', { type: 'image/png' })], getData: () => '' } });
    act(() => host.querySelector('textarea')!.dispatchEvent(event));
    expect(props.onAdd).not.toHaveBeenCalled();
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('当前模型不接受图片');
    mount({ value: '你好', imageDisabledReason: '当前模型不接受图片' });
    expect(send().disabled).toBe(true);
  });
  it('keeps failed images removable and retries exactly that upload', () => {
    const failed = { ...ready, status: 'error' as const, error: '请重试' };
    const props = mount({ drafts: [failed] });
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="重试图片 1"]')!.click());
    expect(props.onRetry).toHaveBeenCalledWith(failed);
    act(() => host.querySelector<HTMLButtonElement>('[aria-label^="移除图片"]')!.click());
    expect(props.onRemove).toHaveBeenCalledWith('one');
  });
  it('blocks mutation and submission while a confirmation or active execution disables the composer', () => {
    mount({ disabled: true });
    expect(send().disabled).toBe(true);
    expect(host.querySelector<HTMLButtonElement>('[aria-label^="移除图片"]')!.disabled).toBe(true);
    expect(host.querySelector('textarea')!.disabled).toBe(true);
  });
  it('does not send while confirming an IME composition', () => {
    const props = mount();
    act(() => host.querySelector('textarea')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true })));
    expect(props.onSend).not.toHaveBeenCalled();
  });
});
