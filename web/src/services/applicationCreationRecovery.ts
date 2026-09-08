import type { ApplicationCreationInput } from '@/types/application';

export interface PendingCreation {
  request: ApplicationCreationInput;
  status: 'submitting' | 'unknown';
}
const prefix = (scope: string) => `offerpilot.application-create.${scope}.`;
export function loadPendingCreations(scope: string): PendingCreation[] {
  const records: PendingCreation[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith(prefix(scope))) {
      const record = JSON.parse(localStorage.getItem(key)!);
      if (!record?.request?.idempotency_key || !record.request.company_name || !record.request.position_name) {
        throw new Error('恢复记录损坏，请先检查已有投递');
      }
      records.push(record);
    }
  }
  return records;
}
export function savePendingCreation(scope: string, record: PendingCreation) {
  localStorage.setItem(prefix(scope) + record.request.idempotency_key, JSON.stringify(record));
}
export function clearPendingCreation(scope: string, key: string) {
  localStorage.removeItem(prefix(scope) + key);
}

/** Serialize the final local check across tabs before any HTTP request is sent. */
export async function claimPendingCreation(
  scope: string, record: PendingCreation, bypassed: ReadonlySet<string>,
): Promise<PendingCreation | null> {
  const claim = () => {
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
