import type { PersonaMutation } from '@/services/persona';
import { validPersona } from './personaModel';
const KEY = 'offerpilot.persona.pending-mutation';
let pending: PersonaMutation | null = null;
/** Retain the exact idempotency key across settings navigation and same-tab reload. */
export function readPendingPersonaMutation(profileId: string): PersonaMutation | null {
  if (!pending) {
    try {
      const raw = sessionStorage.getItem(KEY);
      const value = raw ? JSON.parse(raw) as PersonaMutation : null;
      if (value && typeof value.command_id === 'string' && typeof value.profile_id === 'string'
        && Number.isInteger(value.expected_revision) && value.confirmed === true
        && (value.operation === 'update' && validPersona(value.spec)
          || value.operation === 'restore' && Number.isInteger(value.target_revision))) pending = value;
    } catch { /* In-memory retention still covers navigation with locked storage. */ }
  }
  return pending?.profile_id === profileId ? pending : null;
}
export function retainPendingPersonaMutation(command: PersonaMutation | null) {
  pending = command;
  try {
    if (command) sessionStorage.setItem(KEY, JSON.stringify(command));
    else sessionStorage.removeItem(KEY);
  } catch { /* Do not discard an in-memory retry key because storage is locked. */ }
}
