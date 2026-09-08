// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPilotAttachmentDragBinding } from './PilotAttachmentHandle';

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

describe('createPilotAttachmentDragBinding', () => {
  it('binds a card root to serialize its reference-only attachment for native drag', () => {
    const attachment = { kind: 'application' as const, id: '7', label: 'ByteDance · Backend' };
    const binding = createPilotAttachmentDragBinding(attachment);
    const view = render(<article {...binding} />);
    const cardRoot = view.querySelector<HTMLElement>('article')!;
    const setData = vi.fn();
    const dataTransfer = { setData, effectAllowed: 'none' };
    const dragStart = new Event('dragstart', { bubbles: true });
    Object.defineProperty(dragStart, 'dataTransfer', { value: dataTransfer });

    expect(cardRoot.draggable).toBe(true);
    expect(cardRoot.getAttribute('aria-label')).toContain(attachment.label);
    expect(view.querySelector('button')).toBeNull();
    expect(view.textContent).not.toContain('添加到 Pilot');

    act(() => cardRoot.dispatchEvent(dragStart));

    expect(setData).toHaveBeenCalledWith(
      'application/x-offerpilot-context-attachment',
      JSON.stringify(attachment),
    );
    expect(dataTransfer.effectAllowed).toBe('copy');
  });
});
