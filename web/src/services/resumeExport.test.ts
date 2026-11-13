// @vitest-environment jsdom
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, expect, it, vi } from 'vitest';
const adapter = vi.hoisted(() => vi.fn());
vi.mock('axios', async (importOriginal) => {
  const original = await importOriginal<typeof import('axios')>();
  return { ...original, default: { ...original.default, create: (config: object) => original.default.create({ ...config, adapter }) } };
});
import { downloadResumeExport, exportFilename, previewResumeExport, saveExportBlob } from './resumeExport';
import { setStoredAuthToken } from './authToken';
afterEach(() => { adapter.mockReset(); vi.restoreAllMocks(); vi.useRealTimers(); localStorage.clear(); });
it('uses authenticated blob POST requests and freezes the preview fingerprint', async () => {
  setStoredAuthToken('test-token');
  adapter.mockImplementation(async (config) => ({ data: new Blob(['pdf'], { type: 'application/pdf' }), status: 200, statusText: 'OK', config, headers: { 'content-type': 'application/pdf', 'x-resume-export-fingerprint': 'frozen', 'x-resume-export-title': encodeURIComponent('Server renamed title') } }));
  expect((await previewResumeExport(2, 'en-US')).fingerprint).toBe('frozen');
  await downloadResumeExport(2, 'en-US', 'pdf', 'frozen');
  const [preview, download] = adapter.mock.calls.map(([config]) => config);
  expect(preview.headers.get('X-OfferPilot-Token')).toBe('test-token');
  expect(preview.responseType).toBe('blob');
  expect(preview.url).toBe('/resumes/2/export-preview');
  expect(JSON.parse(preview.data)).toEqual({ locale: 'en-US' });
  expect(JSON.parse(download.data)).toEqual({ locale: 'en-US', format: 'pdf', source_fingerprint: 'frozen' });
});
it('decodes error blobs without exposing arbitrary HTML or backend messages', async () => {
  adapter.mockRejectedValue(new AxiosError('failure', 'ERR_BAD_RESPONSE', undefined, undefined, { status: 409, data: new Blob(['<script>bad</script>']), statusText: 'Conflict', headers: {}, config: { headers: new AxiosHeaders() } }));
  await expect(previewResumeExport(2, 'zh-CN')).rejects.toMatchObject({ kind: 'stale' });
});
it('sanitizes server filenames and forces the requested extension', () => {
  expect(exportFilename("attachment; filename*=UTF-8''..%2Funsafe%0A.pdf", 'pdf')).toBe('_unsafe_.pdf');
  expect(exportFilename('attachment; filename="hello.docx"', 'pdf')).toBe('hello.pdf');
  expect(exportFilename("attachment; filename*=UTF-8''%zz", 'docx')).toBe('resume.docx');
});
it('revokes download object URLs after dispatch', () => {
  vi.useFakeTimers();
  URL.createObjectURL = vi.fn(() => 'blob:download'); URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  saveExportBlob(new Blob(['test']), 'resume.pdf');
  expect(document.querySelector('a')).toBeNull();
  vi.runAllTimers(); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:download');
});
it('accepts DOCX bytes and rejects non-document success responses or missing fingerprints', async () => {
  adapter.mockImplementationOnce(async (config) => ({ data: new Blob(['docx']), status: 200, statusText: 'OK', config, headers: { 'content-type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'content-disposition': 'attachment; filename="Resume.docx"' } }));
  expect((await downloadResumeExport(2, 'zh-CN', 'docx', 'frozen')).filename).toBe('Resume.docx');
  adapter.mockImplementationOnce(async (config) => ({ data: new Blob(['html'], { type: 'text/html' }), status: 200, statusText: 'OK', config, headers: {} }));
  await expect(previewResumeExport(2, 'zh-CN')).rejects.toMatchObject({ kind: 'failed' });
  adapter.mockImplementationOnce(async (config) => ({ data: new Blob(['pdf'], { type: 'application/pdf' }), status: 200, statusText: 'OK', config, headers: {} }));
  await expect(previewResumeExport(2, 'zh-CN')).rejects.toMatchObject({ kind: 'failed' });
});
it('requires a valid server snapshot title and uses a rename made before preview', async () => {
  const response = (headers: Record<string, string>) => async (config: unknown) => ({ data: new Blob(['pdf'], { type: 'application/pdf' }), status: 200, statusText: 'OK', config, headers: { 'x-resume-export-fingerprint': 'new-snapshot', ...headers } });
  adapter.mockImplementationOnce(response({ 'x-resume-export-title': encodeURIComponent('已重命名 / 最新') }));
  expect((await previewResumeExport(2, 'zh-CN')).title).toBe('已重命名 / 最新');
  for (const title of [undefined, '%zz', '%0Aunsafe', '%20']) {
    adapter.mockImplementationOnce(response(title === undefined ? {} : { 'x-resume-export-title': title }));
    await expect(previewResumeExport(2, 'zh-CN')).rejects.toMatchObject({ kind: 'failed' });
  }
});
