import type { ApplicationCreationInput } from '@/types/application';

export interface PendingCreation {
  request: ApplicationCreationInput;
  status: 'submitting' | 'unknown';
}
const prefix = (scope: string) => {
  if (typeof scope !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(scope)) {
    throw new Error('恢复记录工作区无效');
  }
  return `offerpilot.application-create.${scope}.`;
};

// ADR-0006 permits this one bounded, credential-free recovery envelope, not
// arbitrary AI state or transport options. Validate the JSON that is stored
// and replayed; never silently remove fields from an original request.
function decodePendingCreation(scope: string, serialized: string): PendingCreation {
  function object(value: unknown, fields: readonly string[]): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || Object.keys(value).some((key) => !fields.includes(key))) {
      throw new Error('恢复记录含非许可字段或格式错误，请先检查已有投递');
    }
    return value as Record<string, unknown>;
  }
  const envelope = object(JSON.parse(serialized), ['request', 'status']);
  const request = object(envelope.request, [
    'company_name', 'position_name', 'job_url', 'status', 'notes', 'closed_reason',
    'expected_scope_id', 'idempotency_key', 'initial_jd',
  ]);
  if (typeof envelope.status !== 'string' || !['submitting', 'unknown'].includes(envelope.status)
      || request.expected_scope_id !== scope
      || typeof request.idempotency_key !== 'string'
      || !/^[A-Za-z0-9_-]{16,128}$/.test(request.idempotency_key)
      || typeof request.company_name !== 'string' || !request.company_name.trim()
      || typeof request.position_name !== 'string' || !request.position_name.trim()) {
    throw new Error('恢复记录身份或状态无效，请先检查已有投递');
  }
  for (const field of ['job_url', 'notes', 'closed_reason']) {
    if (field in request && typeof request[field] !== 'string') {
      throw new Error('恢复记录字段类型无效，请先检查已有投递');
    }
  }
  if ('status' in request && (typeof request.status !== 'string'
      || !['pending', 'applied', 'written_test', 'interview', 'offer', 'closed'].includes(request.status))) {
    throw new Error('恢复记录投递状态无效，请先检查已有投递');
  }
  if (request.initial_jd !== null) {
    const jd = object(request.initial_jd, ['jd_text', 'source_url']);
    if (typeof jd.jd_text !== 'string' || (jd.source_url !== null && typeof jd.source_url !== 'string')) {
      throw new Error('恢复记录 JD 格式无效，请先检查已有投递');
    }
  }
  return envelope as unknown as PendingCreation;
}

export function loadPendingCreations(scope: string): PendingCreation[] {
  const records: PendingCreation[] = [];
  const namespace = prefix(scope);
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith(namespace)) {
      const record = decodePendingCreation(scope, localStorage.getItem(key)!);
      if (key !== namespace + record.request.idempotency_key) {
        throw new Error('恢复记录标识不一致，请先检查已有投递');
      }
      records.push(record);
    }
  }
  return records;
}
export function savePendingCreation(scope: string, record: PendingCreation) {
  const namespace = prefix(scope);
  const serialized = JSON.stringify(record);
  const validated = decodePendingCreation(scope, serialized);
  localStorage.setItem(namespace + validated.request.idempotency_key, serialized);
}
export function clearPendingCreation(scope: string, key: string) {
  localStorage.removeItem(prefix(scope) + key);
}

/** Serialize the final local check across tabs before any HTTP request is sent. */
export async function claimPendingCreation(
  scope: string, record: PendingCreation, bypassed: ReadonlySet<string>,
): Promise<PendingCreation | null> {
  const claim = () => {
    decodePendingCreation(scope, JSON.stringify(record));
    const existing = loadPendingCreations(scope);
    // Replays must remain possible even if the user previously opted to create
    // another record and there are now multiple unknown submissions.
    if (existing.some((item) => item.request.idempotency_key === record.request.idempotency_key)) return null;
    const unresolved = existing.find((item) =>
      item.request.idempotency_key !== record.request.idempotency_key
      && !bypassed.has(item.request.idempotency_key),
    );
    if (unresolved) return unresolved;
    savePendingCreation(scope, record);
    return null;
  };
  return navigator.locks ? navigator.locks.request(prefix(scope), claim) : claim();
}
