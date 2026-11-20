// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import RealtimeVoicePanel from './RealtimeVoicePanel';
import { initialRealtimeState } from '@/features/mockInterviewVoice/realtimeVoiceController';
import { setLocale } from '@/i18n';
afterEach(() => setLocale('zh-CN'));
vi.mock('@/services/interviewVoice', () => ({
  voiceCapabilities: vi.fn().mockResolvedValue({ available: true, consent_revision: 'v1' }),
}));
it('requires per-connection consent and resets opt-in narration after a pause', async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  const props = { disabled: false, onStart: vi.fn(), onPause: vi.fn(), onStop: vi.fn(),
    onRepeat: vi.fn(), onManual: vi.fn(), onDiscard: vi.fn() };
  const render = async (locked: boolean) => act(async () => {
    root.render(<RealtimeVoicePanel {...props} state={{ ...initialRealtimeState, locked, status: locked ? 'listening' : 'paused' }} />);
  });
  try {
    await render(false);
    const start = () => Array.from(host.querySelectorAll('button')).find(b => b.textContent === '开启实时对话')!;
    const checkboxes = () => Array.from(host.querySelectorAll<HTMLInputElement>('input[type=checkbox]'));
    expect(start().disabled).toBe(true);
    expect(checkboxes()[1].checked).toBe(false);
    await act(async () => checkboxes()[1].click());
    expect(start().disabled).toBe(true);
    await act(async () => checkboxes()[0].click());
    expect(start().disabled).toBe(false);
    await act(async () => start().click());
    expect(props.onStart).toHaveBeenCalledWith('v1', false, true);
    await render(true);
    await render(false);
    expect(start().disabled).toBe(true);
    expect(checkboxes()[1].checked).toBe(false);
  } finally { await act(async () => root.unmount()); host.remove(); }
});
it('switches authored status and consent copy without translating transcripts or starting media', async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  const props = { disabled: false, onStart: vi.fn(), onPause: vi.fn(), onStop: vi.fn(),
    onRepeat: vi.fn(), onManual: vi.fn(), onDiscard: vi.fn() };
  try {
    await act(async () => {
      root.render(<RealtimeVoicePanel {...props} state={{ ...initialRealtimeState,
        status: 'manual', transcript: '正在听取回答。', message: '已暂停。重新开启需要再次授权本场自动记录。',
        revocationPending: true }} />);
    });
    await act(async () => setLocale('en-US'));
    expect(host.querySelector('strong')?.textContent).toBe('Realtime conversation · Experimental');
    expect(host.querySelector('[role=status]')?.textContent).toContain('Revocation is not confirmed');
    expect(host.textContent).toContain('Allow automatic answer recording for this connection');
    const transfer = Array.from(host.querySelectorAll('button')).find(b => b.textContent === 'Copy to the answer field for review')!;
    await act(async () => transfer.click());
    expect(props.onManual).toHaveBeenCalledWith('正在听取回答。');
    expect(props.onStart).not.toHaveBeenCalled();
    await act(async () => setLocale('zh-CN'));
    expect(host.querySelector('[role=status]')?.textContent).toContain('授权撤销尚未确认');
  } finally { await act(async () => root.unmount()); host.remove(); }
});
