// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import { displayMailValue, isDefiniteMailRejection, readMailRecovery, saveMailRecovery, validateMailFields } from './jobMailModel';
const suggestionId = '12345678-1234-4123-8123-123456789012';
const operationId = '98765432-1234-4123-8123-123456789012';
beforeEach(() => sessionStorage.clear());
describe('mail review safety model', () => {
  it.each([undefined, 0, -3, 1.5])('never invents a duration when it is %s', (duration) => {
    expect(validateMailFields({ event_type: 'interview', scheduled_at: '2026-10-15T15:00:00+08:00', duration_minutes: duration })).toContain('正整数');
  });
  it.each(['2026-10-15', '2026-10-15T15:00', '明天下午', '2026-10-15T15:00:00'])('rejects incomplete or timezone-less time %s', (time) => {
    expect(validateMailFields({ event_type: 'interview', scheduled_at: time, duration_minutes: 45 })).toContain('时区');
  });
  it('allows a complete supported event and keeps assessment under written_test', () => {
    expect(validateMailFields({ event_type: 'written_test', subtype: 'assessment', scheduled_at: '2026-10-15T15:00:00+08:00', duration_minutes: 45 })).toBeNull();
    expect(validateMailFields({ event_type: 'deadline', scheduled_at: '2026-10-15T15:00:00+08:00', duration_minutes: 45 })).toContain('首批');
  });
  it('persists only operation identity for recovery, not source text or edited values', () => {
    expect(saveMailRecovery(suggestionId, operationId)).toBe(true);
    expect(readMailRecovery()).toEqual({ [suggestionId]: operationId });
    expect(saveMailRecovery(suggestionId, null)).toBe(true);
    expect(readMailRecovery()).toEqual({});
  });
  it('fails closed on corrupted recovery data and distinguishes ambiguous failures', () => {
    sessionStorage.setItem('offerpilot:job-mail:pending-operations', '{');
    expect(readMailRecovery()).toEqual({});
    expect(isDefiniteMailRejection({ response: { status: 409 } })).toBe(true);
    expect(isDefiniteMailRejection({ response: { status: 500 } })).toBe(false);
    expect(isDefiniteMailRejection(new Error('timeout'))).toBe(false);
  });
  it('preserves malicious-looking evidence as text and unknown values as unknown', () => {
    expect(displayMailValue('<script>run()</script>')).toBe('<script>run()</script>');
    expect(displayMailValue(null)).toBe('未提供');
  });
});
