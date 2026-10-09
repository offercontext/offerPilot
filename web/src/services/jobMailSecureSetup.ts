import { authHeaders } from './authToken';
import type { JobMailStatus } from '@/types/jobMail';
import { MailSecureSetupError, type JobMailSecureCapability, type JobMailSetupSession, type JobMailSetupTestResult } from '@/types/jobMailSecureSetup';

export const JOB_MAIL_SECURE_CAPABILITY_KEY = ['job-mail', 'secure-setup', 'capability'];
const SAFE_ERROR_CODES = new Set([
  'setup_expired', 'setup_not_found', 'setup_replaced', 'mail_connection_failed',
  'folder_discovery_failed', 'secure_store_unavailable', 'credential_save_failed',
  'credential_removal_failed', 'invalid_request', 'setup_not_local', 'request_denied',
  'local_browser_required', 'secure_setup_expired', 'secure_setup_conflict', 'secure_input_invalid',
  'secure_test_failed', 'secure_credential_write_failed', 'credential_delete_pending',
]);

async function secureRequest<T>(path: string, body?: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(abort, 35_000);
  try {
    const response = await fetch(path === 'disconnect' ? '/api/job-mail/disconnect' : `/api/job-mail/secure-setup/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin', mode: 'same-origin', redirect: 'error', cache: 'no-store',
      headers: { ...authHeaders(), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: controller.signal,
      ...(path === 'cancel' ? { keepalive: true } : {}),
    });
    if (!response.ok) {
      const result: unknown = await response.json().catch(() => null);
      const code = result && typeof result === 'object' && 'error_code' in result ? result.error_code : undefined;
      throw new MailSecureSetupError(typeof code === 'string' && SAFE_ERROR_CODES.has(code) ? code
        : response.status === 400 || response.status === 422 ? 'invalid_request'
          : response.status === 401 || response.status === 403 ? 'request_denied' : 'request_failed');
    }
    return response.status === 204 ? undefined as T : await response.json() as T;
  } catch (error) {
    if (error instanceof MailSecureSetupError) throw error;
    // No original exception or cause: either may contain a secret-bearing payload.
    throw new MailSecureSetupError('request_failed');
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}

export function getJobMailSecureCapability(): Promise<JobMailSecureCapability> {
  return secureRequest('status');
}
export function startJobMailSecureSetup(purpose: 'connect' | 'disconnect' = 'connect'): Promise<JobMailSetupSession> {
  return secureRequest('start', { explicit_setup_consent: true, purpose });
}
export function testJobMailSecureSetup(input: { setup_token: string; email: string; authorization_code: string; explicit_test_consent: true }, signal?: AbortSignal): Promise<JobMailSetupTestResult> {
  return secureRequest('test', input, signal);
}
export function saveJobMailSecureSetup(input: { setup_token: string; folder_ids: string[]; backfill_days: 0 | 7; explicit_save_consent: true }, signal?: AbortSignal): Promise<JobMailStatus> {
  return secureRequest('save', input, signal);
}
export async function cancelJobMailSecureSetup(setupToken: string): Promise<void> {
  await secureRequest('cancel', { setup_token: setupToken });
}

export function disconnectRealJobMail(setupToken: string): Promise<JobMailStatus> {
  return secureRequest('disconnect', { setup_token: setupToken, explicit_delete_consent: true });
}
