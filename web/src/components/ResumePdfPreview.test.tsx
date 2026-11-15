// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const pdf = vi.hoisted(() => ({ getDocument: vi.fn(), options: { workerSrc: '' } }));
vi.mock('pdfjs-dist', () => ({ getDocument: pdf.getDocument, GlobalWorkerOptions: pdf.options }));
import ResumePdfPreview from './ResumePdfPreview';
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement;
let status: ReturnType<typeof vi.fn>;
const pending = <T,>() => { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const blob = () => ({ arrayBuffer: vi.fn(async () => new ArrayBuffer(12)) }) as unknown as Blob;
const render = async (source: Blob) => { await act(async () => root.render(<ResumePdfPreview blob={source} onStatus={status} />)); };
function documentFixture() {
  const rendering = pending<void>();
  const task = { promise: rendering.promise, cancel: vi.fn() };
  const page = { getViewport: vi.fn(({ scale }: { scale: number }) => ({ width: 800 * scale, height: 1100 * scale })), render: vi.fn(() => task) };
  const document = { numPages: 400, getPage: vi.fn(async () => page) };
  const destroy = vi.fn(async () => {});
  const loading = { promise: Promise.resolve(document), destroy };
  return { rendering, task, page, document, destroy, loading };
}
beforeEach(() => {
  pdf.getDocument.mockReset(); status = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as CanvasRenderingContext2D);
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });
it('loads actual bytes with CSP-safe options and bounds rendering to the current page', async () => {
  const fixture = documentFixture(); pdf.getDocument.mockReturnValue(fixture.loading);
  await render(blob());
  expect(pdf.getDocument).toHaveBeenCalledWith(expect.objectContaining({ data: expect.any(Uint8Array), isEvalSupported: false, disableFontFace: true, useSystemFonts: false, useWorkerFetch: false, useWasm: false, enableXfa: false }));
  expect(pdf.options.workerSrc).toContain('pdf.worker.min.mjs');
  const options = pdf.getDocument.mock.calls[0][0];
  expect(options.url).toBeUndefined(); expect(options.cMapUrl).toBeUndefined(); expect(options.standardFontDataUrl).toBeUndefined();
  expect(fixture.document.getPage).toHaveBeenCalledTimes(1); expect(fixture.document.getPage).toHaveBeenCalledWith(1);
  expect(status).not.toHaveBeenCalledWith('ready');
  const canvas = host.querySelector('canvas')!; expect(canvas.width).toBeLessThanOrEqual(1600); expect(canvas.height).toBeLessThanOrEqual(2200);
  await act(async () => fixture.rendering.resolve()); expect(status).toHaveBeenLastCalledWith('ready');
  const next = Array.from(host.querySelectorAll('button')).find((b) => b.textContent === '下一页')!;
  await act(async () => next.click());
  expect(fixture.task.cancel).toHaveBeenCalled(); expect(fixture.document.getPage).toHaveBeenCalledWith(2);
  expect(fixture.document.getPage).toHaveBeenCalledTimes(2);
});
it('cancels rendering and destroys document/worker ownership on close, ignoring late completion', async () => {
  const fixture = documentFixture(); pdf.getDocument.mockReturnValue(fixture.loading); await render(blob());
  await act(async () => root.render(null));
  expect(fixture.task.cancel).toHaveBeenCalledOnce(); expect(fixture.destroy).toHaveBeenCalledOnce();
  await act(async () => fixture.rendering.resolve()); expect(status).not.toHaveBeenCalledWith('ready');
});
it('ignores old bytes and old rendering after changing the source', async () => {
  const bytes = pending<ArrayBuffer>();
  await render({ arrayBuffer: () => bytes.promise } as unknown as Blob);
  const fixture = documentFixture(); pdf.getDocument.mockReturnValue(fixture.loading);
  await render(blob());
  await act(async () => bytes.resolve(new ArrayBuffer(2)));
  expect(pdf.getDocument).toHaveBeenCalledTimes(1);
  const second = documentFixture(); pdf.getDocument.mockReturnValue(second.loading);
  await render(blob()); expect(fixture.destroy).toHaveBeenCalledOnce(); expect(fixture.task.cancel).toHaveBeenCalledOnce();
  await act(async () => fixture.rendering.resolve()); expect(status).not.toHaveBeenCalledWith('ready');
  await act(async () => second.rendering.resolve()); expect(status).toHaveBeenLastCalledWith('ready');
});
it('reports rendering failure without a successful review state', async () => {
  const fixture = documentFixture(); pdf.getDocument.mockReturnValue(fixture.loading); await render(blob());
  await act(async () => fixture.rendering.reject(new Error('invalid graphics')));
  expect(status).toHaveBeenLastCalledWith('error'); expect(status).not.toHaveBeenCalledWith('ready');
  expect(host.textContent).toContain('PDF 预览渲染失败');
});
it('destroys loading ownership even before document loading completes', async () => {
  const loaded = pending<unknown>(); const destroy = vi.fn(async () => {});
  pdf.getDocument.mockReturnValue({ promise: loaded.promise, destroy }); await render(blob());
  await act(async () => root.render(null)); expect(destroy).toHaveBeenCalledOnce();
  await act(async () => loaded.resolve({ numPages: 1 })); expect(status).not.toHaveBeenCalledWith('ready');
});
