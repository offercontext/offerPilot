// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Resume } from '@/types/resume';
import { setLocale } from '@/i18n';
const api = vi.hoisted(() => ({ preview: vi.fn(), download: vi.fn(), save: vi.fn(), renderStatus: 'ready' as 'ready' | 'loading' | 'error', status: (_status: 'ready' | 'loading' | 'error') => {} }));
vi.mock('./ResumePdfPreview', async () => {
  const { useLayoutEffect } = await import('react');
  return { default: ({ onStatus }: { onStatus: typeof api.status }) => {
    api.status = onStatus;
    useLayoutEffect(() => { onStatus(api.renderStatus); }, [onStatus]);
    return <canvas aria-label="PDF canvas" />;
  } };
});
vi.mock('@/services/resumeExport', async (original) => ({ ...await original<object>(), previewResumeExport: api.preview, downloadResumeExport: api.download, saveExportBlob: api.save }));
import { ResumeExportError } from '@/services/resumeExport';
import ResumeExportModal from './ResumeExportModal';
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement;
const resume = { id: 7, title: 'Saved title' } as Resume;
const button = (text: string) => Array.from(document.querySelectorAll('button')).find((node) => node.textContent?.replace(/\s/g, '') === text.replace(/\s/g, ''))!;
const click = async (text: string) => { await act(async () => button(text).click()); };
const render = async (blocked = false, value = resume) => { await act(async () => root.render(<ResumeExportModal resume={value} blocked={blocked} onClose={() => root.render(null)} />)); };
beforeEach(async () => {
  api.renderStatus = 'ready';
  setLocale('zh-CN'); api.preview.mockReset(); api.download.mockReset(); api.save.mockReset();
  api.preview.mockResolvedValue({ blob: new Blob(['pdf']), fingerprint: 'snapshot', title: 'Server renamed title' });
  URL.createObjectURL = vi.fn(() => 'blob:preview'); URL.revokeObjectURL = vi.fn();
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }) });
  host = document.createElement('div'); document.body.append(host); root = createRoot(host); await render();
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
it('requires preview and review, then downloads the reviewed server snapshot', async () => {
  expect(button('下载 PDF').disabled).toBe(true); await click('生成 PDF 预览');
  expect(document.querySelector('canvas')).not.toBeNull();
  expect(document.querySelector('iframe, object')).toBeNull();
  expect(document.body.textContent).toContain('Server renamed title');
  expect(document.body.textContent).not.toContain('Saved title');
  expect(button('下载 PDF').disabled).toBe(true);
  await act(async () => (document.querySelector('input[type=checkbox]') as HTMLInputElement).click());
  api.download.mockResolvedValue({ blob: new Blob(['docx']), filename: 'safe.docx' });
  await click('下载 DOCX'); expect(api.download).toHaveBeenCalledWith(7, 'zh-CN', 'docx', 'snapshot', expect.any(AbortSignal));
  expect(api.save).toHaveBeenCalledOnce();
});
it('blocks dirty/saving content and invalidates preview on locale or resume change', async () => {
  await render(true); await click('生成 PDF 预览'); expect(api.preview).not.toHaveBeenCalled();
  expect(document.body.textContent).toContain('请先保存当前编辑');
  await render(); await click('生成 PDF 预览');
  await act(async () => { setLocale('en-US'); });
  expect(document.querySelector('canvas')).toBeNull();
  expect(button('Download PDF').disabled).toBe(true);
  await render(false, { ...resume, id: 8 }); expect(document.querySelector('canvas')).toBeNull();
});
it('invalidates stale source and requires a new preview, not another old download', async () => {
  await click('生成 PDF 预览'); await act(async () => (document.querySelector('input[type=checkbox]') as HTMLInputElement).click());
  api.download.mockRejectedValue(new ResumeExportError('stale'));
  await click('下载 PDF'); expect(document.querySelector('canvas')).toBeNull(); expect(button('下载 DOCX').disabled).toBe(true);
  expect(document.body.textContent).toContain('已保存简历已变化'); expect(api.save).not.toHaveBeenCalled();
});
it('ignores late previews after closing and blocks repeated clicks', async () => {
  let resolve!: (value: unknown) => void; api.preview.mockReturnValue(new Promise((done) => { resolve = done; }));
  await click('生成 PDF 预览'); await click('生成 PDF 预览'); expect(api.preview).toHaveBeenCalledOnce();
  await act(async () => root.render(null));
  await act(async () => resolve({ blob: new Blob(['old']), fingerprint: 'old' }));
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});
it('does not save late download results after switching the resume', async () => {
  await click('生成 PDF 预览'); await act(async () => (document.querySelector('input[type=checkbox]') as HTMLInputElement).click());
  let resolve!: (value: unknown) => void; api.download.mockReturnValue(new Promise((done) => { resolve = done; }));
  await click('下载 PDF'); await render(false, { ...resume, id: 8 });
  await act(async () => resolve({ blob: new Blob(['old']), filename: 'old.pdf' })); expect(api.save).not.toHaveBeenCalled();
});
it('keeps a reopened preview pending when the old closed request resolves', async () => {
  let resolveOld!: (value: unknown) => void;
  let resolveNew!: (value: unknown) => void;
  api.preview.mockReturnValueOnce(new Promise((done) => { resolveOld = done; })).mockReturnValueOnce(new Promise((done) => { resolveNew = done; }));
  await click('生成 PDF 预览');
  await act(async () => root.render(null)); await render(); await click('生成 PDF 预览');
  await act(async () => resolveOld({ blob: new Blob(['old']), fingerprint: 'old' }));
  expect(button('生成 PDF 预览').disabled).toBe(true); expect(URL.createObjectURL).not.toHaveBeenCalled();
  await act(async () => resolveNew({ blob: new Blob(['new']), fingerprint: 'new' }));
  expect(document.querySelector('canvas')).not.toBeNull();
});

it('does not allow review or download before rendering and invalidates failed rendering', async () => {
  api.renderStatus = 'loading';
  await click('生成 PDF 预览');
  const checkbox = document.querySelector('input[type=checkbox]') as HTMLInputElement;
  expect(checkbox.disabled).toBe(true); expect(button('下载 PDF').disabled).toBe(true);
  await act(async () => api.status('ready'));
  await act(async () => checkbox.click());
  expect(button('下载 PDF').disabled).toBe(false);
  await act(async () => api.status('loading'));
  expect(checkbox.checked).toBe(false); expect(checkbox.disabled).toBe(true);
  await act(async () => api.status('error'));
  expect(document.querySelector('canvas')).toBeNull(); expect(button('下载 DOCX').disabled).toBe(true);
  expect(document.body.textContent).toContain('PDF 预览渲染失败'); expect(api.download).not.toHaveBeenCalled();
});
