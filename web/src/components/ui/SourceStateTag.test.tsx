import { t } from '@/i18n';
// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { SourceStateTag } from './SourceStateTag';

let root: Root | null = null;
let container: HTMLDivElement | null = null;

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  container?.remove();
  container = null;
});

function render(state: 'current' | 'frozen' | 'changed' | 'unknown' | 'pending') {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root?.render(<SourceStateTag state={state} detail={t("简历版本已变化")} />));
  return container;
}

describe('SourceStateTag', () => {
  it.each([
    ['current', t("当前使用来源")],
    ['frozen', t("已保留当时版本")],
    ['changed', t("来源已更新，已有结果仍保留")],
    ['unknown', t("来源暂不可确认")],
    ['pending', t("待确认的证据预览")],
  ] as const)('renders the fixed Chinese label for %s', (state, label) => {
    const view = render(state);
    expect(view.textContent).toContain(label);
    expect(view.textContent).toContain(t("简历版本已变化"));
  });
});
