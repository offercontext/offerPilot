// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EvidenceList from './EvidenceList';
import type { EvidenceItem, EvidenceTarget } from './model';

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

describe('EvidenceList', () => {
  it('opens a target-bearing application evidence item with its original target', () => {
    const target: EvidenceTarget = { kind: 'application', id: 7 };
    const onOpenEvidence = vi.fn();
    const item: EvidenceItem = {
      id: 'application-7',
      kind: 'application',
      target,
      title: '字节跳动',
      meta: '后端工程师',
      source: 'list_applications',
    };

    const view = render(<EvidenceList items={[item]} onOpenEvidence={onOpenEvidence} />);
    const button = view.querySelector<HTMLButtonElement>('[aria-label="打开投递：字节跳动"]');

    expect(button).toBeInstanceOf(HTMLButtonElement);
    act(() => button?.click());
    expect(onOpenEvidence).toHaveBeenCalledWith(target);
    expect(onOpenEvidence.mock.calls[0]?.[0]).toBe(target);
  });

  it('keeps targetless knowledge evidence readable without an open button', () => {
    const item: EvidenceItem = {
      id: 'knowledge-1',
      kind: 'knowledge',
      title: '面试准备清单',
      snippet: '复习项目经历。',
      source: 'search_knowledge',
    };

    const view = render(<EvidenceList items={[item]} onOpenEvidence={vi.fn()} />);

    expect(view.textContent).toContain('面试准备清单');
    expect(view.textContent).toContain('复习项目经历。');
    expect(view.querySelector('button[aria-label^="打开"]')).toBeNull();
  });

  it('keeps target-bearing evidence non-actionable when the callback is omitted', () => {
    const item: EvidenceItem = {
      id: 'application-7',
      kind: 'application',
      target: { kind: 'application', id: 7 },
      title: '字节跳动',
      source: 'list_applications',
    };

    const view = render(<EvidenceList items={[item]} />);

    expect(view.textContent).toContain('字节跳动');
    expect(view.querySelector('button[aria-label^="打开"]')).toBeNull();
  });
});
