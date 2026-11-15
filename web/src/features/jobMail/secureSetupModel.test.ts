import { describe, expect, it } from 'vitest';
import { MailSecureSetupError, type JobMailSecureCapability } from '@/types/jobMailSecureSetup';
import { canEnterMailCredential, isLoopbackSetupPage, mailSecureSetupErrorText } from './secureSetupModel';
const capability: JobMailSecureCapability = { available: true, reason: '', backend: 'Native fixture', local_only: true, credential_input_allowed: true, configured: false, deletion_pending: false };
const local = { hostname: '127.0.0.1', protocol: 'http:' };
describe('credential entry capability boundary', () => {
  it.each(['localhost', '127.0.0.1', '[::1]'])('permits verified direct loopback page %s only when the server also allows it', (hostname) => {
    expect(canEnterMailCredential(capability, { ...local, hostname })).toBe(true);
    expect(canEnterMailCredential(undefined, { ...local, hostname })).toBe(false);
  });
  it.each(['localhost.evil.test', '127.0.0.1.evil.test', '192.168.1.3', 'example.com'])('blocks remote/untrusted host %s', (hostname) => {
    expect(isLoopbackSetupPage({ hostname, protocol: 'https:' })).toBe(false);
  });
  it('fails closed for unavailable, remote, configured and deletion-pending states', () => {
    expect(canEnterMailCredential({ ...capability, available: false }, local)).toBe(false);
    expect(canEnterMailCredential({ ...capability, credential_input_allowed: false }, local)).toBe(false);
    expect(canEnterMailCredential({ ...capability, configured: true }, local)).toBe(false);
    expect(canEnterMailCredential({ ...capability, deletion_pending: true }, local)).toBe(false);
  });
  it('maps only fixed error codes, never arbitrary exception text', () => {
    expect(mailSecureSetupErrorText(new Error('synthetic-secret'))).not.toContain('synthetic-secret');
    expect(mailSecureSetupErrorText(new MailSecureSetupError('credential_delete_pending'))).toContain('不能视为已清理');
  });
});
