import axios from 'axios';
import type { Locale } from '@/i18n';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api', timeout: 130000 });
export type ExportFormat = 'pdf' | 'docx';
export class ResumeExportError extends Error {
  constructor(public readonly kind: 'stale' | 'invalid' | 'auth' | 'failed' | 'unsupported' | 'too_large' | 'unsupported_text') { super(kind); }
}
export function exportFilename(disposition: string | undefined, format: ExportFormat): string {
  const encoded = disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = disposition?.match(/filename="([^"]*)"|filename=([^;]+)/i);
  let name = plain?.[1] ?? plain?.[2] ?? 'resume';
  if (encoded) { try { name = decodeURIComponent(encoded); } catch { name = 'resume'; } }
  name = name.replace(/[\\/<>:"|?*\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g, '_').replace(/^\.+|[. ]+$/g, '').slice(0, 150);
  name = name.replace(/\.(pdf|docx)$/i, '');
  return `${name || 'resume'}.${format}`;
}
async function binary(id: number, locale: Locale, format: ExportFormat, fingerprint: string | undefined, signal?: AbortSignal) {
  try {
    const response = await http.post<Blob>(`/resumes/${id}/${fingerprint === undefined ? 'export-preview' : 'export'}`,
      fingerprint === undefined ? { locale } : { locale, format, source_fingerprint: fingerprint }, { responseType: 'blob', signal });
    const expected = format === 'pdf' ? 'application/pdf' : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
    if (!(response.data instanceof Blob) || !String(response.headers['content-type'] ?? response.data.type).includes(expected)) throw new ResumeExportError('failed');
    let title: string | undefined;
    if (fingerprint === undefined) {
      const encodedTitle = response.headers['x-resume-export-title'];
      try {
        if (typeof encodedTitle !== 'string' || !encodedTitle) throw new Error('missing title');
        title = decodeURIComponent(encodedTitle);
        if (!title.trim() || /[\u0000-\u001f\u007f]/.test(title)) throw new Error('invalid title');
      } catch { throw new ResumeExportError('failed'); }
    }
    return { blob: response.data, filename: exportFilename(response.headers['content-disposition'], format), fingerprint: String(response.headers['x-resume-export-fingerprint'] ?? ''), title };
  } catch (error) {
    if (error instanceof ResumeExportError || axios.isCancel(error)) throw error;
    if (axios.isAxiosError(error)) {
      // Decode bounded JSON error blobs, but never render server HTML or arbitrary exception text.
      const data = error.response?.data;
      let code: unknown;
      if (data instanceof Blob && data.size <= 65536) { try { code = JSON.parse(await data.text()).error_code; } catch { /* Invalid error body uses the localized fallback. */ } }
      else if (data && typeof data === 'object') code = data.error_code;
      if (code === 'resume_export_unsupported_fields') throw new ResumeExportError('unsupported');
      if (code === 'resume_export_too_large') throw new ResumeExportError('too_large');
      if (code === 'resume_export_unsupported_text') throw new ResumeExportError('unsupported_text');
      const status = error.response?.status;
      throw new ResumeExportError(status === 409 ? 'stale' : status === 422 || status === 400 ? 'invalid' : status === 401 || status === 403 ? 'auth' : 'failed');
    }
    throw new ResumeExportError('failed');
  }
}
export async function previewResumeExport(id: number, locale: Locale, signal?: AbortSignal) {
  const result = await binary(id, locale, 'pdf', undefined, signal);
  if (!result.fingerprint) throw new ResumeExportError('failed');
  return result;
}
export async function downloadResumeExport(id: number, locale: Locale, format: ExportFormat, fingerprint: string, signal?: AbortSignal) {
  return binary(id, locale, format, fingerprint, signal);
}
export function saveExportBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename;
  document.body.append(anchor);
  try { anchor.click(); } finally { anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
}
