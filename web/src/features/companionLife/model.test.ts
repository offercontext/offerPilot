import { afterEach, describe, expect, it } from 'vitest';
import { acceptCompanionSnapshot, blocksCompanion, canPresentBid, companionMirror } from './model';
import { setLocale, t } from '@/i18n';
import { companionLifeExpression } from './copy';
import type { CompanionSignals, CompanionSnapshot } from './contracts';
export const signals: CompanionSignals = { visible: true, typing: false, work_running: false, awaiting_approval: false, mock_interview: false, quiet: false, failure: false };
export const snapshot: CompanionSnapshot = { identity: 'one', revision: 4, persona_version: 'companion-v1', settings: { enabled: true, proactivity: 'default', hide_status: false }, state: { interests: [], activity: null, wish: null }, recent_events: [], shared_participation: [], presence: { session_id: 'session', session_quiet: false, active: true, blocked: false, blockers: [] } };
afterEach(() => { setLocale('zh-CN'); });
describe('companion life presentation safety', () => {
  it.each(['typing', 'work_running', 'awaiting_approval', 'mock_interview', 'quiet', 'failure'] as const)('suppresses a bid and mirror for %s', key => {
    const blocked = { ...signals, [key]: true };
    expect(blocksCompanion(blocked)).toBe(true);
    expect(companionMirror(snapshot, blocked)).toBeUndefined();
    expect(canPresentBid({ id: 'bid', kind: 'companion_life', result_text: 'hello', source_identity: 'one', source_revision: 4 }, snapshot, blocked)).toBe(false);
  });
  it('rejects stale responses and never restores data from before reset', () => {
    expect(acceptCompanionSnapshot(snapshot, { ...snapshot, revision: 3 })).toBe(snapshot);
    const reset = { ...snapshot, identity: 'two', revision: 5 };
    expect(acceptCompanionSnapshot(snapshot, reset)).toBe(reset);
    expect(acceptCompanionSnapshot(reset, snapshot)).toBe(reset);
  });
  it('checks identity, revision, disabled, hidden and invisible again after transport', () => {
    const bid = { id: 'bid', kind: 'companion_life' as const, result_text: 'hello', source_identity: 'one', source_revision: 4 };
    expect(canPresentBid(bid, snapshot, signals)).toBe(true);
    for (const current of [{ ...snapshot, revision: 5 }, { ...snapshot, identity: 'two' }, { ...snapshot, settings: { ...snapshot.settings, enabled: false } }, { ...snapshot, settings: { ...snapshot.settings, hide_status: true } }, { ...snapshot, settings: { ...snapshot.settings, proactivity: 'off' as const } }]) expect(canPresentBid(bid, current, signals)).toBe(false);
    expect(canPresentBid(bid, snapshot, { ...signals, visible: false })).toBe(false);
  });
});

it('mirrors exactly the shared expression across settings revisions, refresh and both locales', () => {
  const current: CompanionSnapshot = { ...snapshot, state: { ...snapshot.state, activity: {
    id: 'stable-boat', template_id: 'paper_boat', title: '折一只纸小船', status: 'active',
    step_index: 1, step_count: 3, step_title: '折好船身', started_at: 1, updated_at: 2,
    promise: null, participants: ['companion'],
  } } };
  for (const locale of ['zh-CN', 'en-US'] as const) {
    setLocale(locale);
    const expected = companionLifeExpression(current.state.activity, t);
    expect(companionMirror(current, signals)?.detail).toBe(expected);
    const refreshed = JSON.parse(JSON.stringify(current));
    refreshed.revision += 1;
    refreshed.settings.proactivity = 'less';
    expect(companionMirror(refreshed, signals)?.detail).toBe(expected);
    for (const status of ['paused', 'waiting_shared', 'completed'] as const) {
      const changed = { ...current, state: { ...current.state, activity: { ...current.state.activity!, status, promise: status === 'waiting_shared' ? '等你一起选颜色' : null } } };
      expect(companionMirror(changed, signals)?.detail).toBe(companionLifeExpression(changed.state.activity, t));
    }
  }
});

it('includes a current wish only in an eligible bid mirror, never ordinary status or stale/disabled invitations', () => {
  const activity = { id: 'boat', template_id: 'paper_boat', title: '折一只纸小船', status: 'active' as const, step_index: 1, step_count: 3, step_title: '折好船身', started_at: 1, updated_at: 2, promise: null, participants: ['companion'] };
  const current: CompanionSnapshot = { ...snapshot, state: { ...snapshot.state, activity, wish: { id: 'wish', activity_id: 'boat', text: '一起给纸小船起个名字' } } };
  const bid = { id: 'bid', kind: 'companion_life' as const, result_text: 'backend prose', source_identity: current.identity, source_revision: current.revision };
  for (const locale of ['zh-CN', 'en-US'] as const) {
    setLocale(locale);
    const plain = companionLifeExpression(activity, t);
    expect(companionMirror(current, signals)?.detail).toBe(plain);
    expect(companionMirror(current, signals, bid)?.detail).toBe(companionLifeExpression(activity, t, current.state.wish));
    expect(companionMirror(current, signals, { ...bid, source_revision: bid.source_revision - 1 })?.detail).toBe(plain);
    expect(companionMirror(current, signals, { ...bid, source_identity: 'old' })?.detail).toBe(plain);
    expect(companionMirror({ ...current, settings: { ...current.settings, proactivity: 'off' } }, signals, bid)?.detail).toBe(plain);
    expect(companionMirror({ ...current, state: { ...current.state, wish: null } }, signals, bid)?.detail).toBe(plain);
    expect(companionMirror(current, { ...signals, quiet: true }, bid)).toBeUndefined();
  }
});
