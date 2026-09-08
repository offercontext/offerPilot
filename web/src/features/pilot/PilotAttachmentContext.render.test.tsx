// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { PilotAttachmentProvider, usePilotAttachments } from './PilotAttachmentContext';

declare global {
  // React checks this flag before emitting act() environment warnings.
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const application = { kind: 'application' as const, id: '7', label: 'ByteDance · Backend' };

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

function AttachmentPanel({ keyName, renders }: { keyName: 'one' | 'two'; renders: number[] }) {
  const { attachments, addAttachment } = usePilotAttachments(`new:${keyName}`);
  renders.push(attachments.length);

  return (
    <button type="button" data-testid={keyName} onClick={() => addAttachment(application)}>
      {attachments.length}
    </button>
  );
}

function SendHarness({
  outcome,
}: {
  outcome: () => Promise<boolean>;
}) {
  const { activeKey, attachments, addAttachment, clearAttachmentsByKey } = usePilotAttachments('new:send');
  const send = async () => {
    const sendKey = activeKey;
    try {
      if (await outcome() && sendKey) clearAttachmentsByKey(sendKey);
    } catch {
      // An abort or transport error must retain the local draft.
    }
  };

  return (
    <>
      <button type="button" data-testid="attach" onClick={() => addAttachment(application)}>attach</button>
      <button type="button" data-testid="send" onClick={() => void send()}>send</button>
      <output data-testid="count">{attachments.length}</output>
    </>
  );
}

describe('PilotAttachmentProvider concurrent consumers', () => {
  it('keeps two simultaneously mounted attachment panels isolated without a render loop', () => {
    const firstRenders: number[] = [];
    const secondRenders: number[] = [];
    const view = render(
      <PilotAttachmentProvider>
        <AttachmentPanel keyName="one" renders={firstRenders} />
        <AttachmentPanel keyName="two" renders={secondRenders} />
      </PilotAttachmentProvider>,
    );

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="one"]')?.click());

    expect(view.querySelector('[data-testid="one"]')?.textContent).toBe('1');
    expect(view.querySelector('[data-testid="two"]')?.textContent).toBe('0');
    expect(firstRenders).toHaveLength(2);
    expect(secondRenders.length).toBeLessThanOrEqual(2);
  });

  it.each([
    ['successful send', async () => true, '0'],
    ['failed send', async () => false, '1'],
    ['aborted send', async () => { throw new DOMException('aborted', 'AbortError'); }, '1'],
  ])('clears a send draft only after a %s', async (_name, outcome, expectedCount) => {
    const view = render(
      <PilotAttachmentProvider>
        <SendHarness outcome={outcome} />
      </PilotAttachmentProvider>,
    );

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="attach"]')?.click());
    await act(async () => {
      view.querySelector<HTMLButtonElement>('[data-testid="send"]')?.click();
      await Promise.resolve();
    });

    expect(view.querySelector('[data-testid="count"]')?.textContent).toBe(expectedCount);
  });
});
