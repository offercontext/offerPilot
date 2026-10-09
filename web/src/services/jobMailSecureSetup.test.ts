import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailSecureSetupError } from '@/types/jobMailSecureSetup';
vi.mock('./authToken', () => ({ authHeaders: () => ({ 'X-OfferPilot-Token': 'fixture-auth' }) }));
const api = await import('./jobMailSecureSetup');
const fetchMock = vi.fn();
const fixtureSecret = 'synthetic-code-for-unit-tests-only';
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockReset(); fetchMock.mockResolvedValue(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })); });
afterEach(() => vi.unstubAllGlobals());

describe('secret-bearing setup requests', () => {
  it('uses same-origin no-store requests without redirects and keeps tokens out of URLs', async () => {
    await api.testJobMailSecureSetup({ setup_token: 'fixture-session', email: 'fixture@qq.com', authorization_code: fixtureSecret, explicit_test_consent: true });
    expect(fetchMock).toHaveBeenCalledWith('/api/job-mail/secure-setup/test', expect.objectContaining({
      method: 'POST', mode: 'same-origin', credentials: 'same-origin', redirect: 'error', cache: 'no-store',
      headers: { 'X-OfferPilot-Token': 'fixture-auth', 'Content-Type': 'application/json' },
      body: JSON.stringify({ setup_token: 'fixture-session', email: 'fixture@qq.com', authorization_code: fixtureSecret, explicit_test_consent: true }),
    }));
    expect(fetchMock.mock.calls[0][0]).not.toContain('fixture-session');
  });
  it('does not retain or surface secret-bearing server/native error details', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: fixtureSecret, error_code: 'untrusted-code', detail: { input: fixtureSecret } }), { status: 422 }));
    const error = await api.testJobMailSecureSetup({ setup_token: 'fixture', email: 'fixture@qq.com', authorization_code: fixtureSecret, explicit_test_consent: true }).catch((value) => value);
    expect(error).toBeInstanceOf(MailSecureSetupError); expect(error.code).toBe('invalid_request');
    expect(String(error) + JSON.stringify(error)).not.toContain(fixtureSecret); expect(error.cause).toBeUndefined(); expect(error.response).toBeUndefined();
    fetchMock.mockRejectedValueOnce(new Error(fixtureSecret));
    await expect(api.startJobMailSecureSetup()).rejects.toMatchObject({ code: 'request_failed' });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('keeps setup, verify and explicit vault-save separate and never retries a request', async () => {
    await api.startJobMailSecureSetup();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ explicit_setup_consent: true, purpose: 'connect' });
    fetchMock.mockResolvedValueOnce(new Response('{}'));
    await api.saveJobMailSecureSetup({ setup_token: 'fixture-session', folder_ids: ['folder-1'], backfill_days: 0, explicit_save_consent: true });
    expect(fetchMock.mock.calls[1][0]).toBe('/api/job-mail/secure-setup/save');
    expect(fetchMock.mock.calls[1][1].body).not.toContain('authorization_code');
    fetchMock.mockRejectedValueOnce(new Error('network'));
    await expect(api.startJobMailSecureSetup()).rejects.toMatchObject({ code: 'request_failed' });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
  it('binds credential deletion to a dedicated setup purpose and explicit delete consent', async () => {
    await api.startJobMailSecureSetup('disconnect');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).purpose).toBe('disconnect');
    fetchMock.mockResolvedValueOnce(new Response('{}'));
    await api.disconnectRealJobMail('fixture-session');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/job-mail/disconnect');
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ setup_token: 'fixture-session', explicit_delete_consent: true });
  });
  it('cancels using a best-effort keepalive body and no token-bearing URL', async () => {
    await api.cancelJobMailSecureSetup('fixture-session');
    expect(fetchMock).toHaveBeenCalledWith('/api/job-mail/secure-setup/cancel', expect.objectContaining({ keepalive: true, body: '{"setup_token":"fixture-session"}' }));
  });
});
