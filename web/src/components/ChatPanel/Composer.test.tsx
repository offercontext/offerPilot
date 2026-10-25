// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Composer from './Composer';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | undefined;
let root: Root | undefined;

function render(ui: React.ReactNode) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root?.render(ui));
  return container;
}

function changeValue(input: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = undefined;
  container = undefined;
});

describe('Composer quick questions', () => {
  it('routes immediate slash commands separately from explicit image submission', async () => {
    const onSend = vi.fn().mockResolvedValue('sent');
    const onCapabilitySend = vi.fn().mockResolvedValue('sent');
    const draft = { key: 'image', scopeKey: 'new', scope: {}, name: '截图', previewUrl: 'blob:preview', status: 'ready' as const };
    const cap = { id: 'list', group: 'test', label: 'List', hint: 'List items', prompt: 'List my items', icon: () => null };
    const view = render(<Composer capabilities={[cap]} imageDrafts={[draft]} onSend={onSend} onCapabilitySend={onCapabilitySend} />);
    act(() => changeValue(view.querySelector('textarea')!, '/'));
    await act(async () => view.querySelector('textarea')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    expect(onCapabilitySend).toHaveBeenCalledWith('List my items');
    expect(onSend).not.toHaveBeenCalled();
    expect(view.querySelector('[aria-label="待发送图片"]')).not.toBeNull();
    await act(async () => view.querySelector<HTMLButtonElement>('[aria-label="发送"]')!.click());
    expect(onSend).toHaveBeenCalledWith('');
  });
  it('sends a ready image without text, and blocks an uploading or failed image', async () => {
    const onSend = vi.fn().mockResolvedValue('sent');
    const draft = { key: 'image', scopeKey: 'new', scope: {}, name: '截图', previewUrl: 'blob:preview', status: 'ready' as const };
    const view = render(<Composer capabilities={[]} imageDrafts={[draft]} onSend={onSend} />);
    await act(async () => view.querySelector<HTMLButtonElement>('[aria-label="发送"]')?.click());
    expect(onSend).toHaveBeenCalledWith('');
    for (const status of ['uploading', 'error'] as const) {
      act(() => root?.render(<Composer capabilities={[]} imageDrafts={[{ ...draft, status }]} onSend={onSend} />));
      expect(view.querySelector<HTMLButtonElement>('[aria-label="发送"]')?.disabled).toBe(true);
    }
  });

  it('uploads pasted images without cancelling mixed plain text paste', () => {
    const onAddImages = vi.fn();
    const view = render(<Composer capabilities={[]} onAddImages={onAddImages} onSend={vi.fn()} />);
    const image = new File(['pixels'], 'clip.png', { type: 'image/png' });
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(paste, 'clipboardData', { value: { files: [image], getData: () => '保留文字' } });
    act(() => view.querySelector('textarea')?.dispatchEvent(paste));
    expect(onAddImages).toHaveBeenCalledWith([image]);
    expect(paste.defaultPrevented).toBe(false);
  });

  it('uses the shared controlled draft without sending it', () => {
    const onSend = vi.fn();
    const onDraftChange = vi.fn();
    const view = render(
      <Composer
        capabilities={[]}
        draftValue="和 Pilot 讨论这份 Offer"
        onDraftChange={onDraftChange}
        onSend={onSend}
      />,
    );
    const input = view.querySelector<HTMLTextAreaElement>('textarea');

    expect(input?.value).toBe('和 Pilot 讨论这份 Offer');
    expect(onSend).not.toHaveBeenCalled();
    act(() => changeValue(input!, '保留新的共享草稿'));
    expect(onDraftChange).toHaveBeenCalledWith('保留新的共享草稿');
  });

  it('fills the composer without sending when a quick question is selected', () => {
    const onSend = vi.fn();
    const view = render(
      <Composer
        capabilities={[]}
        suggestions={['Compare this offer with my application']}
        onSend={onSend}
      />,
    );

    const question = view.querySelector<HTMLButtonElement>('[data-testid="quick-question-0"]');
    expect(question?.textContent).toContain('Compare this offer');

    act(() => question?.click());

    expect(view.querySelector<HTMLTextAreaElement>('textarea')?.value).toBe(
      'Compare this offer with my application',
    );
    expect(onSend).not.toHaveBeenCalled();
  });

  it('hides quick questions after typing and while disabled', () => {
    const view = render(
      <Composer capabilities={[]} suggestions={['Question one']} onSend={vi.fn()} />,
    );
    const input = view.querySelector<HTMLTextAreaElement>('textarea');

    expect(view.querySelector('[data-testid="quick-question-0"]')).not.toBeNull();
    act(() => changeValue(input!, 'A draft question'));
    expect(view.querySelector('[data-testid="quick-question-0"]')).toBeNull();

    act(() => root?.render(<Composer capabilities={[]} suggestions={['Question one']} disabled onSend={vi.fn()} />));
    expect(view.querySelector('[data-testid="quick-question-0"]')).toBeNull();
  });
});

describe('Composer onboarding focus', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('focuses the local textarea without changing a draft or sending a message', () => {
    const onSend = vi.fn();
    const view = render(<Composer capabilities={[]} onboardingFocusToken={1} onSend={onSend} />);
    const input = view.querySelector<HTMLTextAreaElement>('textarea');

    act(() => vi.advanceTimersByTime(1));
    expect(document.activeElement).toBe(input);

    act(() => changeValue(input!, 'Keep this draft'));
    expect(input?.value).toBe('Keep this draft');
    expect(onSend).not.toHaveBeenCalled();
    expect(view.firstElementChild?.className).toContain('composerOnboardingFocus');

    act(() => vi.advanceTimersByTime(2399));
    expect(view.firstElementChild?.className).not.toContain('composerOnboardingFocus');
  });

  it('does not replay a consumed token after remounting and accepts a newer token', () => {
    const onSend = vi.fn();
    const firstView = render(<Composer capabilities={[]} onboardingFocusToken={1} onSend={onSend} />);
    const firstInput = firstView.querySelector<HTMLTextAreaElement>('textarea');

    act(() => vi.advanceTimersByTime(1));
    expect(document.activeElement).toBe(firstInput);

    act(() => root?.render(<Composer capabilities={[]} onboardingFocusToken={0} onSend={onSend} />));
    act(() => root?.unmount());
    container?.remove();
    root = undefined;
    container = undefined;

    const remountedView = render(<Composer capabilities={[]} onboardingFocusToken={0} onSend={onSend} />);
    const remountedInput = remountedView.querySelector<HTMLTextAreaElement>('textarea');
    act(() => vi.advanceTimersByTime(1));
    expect(document.activeElement).not.toBe(remountedInput);

    act(() => root?.render(<Composer capabilities={[]} onboardingFocusToken={2} onSend={onSend} />));
    act(() => vi.advanceTimersByTime(1));
    expect(document.activeElement).toBe(remountedInput);
    expect(onSend).not.toHaveBeenCalled();
  });
});
