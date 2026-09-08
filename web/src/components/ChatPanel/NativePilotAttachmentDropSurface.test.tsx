// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import NativePilotAttachmentDropSurface from './NativePilotAttachmentDropSurface';

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

function drop(target: Element, payload: unknown) {
  const event = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', {
    value: {
      types: ['application/x-offerpilot-context-attachment'],
      getData: () => JSON.stringify(payload),
    },
  });
  act(() => target.dispatchEvent(event));
  return event;
}

function dragOver(target: Element, payload: unknown) {
  const event = new Event('dragover', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', {
    value: {
      types: ['application/x-offerpilot-context-attachment'],
      getData: () => JSON.stringify(payload),
    },
  });
  act(() => target.dispatchEvent(event));
  return event;
}

function dragEnter(target: Element, payload: string) {
  const event = new Event('dragenter', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', {
    value: {
      types: ['application/x-offerpilot-context-attachment'],
      getData: () => payload,
    },
  });
  act(() => target.dispatchEvent(event));
}

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = undefined;
  container = undefined;
});

describe('NativePilotAttachmentDropSurface', () => {
  it('accepts a valid offer or resume reference anywhere in the visible Pilot surface', () => {
    const onNativeDrop = vi.fn();
    const view = render(
      <NativePilotAttachmentDropSurface onNativeDrop={onNativeDrop}>
        <div data-testid="pilot-body">Pilot body</div>
      </NativePilotAttachmentDropSurface>,
    );
    const surface = view.querySelector('[data-testid="pilot-native-drop-surface"]')!;

    const event = drop(surface, { kind: 'resume', id: '3', label: 'Backend resume' });

    expect(event.defaultPrevented).toBe(true);
    expect(onNativeDrop).toHaveBeenCalledWith({ kind: 'resume', id: '3', label: 'Backend resume' });
  });

  it('uses the same Chinese full-surface drop affordance for native card drags', () => {
    const view = render(
      <NativePilotAttachmentDropSurface onNativeDrop={vi.fn()}>
        <div>Visible Pilot</div>
      </NativePilotAttachmentDropSurface>,
    );
    const surface = view.querySelector<HTMLElement>('[data-testid="pilot-native-drop-surface"]')!;

    const event = dragOver(surface, { kind: 'offer', id: '8', label: 'Acme offer' });

    expect(event.defaultPrevented).toBe(true);
    expect(surface.dataset.dragging).toBe('true');
    expect(surface.textContent).toContain('松开以加入 Pilot 上下文');
  });

  it('does not show the full-surface affordance for an observable malformed native payload', () => {
    const view = render(
      <NativePilotAttachmentDropSurface onNativeDrop={vi.fn()}>
        <div>Visible Pilot</div>
      </NativePilotAttachmentDropSurface>,
    );
    const surface = view.querySelector<HTMLElement>('[data-testid="pilot-native-drop-surface"]')!;

    dragEnter(surface, '{oops');

    expect(surface.dataset.dragging).toBeUndefined();
    expect(surface.textContent).not.toContain('松开以加入 Pilot 上下文');
  });

  it('does not accept a native attachment while the composer is disabled', () => {
    const onNativeDrop = vi.fn();
    const view = render(
      <NativePilotAttachmentDropSurface disabled onNativeDrop={onNativeDrop}>
        <div>Disabled Pilot</div>
      </NativePilotAttachmentDropSurface>,
    );

    const event = drop(view.querySelector('[data-testid="pilot-native-drop-surface"]')!, {
      kind: 'offer', id: '8', label: 'Acme offer',
    });

    expect(event.defaultPrevented).toBe(false);
    expect(onNativeDrop).not.toHaveBeenCalled();
  });
});
