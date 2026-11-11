import { useLayoutEffect, useRef, useState } from 'react';
import { Alert, Button, Space } from 'antd';
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { t, useLocale } from '@/i18n';

// Both module and worker are bundled locally. Desktop explicitly forbids frames.
export type PdfPreviewStatus = 'loading' | 'ready' | 'error';
interface Props {
  blob: Blob;
  onStatus: (status: PdfPreviewStatus) => void;
}

export default function ResumePdfPreview({ blob, onStatus }: Props) {
  useLocale();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [rendering, setRendering] = useState(true);
  const [failed, setFailed] = useState(false);
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;

  useLayoutEffect(() => {
    let current = true;
    let loading: PDFDocumentLoadingTask | undefined;
    setDocument(null); setPageNumber(1); setFailed(false); setRendering(true);
    onStatusRef.current('loading');
    void Promise.all([import('pdfjs-dist'), blob.arrayBuffer()]).then(async ([pdfjs, buffer]) => {
      if (!current) return;
      pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
      loading = pdfjs.getDocument({
        data: new Uint8Array(buffer),
        isEvalSupported: false,
        disableFontFace: true,
        useSystemFonts: false,
        useWorkerFetch: false,
        useWasm: false,
        enableXfa: false,
        isOffscreenCanvasSupported: false,
        isImageDecoderSupported: false,
        maxImageSize: 16_000_000,
      });
      const loaded = await loading.promise;
      if (!current) return;
      if (loaded.numPages < 1) throw new Error('empty PDF');
      setDocument(loaded);
    }).catch(() => {
      if (current) { setFailed(true); setRendering(false); onStatusRef.current('error'); }
    });
    return () => {
      current = false;
      // PDFDocumentLoadingTask owns its document transport and dedicated worker.
      if (loading) void loading.destroy().catch(() => { /* Already terminated. */ });
    };
  }, [blob]);

  useLayoutEffect(() => {
    if (!document || !canvas.current) return;
    let current = true;
    let task: RenderTask | undefined;
    const target = canvas.current;
    setRendering(true); setFailed(false); onStatusRef.current('loading');
    void document.getPage(pageNumber).then(async (page) => {
      if (!current) return;
      const natural = page.getViewport({ scale: 1 });
      // One bounded page, never a canvas for every page in a long document.
      const scale = Math.min(1.5, 1600 / natural.width, 2200 / natural.height);
      const viewport = page.getViewport({ scale });
      target.width = Math.ceil(viewport.width);
      target.height = Math.ceil(viewport.height);
      const context = target.getContext('2d');
      if (!context) throw new Error('canvas unavailable');
      task = page.render({ canvas: target, canvasContext: context, viewport });
      await task.promise;
      if (current) { setRendering(false); onStatusRef.current('ready'); }
    }).catch(() => {
      if (current) { setFailed(true); setRendering(false); onStatusRef.current('error'); }
    });
    return () => {
      current = false;
      task?.cancel();
      target.width = 0; target.height = 0;
    };
  }, [document, pageNumber]);

  const changePage = (next: number) => {
    onStatusRef.current('loading'); setRendering(true); setPageNumber(next);
  };
  return <section aria-label={t('已保存简历 PDF 预览')} aria-busy={rendering}>
    <Space wrap style={{ marginBottom: 12 }}>
      <Button disabled={!document || rendering || failed || pageNumber <= 1} onClick={() => changePage(pageNumber - 1)}>{t('上一页')}</Button>
      <span>{t('第 {page} 页，共 {total} 页', { page: pageNumber, total: document?.numPages ?? 0 })}</span>
      <Button disabled={!document || rendering || failed || pageNumber >= document.numPages} onClick={() => changePage(pageNumber + 1)}>{t('下一页')}</Button>
    </Space>
    {rendering && <p role="status">{t('正在渲染 PDF 预览…')}</p>}
    {failed && <Alert type="error" message={t('PDF 预览渲染失败，请重新预览后重试。')} />}
    <div style={{ maxHeight: '55vh', overflow: 'auto', border: '1px solid #ddd' }}>
      <canvas ref={canvas} role="img" aria-label={t('简历 PDF 第 {page} 页', { page: pageNumber })} style={{ display: rendering || failed ? 'none' : 'block', width: '100%', height: 'auto' }} />
    </div>
  </section>;
}
