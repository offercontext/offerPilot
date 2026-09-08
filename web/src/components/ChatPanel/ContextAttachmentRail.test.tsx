// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ContextAttachmentRail from './ContextAttachmentRail';

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

function nativeDrop(
  target: Element,
  type: string,
  value: string,
): Event {
  const event = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', {
    value: { types: [type], getData: () => value },
  });
  act(() => target.dispatchEvent(event));
  return event;
}

function nativeDragOver(target: Element, type: string, value: string): Event {
  const event = new Event('dragover', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', {
    value: { types: [type], getData: () => value },
  });
  act(() => target.dispatchEvent(event));
  return event;
}

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = undefined;
  container = undefined;
});

describe('ContextAttachmentRail', () => {
  it('shows attachment chips with distinct accessible removal controls', () => {
    const onRemove = vi.fn();
    const view = render(
      <ContextAttachmentRail
        attachments={[
          { kind: 'application', id: '12', label: 'ByteDance - Backend' },
          { kind: 'resume', id: 'primary', label: 'Primary resume' },
        ]}
        onRemove={onRemove}
      />,
    );

    expect(view.textContent).toContain('ByteDance - Backend');
    expect(view.textContent).toContain('Primary resume');
    const applicationRemove = view.querySelector<HTMLButtonElement>(
      '[aria-label="Remove ByteDance - Backend from context"]',
    );
    const resumeRemove = view.querySelector<HTMLButtonElement>(
      '[aria-label="Remove Primary resume from context"]',
    );
    expect(applicationRemove).not.toBeNull();
    expect(resumeRemove).not.toBeNull();

    act(() => applicationRemove?.click());
    expect(onRemove).toHaveBeenCalledWith({
      kind: 'application',
      id: '12',
      label: 'ByteDance - Backend',
    });
  });

  it('accepts only valid OfferPilot attachment drops', () => {
    const onNativeDrop = vi.fn();
    const view = render(<ContextAttachmentRail attachments={[]} onRemove={vi.fn()} onNativeDrop={onNativeDrop} />);
    const rail = view.querySelector('[data-testid="context-attachment-rail"]')!;

    const protectedDrag = nativeDragOver(rail, 'application/x-offerpilot-context-attachment', '');
    expect(protectedDrag.defaultPrevented).toBe(true);
    expect(rail.className).toContain('contextAttachmentRailDragging');

    const malformedDrag = nativeDragOver(
      rail,
      'application/x-offerpilot-context-attachment',
      '{oops',
    );
    expect(malformedDrag.defaultPrevented).toBe(false);
    expect(rail.className).not.toContain('contextAttachmentRailDragging');

    const malformed = nativeDrop(rail, 'application/x-offerpilot-context-attachment', '{oops');
    expect(malformed.defaultPrevented).toBe(false);
    expect(onNativeDrop).not.toHaveBeenCalled();
    expect(rail.className).not.toContain('contextAttachmentRailDragging');

    const secondProtectedDrag = nativeDragOver(rail, 'application/x-offerpilot-context-attachment', '');
    expect(secondProtectedDrag.defaultPrevented).toBe(true);

    const valid = nativeDrop(
      rail,
      'application/x-offerpilot-context-attachment',
      JSON.stringify({ kind: 'offer', id: '7', label: 'Acme offer' }),
    );
    expect(valid.defaultPrevented).toBe(true);
    expect(onNativeDrop).toHaveBeenCalledWith({ kind: 'offer', id: '7', label: 'Acme offer' });

    const unrelated = nativeDrop(rail, 'text/plain', JSON.stringify({ kind: 'offer', id: '7', label: 'Acme offer' }));
    const incomplete = nativeDrop(
      rail,
      'application/x-offerpilot-context-attachment',
      JSON.stringify({ kind: 'offer', id: '', label: 'Acme offer' }),
    );
    expect(unrelated.defaultPrevented).toBe(false);
    expect(incomplete.defaultPrevented).toBe(false);
    expect(onNativeDrop).toHaveBeenCalledTimes(1);
    expect(rail.className).not.toContain('contextAttachmentRailDragging');

    const unrelatedDrag = nativeDragOver(rail, 'text/plain', 'not an attachment');
    expect(unrelatedDrag.defaultPrevented).toBe(false);
  });
});
