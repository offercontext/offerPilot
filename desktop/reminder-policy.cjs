'use strict';
// Delivery permission is local opt-in; business eligibility remains the backend
// candidate contract. Display-only candidates must never become native alerts.
const REASONS = Object.freeze(['deadline', 'interview_preparation', 'review', 'stale_application']);
const CATCHUP_MS = 15 * 60 * 1000;
const BATCH_LIMIT = 3;
const HOUR_LIMIT = 10;
const HISTORY_LIMIT = 1000;
const HISTORY_MS = 8 * 24 * 60 * 60 * 1000;
const positive = n => Number.isSafeInteger(n) && n > 0;
function preferences(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).some(k => !['enabled','scopes','paused','quietEnabled','quietStart','quietEnd'].includes(k))
    || !['enabled','paused','quietEnabled'].every(k => typeof value[k] === 'boolean')
    || !Array.isArray(value.scopes) || value.scopes.length > REASONS.length
    || new Set(value.scopes).size !== value.scopes.length || value.scopes.some(k => !REASONS.includes(k))
    || !['quietStart','quietEnd'].every(k => Number.isInteger(value[k]) && value[k] >= 0 && value[k] <= 23)
    || (value.enabled && value.scopes.length === 0)) throw new Error('invalid_preferences');
  return { enabled:value.enabled, scopes:[...value.scopes], paused:value.paused,
    quietEnabled:value.quietEnabled, quietStart:value.quietStart, quietEnd:value.quietEnd };
}
const defaults = () => ({ enabled:false, scopes:[], paused:false, quietEnabled:true, quietStart:22, quietEnd:8 });
function quiet(p, now) {
  if (p.paused) return true;
  if (!p.quietEnabled) return false;
  const h = new Date(now).getHours();
  return p.quietStart === p.quietEnd || (p.quietStart < p.quietEnd
    ? h >= p.quietStart && h < p.quietEnd : h >= p.quietStart || h < p.quietEnd);
}
function candidate(raw) {
  if (!raw || !REASONS.includes(raw.reason_code) || !positive(raw.application_id)
    || !positive(raw.subject_id) || typeof raw.source_revision !== 'string'
    || !/^[a-f0-9]{64}$/.test(raw.source_revision)
    || ![raw.delivery_due_at,raw.delivery_expires_at].every(n => typeof n === 'number' && Number.isFinite(n) && n > 0 && n < 1e12)
    || raw.delivery_expires_at <= raw.delivery_due_at) return null;
  const app = raw.reason_code === 'stale_application';
  if (raw.subject_type !== (app ? 'application' : 'application_event')
    || (app ? raw.subject_id !== raw.application_id || raw.event_id !== null : raw.event_id !== raw.subject_id)
    || raw.id !== `${raw.subject_type}:${raw.subject_id}:${raw.reason_code}` || raw.offer_id !== null) return null;
  return { id:raw.id, reason:raw.reason_code, revision:raw.source_revision,
    applicationId:raw.application_id, eventId:app ? null : raw.event_id,
    due:raw.delivery_due_at * 1000, expires:raw.delivery_expires_at * 1000,
    key:`${raw.id}:${raw.delivery_due_at}` };
}
function snapshot(body) {
  if (!body || !Array.isArray(body.items) || body.items.length > 2000) throw new Error('invalid_candidates');
  const all = new Map();
  for (const raw of body.items) {
    const c = candidate(raw);
    if (!c) continue;
    // Contradictory duplicate identities fail the entire read, not last-wins.
    if (all.has(c.id)) throw new Error('duplicate_candidate');
    all.set(c.id,c);
  }
  return [...all.values()];
}
function eligible(c, p, now, enabledAt) {
  return p.enabled && p.scopes.includes(c.reason) && !quiet(p,now)
    && c.due >= enabledAt && c.due <= now && c.due >= now - CATCHUP_MS && now < c.expires;
}
function select(candidates, p, history, now, enabledAt) {
  const budget = Math.max(0,HOUR_LIMIT - Object.values(history).filter(n => n >= now - 3600000).length);
  return candidates.filter(c => eligible(c,p,now,enabledAt) && !Object.hasOwn(history,c.key))
    .sort((a,b) => b.due-a.due || a.id.localeCompare(b.id)).slice(0,Math.min(BATCH_LIMIT,budget));
}
function advanceClock(highWater, now) {
  if (!Number.isFinite(now) || now <= 0 || !Number.isFinite(highWater) || highWater < 0
    || now < highWater - 2000) throw new Error('clock_changed');
  return Math.max(highWater,now);
}
function prune(history, now) {
  const entries = Object.entries(history).filter(([,v]) => Number.isFinite(v) && v >= now-HISTORY_MS);
  // Never evict live dedupe entries to make room: rate-limit until they expire.
  if (entries.length >= HISTORY_LIMIT) throw new Error('history_full');
  return Object.fromEntries(entries);
}
module.exports = { REASONS,CATCHUP_MS,BATCH_LIMIT,HOUR_LIMIT,HISTORY_LIMIT,HISTORY_MS,
  preferences,defaults,quiet,candidate,snapshot,eligible,select,prune,advanceClock };
