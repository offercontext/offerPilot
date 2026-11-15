import { useEffect, useSyncExternalStore } from 'react';
import { getPersona, type PersonaView } from '@/services/persona';
import { getCompanion } from '@/services/companion';
import type { CompanionSnapshot } from '@/features/companionLife/contracts';
import { acceptCompanionSnapshot } from '@/features/companionLife/model';
let name = 'Haru';
let generation = 0;
let lifeGeneration = 0;
let snapshot: CompanionSnapshot | null = null;
let disabledPending = false;
let invalidated = false;
let loading: Promise<void> | undefined;
let refreshQueued = false;
const listeners = new Set<() => void>();
const KEY = 'offerpilot.persona.changed';
export const COMPANION_CHANGED_KEY = 'offerpilot.companion.changed';
function emit() { listeners.forEach(listener => listener()); }
function displayName() { return snapshot?.settings.enabled && !disabledPending && !invalidated ? name : 'Pilot'; }
export function publishPersona(view: PersonaView) {
  generation += 1; name = view.spec.display_name; emit();
  try { localStorage.setItem(KEY, `${view.profile_id}:${view.revision}`); } catch { /* Locked storage does not block saving. */ }
}
/** Publish only accepted server snapshots; revisions fence delayed reads in other surfaces. */
export function publishCompanionSnapshot(next: CompanionSnapshot, confirmedRead = false) {
  const accepted = acceptCompanionSnapshot(snapshot, next);
  if (accepted !== next) return;
  const changed = snapshot?.settings.enabled !== accepted.settings.enabled;
  const advanced = !snapshot || accepted.revision > snapshot.revision;
  snapshot = accepted;
  // Presence-only heartbeats carry the old settings revision. They must neither
  // undo a cross-window invalidation nor fence out its authoritative GET.
  if (advanced) lifeGeneration += 1;
  if (advanced || confirmedRead) invalidated = false;
  emit();
  if (changed) {
    try { localStorage.setItem(COMPANION_CHANGED_KEY, `${accepted.identity}:${accepted.revision}`); } catch { /* Focus and polling still refresh locked storage. */ }
  }
}
/** A pending opt-out must hide the persona even when its receipt is uncertain. */
export function setCompanionDisabledPending(value: boolean) { disabledPending = value; emit(); }
function refresh(force = false): Promise<void> {
  if (loading) {
    if (force) { generation += 1; lifeGeneration += 1; refreshQueued = true; }
    return loading;
  }
  const started = generation;
  const lifeStarted = lifeGeneration;
  loading = Promise.all([
    getPersona().then(view => { if (started === generation) { name = view.spec.display_name; emit(); } }).catch(() => { /* Never block conversation on a persona read. */ }),
    getCompanion().then(next => { if (lifeStarted === lifeGeneration) publishCompanionSnapshot(next, true); }).catch(() => { /* Unknown or invalidated opt-in stays ordinary Pilot. */ }),
  ]).then(() => {}).finally(() => {
    loading = undefined;
    if (refreshQueued) { refreshQueued = false; void refresh(); }
  });
  return loading;
}
function onStorage(event: StorageEvent) {
  if (event.key !== KEY && event.key !== COMPANION_CHANGED_KEY && event.key !== null) return;
  if (event.key !== KEY) { invalidated = true; emit(); }
  void refresh(true);
}
function onFocus() { void refresh(true); }
function subscribe(listener: () => void) {
  if (!listeners.size) {
    window.addEventListener('storage', onStorage); window.addEventListener('focus', onFocus);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      window.removeEventListener('storage', onStorage); window.removeEventListener('focus', onFocus);
    }
  };
}
export function useCompanionName() {
  const value = useSyncExternalStore(subscribe, displayName, () => 'Pilot');
  useEffect(() => { void refresh(); }, []);
  return value;
}
/** Apply only to authored UI copy, never user messages, tool IDs or product branding. */
export function companionText(authoredText: string, displayName: string): string {
  return authoredText.replace(/Haru/g, () => displayName);
}
