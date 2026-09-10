// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import { clearPendingCreation, loadPendingCreations, savePendingCreation, type PendingCreation } from './applicationCreationRecovery';

const scope = 'workspace-a';
const record = (): PendingCreation => ({
  status: 'unknown',
  request: {
    expected_scope_id: scope, idempotency_key: 'creation-test-key-001',
    company_name: '测试公司', position_name: '工程师', status: 'pending',
    notes: '仅岗位备注', job_url: 'https://job.invalid/?id=1&track=2', closed_reason: '',
    initial_jd: { jd_text: '  15–25K·14薪\n200元/天 ✨\n', source_url: 'https://source.invalid/?a=1&b=2' },
  },
});
beforeEach(() => localStorage.clear());

describe('bounded Application creation recovery storage', () => {
  it('round-trips only the exact original request in its workspace until confirmed', () => {
    const original = record();
    savePendingCreation(scope, original);
    expect(loadPendingCreations(scope)).toEqual([original]);
    expect(loadPendingCreations('workspace-b')).toEqual([]);
    clearPendingCreation(scope, original.request.idempotency_key);
    expect(loadPendingCreations(scope)).toEqual([]);
  });
  it.each(['api_key', 'auth_token', 'confirmation_token', 'headers', 'operation_id'])('rejects an extra %s at every envelope level before storage', (field) => {
    for (const level of ['envelope', 'request', 'jd']) {
      const value = record();
      const target = level === 'envelope' ? value : level === 'request' ? value.request : value.request.initial_jd!;
      Object.assign(target, { [field]: 'must-not-persist' });
      expect(() => savePendingCreation(scope, value)).toThrow();
      expect(localStorage.length).toBe(0);
    }
  });
  it('rejects workspace mismatches and invalid runtime field types', () => {
    const foreign = record(); foreign.request.expected_scope_id = 'workspace-b';
    expect(() => savePendingCreation(scope, foreign)).toThrow();
    const invalid = record(); Object.assign(invalid.request, { notes: { api_key: 'secret' } });
    expect(() => savePendingCreation(scope, invalid)).toThrow();
    expect(localStorage.length).toBe(0);
  });
  it('rejects coercible statuses and request keys outside the server contract', () => {
    for (const change of [
      (value: PendingCreation) => Object.assign(value, { status: ['unknown'] }),
      (value: PendingCreation) => Object.assign(value.request, { status: ['pending'] }),
      (value: PendingCreation) => Object.assign(value.request, { idempotency_key: 'wrong:key-with-colon' }),
    ]) {
      const value = record(); change(value);
      expect(() => savePendingCreation(scope, value)).toThrow();
    }
    expect(localStorage.length).toBe(0);
  });
  it('fails closed on a corrupted or mis-keyed persisted envelope without deleting it', () => {
    const key = `offerpilot.application-create.${scope}.different-key-001`;
    localStorage.setItem(key, JSON.stringify(record()));
    expect(() => loadPendingCreations(scope)).toThrow();
    expect(localStorage.getItem(key)).not.toBeNull();
  });
});
