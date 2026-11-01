import { useEffect, useSyncExternalStore } from 'react';
import { getPersona, type PersonaView } from '@/services/persona';
let name = 'Haru';
let generation = 0;
let loading: Promise<void> | undefined;
const listeners = new Set<() => void>();
const KEY = 'offerpilot.persona.changed';
function emit() { listeners.forEach(listener => listener()); }
export function publishPersona(view: PersonaView) {
  generation += 1; name = view.spec.display_name; emit();
  try { localStorage.setItem(KEY, `${view.profile_id}:${view.revision}`); } catch { /* Locked storage does not block saving. */ }
}
function refresh(force = false): Promise<void> {
  if (force && loading) {
    generation += 1;
    return loading.then(() => refresh());
  }
  const started = generation;
  loading ??= getPersona().then(view => { if (started === generation) { name = view.spec.display_name; emit(); } }).catch(() => { /* Keep last known name; never block conversation. */ }).finally(() => { loading = undefined; });
  return loading;
}
export function useCompanionName() {
  const value = useSyncExternalStore(listener => { listeners.add(listener); return () => listeners.delete(listener); }, () => name, () => 'Haru');
  useEffect(() => {
    void refresh();
    const onStorage = (event: StorageEvent) => { if (event.key === KEY) void refresh(true); };
    const onFocus = () => { void refresh(true); };
    window.addEventListener('storage', onStorage); window.addEventListener('focus', onFocus);
    return () => { window.removeEventListener('storage', onStorage); window.removeEventListener('focus', onFocus); };
  }, []);
  return value;
}
/** Apply only to authored UI copy, never user messages, tool IDs or product branding. */
export function companionText(authoredText: string, displayName: string): string {
  return authoredText.replace(/Haru/g, () => displayName);
}
