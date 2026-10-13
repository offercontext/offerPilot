// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import RealtimeVoicePanel from './RealtimeVoicePanel';
import { initialRealtimeState } from '@/features/mockInterviewVoice/realtimeVoiceController';
vi.mock('@/services/interviewVoice', () => ({
  voiceCapabilities: vi.fn().mockResolvedValue({ available: true, consent_revision: 'v1' }),
}));
it('requires explicit per-connection consent and clears consent after a pause', async () => {
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
    expect(start().disabled).toBe(true);
    await act(async () => { (host.querySelector('input[type=checkbox]') as HTMLInputElement).click(); });
    expect(start().disabled).toBe(false);
    await act(async () => start().click());
    expect(props.onStart).toHaveBeenCalledWith('v1', false);
    await render(true);
    await render(false);
    expect(start().disabled).toBe(true);
  } finally { await act(async () => root.unmount()); host.remove(); }
});
