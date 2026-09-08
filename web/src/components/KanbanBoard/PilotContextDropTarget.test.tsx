// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import PilotContextDropTarget from './PilotContextDropTarget';

vi.mock('@dnd-kit/core', () => ({
  useDroppable: () => ({ isOver: false, setNodeRef: () => undefined }),
}));

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

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = undefined;
  container = undefined;
});

describe('PilotContextDropTarget', () => {
  it('accepts a native offer or resume drop across the same full target used by Kanban', () => {
    const onNativeDrop = vi.fn();
    const view = render(
      <PilotContextDropTarget onNativeDrop={onNativeDrop}>
        <div>Visible Pilot</div>
      </PilotContextDropTarget>,
    );
    const target = view.querySelector<HTMLElement>('[data-testid="pilot-native-drop-surface"]')!;
    const event = new Event('drop', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'dataTransfer', {
      value: {
        types: ['application/x-offerpilot-context-attachment'],
        getData: () => JSON.stringify({ kind: 'offer', id: '5', label: 'Acme offer' }),
      },
    });

    act(() => target.dispatchEvent(event));

    expect(event.defaultPrevented).toBe(true);
    expect(onNativeDrop).toHaveBeenCalledWith({ kind: 'offer', id: '5', label: 'Acme offer' });
  });
});
