import { t } from '@/i18n';
// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConfirmationPanel } from './ConfirmationPanel';

let root: Root | null = null;
let container: HTMLDivElement | null = null;

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  container?.remove();
  container = null;
});

describe('ConfirmationPanel', () => {
  it('renders sources and delegates the child action without owning a mutation', () => {
    const onConfirm = vi.fn();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root?.render(
      <ConfirmationPanel
        title={t("确认生成面试建议")}
        description={t("仅使用以下已选择内容。")}
        sources={[{ state: 'frozen', detail: t("已确认岗位描述") }]}
      >
        <button type="button" onClick={onConfirm}>{t("确认并生成")}</button>
      </ConfirmationPanel>,
    ));

    expect(container.textContent).toContain(t("确认生成面试建议"));
    expect(container.textContent).toContain(t("已保留当时版本"));
    expect(container.textContent).toContain(t("已确认岗位描述"));
    const button = container.querySelector('button');
    expect(button).not.toBeNull();
    act(() => button?.click());
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
