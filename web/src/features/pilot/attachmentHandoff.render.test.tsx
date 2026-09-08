// @vitest-environment jsdom
import { act, useEffect, useRef, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { retainPilotAttachmentKey } from './attachmentHandoff';
import {
  PilotAttachmentProvider,
  usePilotAttachments,
  type PilotAttachmentConversationKey,
} from './PilotAttachmentContext';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const application = { kind: 'application' as const, id: '7', label: 'ByteDance 路 Backend' };
const existingConversationKey = 'conversation:7' as PilotAttachmentConversationKey;

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

function PilotSurface({
  attachmentDraftKey,
  conversationKey,
  onAttachmentKeyChange,
  surface,
}: {
  attachmentDraftKey?: PilotAttachmentConversationKey;
  conversationKey?: PilotAttachmentConversationKey;
  onAttachmentKeyChange: (key?: PilotAttachmentConversationKey) => void;
  surface: 'rail' | 'drawer' | 'page';
}) {
  const {
    activeKey,
    addAttachment,
    attachments,
    ensureNewAttachmentDraft,
    setActiveConversationKey,
  } = usePilotAttachments();
  const [selectedConversationKey, setSelectedConversationKey] = useState(conversationKey);
  const handoffAttachmentKeyRef = useRef<PilotAttachmentConversationKey>();

  useEffect(() => {
    if (attachmentDraftKey) {
      handoffAttachmentKeyRef.current = attachmentDraftKey;
      setActiveConversationKey(attachmentDraftKey);
      return;
    }
    if (selectedConversationKey) {
      setActiveConversationKey(selectedConversationKey);
      return;
    }
    if (
      handoffAttachmentKeyRef.current !== undefined &&
      handoffAttachmentKeyRef.current === activeKey
    ) {
      return;
    }
    ensureNewAttachmentDraft();
  }, [
    activeKey,
    attachmentDraftKey,
    ensureNewAttachmentDraft,
    selectedConversationKey,
    setActiveConversationKey,
  ]);

  useEffect(() => {
    onAttachmentKeyChange(activeKey);
  }, [activeKey, onAttachmentKeyChange]);

  return (
    <section data-surface={surface}>
      <button type="button" data-testid="attach" onClick={() => addAttachment(application)}>attach</button>
      <button type="button" data-testid="select-conversation-8" onClick={() => setSelectedConversationKey('conversation:8')}>
        select conversation 8
      </button>
      <output data-testid="key">{activeKey}</output>
      <output data-testid="count">{attachments.length}</output>
    </section>
  );
}

function HandoffHarness({ reports }: { reports: string[] }) {
  const [surface, setSurface] = useState<'rail' | 'drawer' | 'page'>('rail');
  const [activeKey, setActiveKey] = useState<PilotAttachmentConversationKey>();
  const [pendingKey, setPendingKey] = useState<PilotAttachmentConversationKey>();
  const attachmentDraftKey = pendingKey;
  const syncAttachmentKey = (key?: PilotAttachmentConversationKey) => {
    reports.push(`${surface}:${key ?? 'undefined'}:${pendingKey ?? 'undefined'}`);
    setActiveKey((currentKey) => retainPilotAttachmentKey(currentKey, key));
    if (key) setPendingKey(undefined);
  };
  const handoffTo = (nextSurface: 'drawer' | 'page') => {
    setPendingKey(activeKey);
    setSurface(nextSurface);
  };

  return (
    <>
      {surface === 'rail' ? <PilotSurface
        key="rail"
        attachmentDraftKey={surface === 'rail' ? undefined : attachmentDraftKey}
        conversationKey={surface === 'rail' ? existingConversationKey : undefined}
        onAttachmentKeyChange={syncAttachmentKey}
        surface={surface}
      /> : surface === 'drawer' ? <PilotSurface
        key="drawer"
        attachmentDraftKey={attachmentDraftKey}
        onAttachmentKeyChange={syncAttachmentKey}
        surface="drawer"
      /> : <PilotSurface
        key="page"
        attachmentDraftKey={attachmentDraftKey}
        onAttachmentKeyChange={syncAttachmentKey}
        surface="page"
      />}
      <button type="button" data-testid="drawer" onClick={() => handoffTo('drawer')}>drawer</button>
      <button type="button" data-testid="page" onClick={() => handoffTo('page')}>page</button>
    </>
  );
}

describe('Pilot attachment surface handoff', () => {
  it('allocates a fresh draft before the first offer or resume attachment on an empty Pilot', () => {
    const reports: string[] = [];
    const view = render(
      <PilotAttachmentProvider>
        <PilotSurface onAttachmentKeyChange={(key) => reports.push(key ?? 'undefined')} surface="rail" />
      </PilotAttachmentProvider>,
    );

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="attach"]')?.click());

    expect(view.querySelector('[data-testid="key"]')?.textContent).toBe('new:draft-1');
    expect(view.querySelector('[data-testid="count"]')?.textContent).toBe('1');
    expect(reports).toContain('new:draft-1');
  });

  it('keeps an existing rail conversation attachment through drawer and page handoffs', () => {
    const reports: string[] = [];
    const view = render(
      <PilotAttachmentProvider>
        <HandoffHarness reports={reports} />
      </PilotAttachmentProvider>,
    );

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="attach"]')?.click());
    act(() => view.querySelector<HTMLButtonElement>('[data-testid="drawer"]')?.click());

    expect(view.querySelector('[data-surface="drawer"] [data-testid="key"]')?.textContent).toBe(existingConversationKey);
    expect(view.querySelector('[data-surface="drawer"] [data-testid="count"]')?.textContent).toBe('1');
    expect(reports).toContain('drawer:undefined:conversation:7');
    expect(reports).toContain('drawer:conversation:7:conversation:7');

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="page"]')?.click());

    expect(view.querySelector('[data-surface="page"] [data-testid="key"]')?.textContent).toBe(existingConversationKey);
    expect(view.querySelector('[data-surface="page"] [data-testid="count"]')?.textContent).toBe('1');

    act(() => view.querySelector<HTMLButtonElement>('[data-testid="select-conversation-8"]')?.click());

    expect(view.querySelector('[data-surface="page"] [data-testid="key"]')?.textContent).toBe('conversation:8');
    expect(view.querySelector('[data-surface="page"] [data-testid="count"]')?.textContent).toBe('0');
  });
});
