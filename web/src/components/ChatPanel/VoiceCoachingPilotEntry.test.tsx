// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isVoiceCoachingPilotIntent, VoiceCoachingPilotEntry } from './index';

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

describe('Voice Coaching Pilot entry', () => {
  it('recognizes only explicit local navigation intents', () => {
    expect(isVoiceCoachingPilotIntent('查看表达成长')).toBe(true);
    expect(isVoiceCoachingPilotIntent('  查看我的表达成长  ')).toBe(true);
    expect(isVoiceCoachingPilotIntent('帮我评价面试能力')).toBe(false);
  });

  it('navigates only after the user explicitly clicks the local entry', async () => {
    const open = vi.fn();
    await act(async () => root.render(<VoiceCoachingPilotEntry onOpen={open} />));
    expect(open).not.toHaveBeenCalled();
    const button = host.querySelector<HTMLButtonElement>('[data-testid="pilot-open-voice-coaching-growth"]');
    act(() => button?.click());
    expect(open).toHaveBeenCalledOnce();
  });
});
