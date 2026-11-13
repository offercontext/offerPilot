import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import ResumePdfPreview, { type PdfPreviewStatus } from './ResumePdfPreview';
import { Alert, Button, Checkbox, Modal, Space } from 'antd';
import { t, useLocale } from '@/i18n';
import type { Resume } from '@/types/resume';
import { downloadResumeExport, previewResumeExport, ResumeExportError, saveExportBlob, type ExportFormat } from '@/services/resumeExport';

export default function ResumeExportModal({ resume, blocked, onClose }: { resume: Resume; blocked: boolean; onClose: () => void }) {
  const locale = useLocale();
  const [preview, setPreview] = useState<{ blob: Blob; fingerprint: string; title: string } | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [previewReady, setPreviewReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  const invalidate = () => {
    epoch.current += 1; request.current?.abort(); request.current = null;

  };
  // Invalidate at commit, before any late promise can expose another resume or locale.
  useLayoutEffect(() => {
    invalidate(); setPreview(null); setReviewed(false); setPreviewReady(false); setBusy(false); setError('');
    return invalidate;
  }, [resume, locale, blocked]);
  const fail = (cause: unknown) => {
    invalidate(); setPreview(null); setReviewed(false); setPreviewReady(false);
    const errors = {
      stale: '已保存简历已变化，请重新预览并核对。',
      invalid: '没有可导出的有效结构化内容，请补充并保存后重试。',
      auth: '认证已失效，请重新登录后预览。',
      unsupported: '简历包含暂不支持导出的字段，请检查高级 JSON 后重试。',
      too_large: '简历内容过长，请精简并保存后重试。',
      unsupported_text: '简历包含字体无法显示的字符，请调整并保存后重试。',
      failed: '导出失败，请重新预览后重试。',
    };
    setError(errors[cause instanceof ResumeExportError ? cause.kind : 'failed']);
  };
  const generate = async () => {
    if (blocked || request.current) return;
    invalidate(); setPreview(null); setReviewed(false); setPreviewReady(false); setError(''); setBusy(true);
    const current = epoch.current;
    const controller = new AbortController(); request.current = controller;
    try {
      const result = await previewResumeExport(resume.id, locale, controller.signal);
      if (current !== epoch.current) return;
      setPreview({ blob: result.blob, fingerprint: result.fingerprint, title: result.title! });
    } catch (cause) { if (current === epoch.current) fail(cause); }
    finally { if (current === epoch.current || request.current === null) { request.current = null; setBusy(false); } }
  };
  const download = async (format: ExportFormat) => {
    if (blocked || request.current || !preview || !previewReady || !reviewed) return;
    const current = epoch.current;
    const controller = new AbortController(); request.current = controller; setBusy(true); setError('');
    try {
      const result = await downloadResumeExport(resume.id, locale, format, preview.fingerprint, controller.signal);
      if (current !== epoch.current) return;
      saveExportBlob(result.blob, result.filename);
    } catch (cause) { if (current === epoch.current) fail(cause); }
    finally { if (current === epoch.current || request.current === null) { request.current = null; setBusy(false); } }
  };
  const previewStatus = useCallback((status: PdfPreviewStatus) => {
    setPreviewReady(status === 'ready');
    setReviewed(false);
    if (status === 'error') {
      invalidate(); setPreview(null);
      setError('PDF 预览渲染失败，请重新预览后重试。');
    }
  }, []);
  return <Modal open title={t('导出已保存简历')} width={900} onCancel={() => { invalidate(); onClose(); }} footer={null}>
    <p>{preview ? t('已保存版本：{title}', { title: preview.title }) : t('生成预览后显示已保存版本标题')} · {locale === 'zh-CN' ? '简体中文' : 'English'}</p>
    <Alert type="info" showIcon message={t('仅导出已保存的结构化字段和“其他”补充文本，不包含原始或上传原文。导出不会标记已投递或已提交，也不会联系第三方。')} />
    <p>{t('PDF 展示实际分页。DOCX 内容相同且可编辑，但 Word 和本地字体可能改变分页。')}</p>
    {blocked && <Alert type="warning" message={t('请先保存当前编辑，保存完成后再导出。')} />}
    {error && <Alert type="error" role="alert" message={t(error)} />}
    <Space wrap style={{ marginBlock: 16 }}>
      <Button disabled={blocked || busy} loading={busy && !preview} onClick={() => void generate()}>{t('生成 PDF 预览')}</Button>
      <Button disabled={blocked || busy || !preview || !previewReady || !reviewed} onClick={() => void download('pdf')}>{t('下载 PDF')}</Button>
      <Button disabled={blocked || busy || !preview || !previewReady || !reviewed} onClick={() => void download('docx')}>{t('下载 DOCX')}</Button>
    </Space>
    {preview && <>
      <ResumePdfPreview blob={preview.blob} onStatus={previewStatus} />
      <Checkbox checked={reviewed} disabled={busy || blocked || !previewReady} onChange={(event) => setReviewed(event.target.checked)}>{t('我已核对本次 PDF 预览，确认导出此版本。')}</Checkbox>
    </>}
  </Modal>;
}
