// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ContextPanel from './ContextPanel';
import { isInterviewStoryPilotIntent } from './index';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: () => ({ matches: false, addListener: () => undefined, removeListener: () => undefined }),
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
});

describe('Interview Story Pilot entry', () => {
  it('recognizes only the two explicit local Story intents', () => {
    expect(isInterviewStoryPilotIntent('整理面试故事')).toBe(true);
    expect(isInterviewStoryPilotIntent('  帮我整理一个面试故事  ')).toBe(true);
    expect(isInterviewStoryPilotIntent('帮我看看今天的面试安排')).toBe(false);
  });

  it('opens the local Story workflow only after an explicit click', () => {
    const open = vi.fn();
    act(() => root?.render(<ContextPanel
      isNego={false}
      offer={null}
      capabilities={[]}
      evidence={[]}
      autoApprove={false}
      hasKey
      degraded={false}
      disabled={false}
      onCapability={() => {}}
      onToggleAutoApprove={() => {}}
      onOpenInterviewStoryLibrary={open}
    />));

    expect(open).not.toHaveBeenCalled();
    const trigger = container?.querySelector('[data-testid="pilot-open-interview-story-library"]') as HTMLButtonElement;
    act(() => trigger.click());
    expect(open).toHaveBeenCalledTimes(1);
  });
});
