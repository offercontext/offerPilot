// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CommandPalette from './CommandPalette';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
let container: HTMLDivElement | undefined;
const scrollIntoView = vi.fn();

beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => getComputedStyle(element));
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    value: scrollIntoView,
  });
  scrollIntoView.mockReset();
  window.matchMedia = vi.fn().mockReturnValue({
    matches: false,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  document.querySelectorAll('.ant-modal-root').forEach((node) => node.remove());
  vi.restoreAllMocks();
});

describe('CommandPalette accessibility', () => {
  it('announces the active option while keyboard navigation moves through results', () => {
    act(() => root?.render(
      <CommandPalette
        open
        applications={[]}
        pipelineActions={[]}
        onClose={vi.fn()}
        onNavigate={vi.fn()}
        onOpenDetail={vi.fn()}
        onAddApplication={vi.fn()}
        onOpenResume={vi.fn()}
        onOpenChat={vi.fn()}
        onOpenSettings={vi.fn()}
        onRunPipelineAction={vi.fn()}
      />,
    ));

    const input = document.querySelector('[role="combobox"]') as HTMLInputElement;
    const options = [...document.querySelectorAll('[role="option"]')];
    expect(input.getAttribute('aria-controls')).toBe('command-palette-listbox');
    expect(options.length).toBeGreaterThan(1);
    expect(options[0].getAttribute('aria-selected')).toBe('true');
    expect(input.getAttribute('aria-activedescendant')).toBe(options[0].id);

    act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })));
    expect(options[0].getAttribute('aria-selected')).toBe('false');
    expect(options[1].getAttribute('aria-selected')).toBe('true');
    expect(input.getAttribute('aria-activedescendant')).toBe(options[1].id);
    expect(scrollIntoView).toHaveBeenLastCalledWith({ block: 'nearest' });

    for (let index = 0; index < 8; index += 1) {
      act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })));
    }
    expect(document.querySelector('[role="option"][aria-selected="true"]')?.id).toBe(options[9].id);
    expect(scrollIntoView).toHaveBeenLastCalledWith({ block: 'nearest' });
  });
});
