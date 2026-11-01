/** Synthetic UI transport, not an implementation or proof of backend persistence.
 * Commands follow services/companion.ts and companionLife/contracts.ts. Only the
 * small transitions exercised by this gate exist; unknown operations fail shut.
 */
import { createFixtureApi } from '../localization/fixture-api.mjs';

export const ORIGIN = 'http://127.0.0.1:4175';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const copy = value => structuredClone(value);
const canonical = value => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
const keysAre = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).every(key => keys.includes(key));
const wakeEvents = new Set(['app_open', 'user_interaction', 'chat_end', 'work_completed']);
const signalKeys = ['visible', 'typing', 'work_running', 'awaiting_approval', 'mock_interview', 'quiet', 'failure'];
const time = 1792576800;
export function initialSnapshot() {
  return {
    identity: '11111111-1111-4111-8111-111111111111', revision: 7, persona_version: 'companion-v1',
    settings: { enabled: true, proactivity: 'default', hide_status: false },
    state: {
      interests: ['paper_boat', 'cloud_shapes'],
      activity: { id: '22222222-2222-4222-8222-222222222222', template_id: 'paper_boat', title: '折一只纸小船',
        status: 'waiting_shared', step_index: 0, step_count: 3, step_title: '挑一张想象中的彩纸',
        started_at: time, updated_at: time, participants: ['companion', 'user'], promise: '等你一起选颜色' },
      wish: null,
    },
    recent_events: [],
    shared_participation: [
      { id: 'prior-completed', activity_id: '33333333-3333-4333-8333-333333333333', status: 'completed', promise: 'Earlier synthetic shared detail', created_at: time - 100, completed_at: time - 90 },
      { id: 'current-promise', activity_id: '22222222-2222-4222-8222-222222222222', status: 'promised', promise: '等你一起选颜色', created_at: time, completed_at: null },
    ],
    presence: { session_id: 'synthetic-session', session_quiet: false, active: true, blocked: false, blockers: [] },
  };
}

export function createCompanionFixture() {
  const base = createFixtureApi({ retainedConversation: true, configuredAi: true });
  const state = initialSnapshot();
  const requests = [], violations = [], receipts = new Map(), clients = new Map(), permits = new Map();
  let dropEvent, holdEvent, bidSent = false, execution = null, pending = null;
  const answer = (data, status = 200) => ({ status, data: copy(data) });
  const reject = (reason, status = 422, violation = true) => {
    if (violation) violations.push(reason);
    return answer({ error_code: reason, detail: reason }, status);
  };
  const presence = () => {
    const active = [...clients.values()].filter(client => client.visible);
    const blockers = signalKeys.filter(key => key !== 'visible' && active.some(client => client[key]));
    return { session_id: 'synthetic-session', session_quiet: false, active: active.length > 0,
      blocked: active.length === 0 || blockers.length > 0, blockers };
  };
  function handle(method, url, body, headers = {}) {
    const parsed = new URL(url, ORIGIN), pathname = parsed.pathname;
    const request = { method, path: pathname, query: parsed.search, body: copy(body), index: requests.length + 1 };
    requests.push(request);
    const finish = result => { request.status = result.status; request.receipt = result.data?.receipt; request.dropResponse = !!result.dropResponse; request.replayed = !!result.replayed; return result; };
    if (parsed.origin !== ORIGIN || parsed.username || parsed.password) return finish(reject('external_origin', 501));
    if (Object.keys(headers).some(key => ['authorization', 'proxy-authorization', 'cookie'].includes(key.toLowerCase()))) return finish(reject('unexpected_credentials', 501));
    if (!pathname.startsWith('/api/companion')) {
      if (method !== 'GET') return finish(reject(`forbidden_write:${method}:${pathname}`, 501));
      let result;
      if (pathname === '/api/chat/conversations/401/execution') {
        result = answer({ execution });
        request.execution = copy(execution);
      }
      else {
        result = base.handle(method, parsed.href, body);
        if (result.status === 501) violations.push(`unmocked_read:${pathname}`);
        if (pathname === '/api/chat/conversations' && result.status === 200) result = answer(result.data.map(item => ({ ...item, pending_action: pending })));
      }
      return finish(result);
    }
    // Read-only default persona for the authored-name hook/settings shell.
    // This life gate does not grant persona mutations or model preview calls.
    if (method === 'GET' && pathname === '/api/companion/persona' && !parsed.search) {
      const result = base.handle(method, parsed.href, body);
      return finish(answer(result.data, result.status));
    }
    if (method === 'GET' && pathname === '/api/companion' && !parsed.search) return finish(answer(state));
    if (method === 'POST' && pathname === '/api/companion/presence' && !parsed.search) {
      if (!keysAre(body, ['identity', 'client_id', 'sequence', ...signalKeys]) || body.identity !== state.identity
        || !UUID.test(body.client_id) || !Number.isSafeInteger(body.sequence) || body.sequence < 1
        || !signalKeys.every(key => typeof body[key] === 'boolean')) return finish(reject('invalid_presence'));
      if ((clients.get(body.client_id)?.sequence ?? -1) < body.sequence) clients.set(body.client_id, copy(body));
      state.presence = presence();
      return finish(answer(state.presence));
    }
    if (method === 'POST' && pathname === '/api/companion/bid' && !parsed.search) {
      if (!keysAre(body, ['identity', 'expected_revision', 'client_id']) || !UUID.test(body?.client_id)
        || body.identity !== state.identity || !Number.isSafeInteger(body.expected_revision)) return finish(reject('invalid_bid'));
      if (body.expected_revision !== state.revision) return finish(reject('stale_bid', 409, false));
      const eligible = !bidSent && state.settings.enabled && state.settings.proactivity !== 'off'
        && !state.settings.hide_status && !state.presence.blocked;
      if (eligible) bidSent = true;
      return finish(answer({ item: eligible ? { id: 'synthetic-bid', kind: 'companion_life', result_text: 'Synthetic committed activity only',
        source_event_type: 'activity_progressed', source_identity: state.identity, source_revision: state.revision } : null }));
    }
    const isEvent = method === 'POST' && pathname === '/api/companion/events';
    const isSettings = method === 'PUT' && pathname === '/api/companion/settings';
    if ((!isEvent && !isSettings) || parsed.search) return finish(reject(`forbidden_companion_path:${method}:${pathname}`, 501));
    if (!keysAre(body, ['identity', 'expected_revision', 'command_id', ...(isEvent ? ['event', 'activity_id', 'wish_id'] : ['settings'])])
      || !UUID.test(body.identity) || !UUID.test(body.command_id) || !Number.isSafeInteger(body.expected_revision)
      || body.expected_revision < 0) return finish(reject('invalid_envelope'));
    const action = isEvent ? body.event : 'settings';
    const digest = canonical({ method, pathname, body });
    if (receipts.has(body.command_id)) {
      const receipt = receipts.get(body.command_id);
      return finish(receipt.digest === digest ? { ...copy(receipt.result), replayed: true } : reject('command_id_conflict', 409));
    }
    if (!wakeEvents.has(action) && !['pause', 'resume', 'withdraw_shared', 'complete_shared', 'settings'].includes(action)) return finish(reject(`forbidden_event:${action}`, 501));
    // Even a stale write must be expected by the scenario. Otherwise an
    // accidental write could hide behind an ordinary CAS rejection.
    if (!wakeEvents.has(action) && !(permits.get(action) > 0)) return finish(reject(`unarmed_command:${action}`, 501));
    if (body.identity !== state.identity || body.expected_revision !== state.revision) return finish(reject('stale_revision', 409, false));
    if (isEvent && (body.activity_id !== state.state.activity.id || body.wish_id !== undefined)) return finish(reject('activity_binding_mismatch'));
    if (isEvent && !wakeEvents.has(action) && !state.settings.enabled) return finish(reject('companion_disabled', 409, false));
    if (!wakeEvents.has(action)) permits.set(action, permits.get(action) - 1);
    const activity = state.state.activity;
    let changed = false;
    if (isSettings) {
      const settings = body.settings;
      if (!keysAre(settings, ['enabled', 'proactivity', 'hide_status']) || typeof settings.enabled !== 'boolean'
        || typeof settings.hide_status !== 'boolean' || !['off', 'less', 'default'].includes(settings.proactivity)) return finish(reject('invalid_settings'));
      changed = canonical(state.settings) !== canonical(settings);
      state.settings = copy(settings);
    } else if (action === 'pause') {
      if (!['active', 'waiting_shared'].includes(activity.status)) return finish(reject('invalid_pause'));
      activity.paused_from = activity.status; activity.status = 'paused'; changed = true;
    } else if (action === 'resume') {
      if (activity.status !== 'paused') return finish(reject('invalid_resume'));
      activity.status = activity.paused_from; delete activity.paused_from; changed = true;
    } else if (action === 'withdraw_shared' || action === 'complete_shared') {
      const paused = activity.status === 'paused' && activity.paused_from === 'waiting_shared';
      if (activity.status !== 'waiting_shared' && !(action === 'withdraw_shared' && paused)) return finish(reject('no_live_shared_step'));
      const promise = state.shared_participation.find(item => item.activity_id === activity.id && item.status === 'promised');
      if (!promise) return finish(reject('missing_promised_record'));
      promise.status = action === 'withdraw_shared' ? 'abandoned' : 'completed';
      promise.completed_at = action === 'complete_shared' ? time + state.revision : null;
      if (paused) activity.paused_from = 'active'; else activity.status = 'active';
      // The explicit UI control acknowledges participation; it does not itself
      // advance the template step. Withdrawal alone removes the participant.
      activity.promise = null;
      if (action === 'withdraw_shared') activity.participants = ['companion'];
      changed = true;
    }
    if (changed) {
      state.revision += 1;
      if (isEvent) {
        activity.updated_at = time + state.revision;
        const eventType = { pause: 'activity_paused', resume: 'activity_resumed', withdraw_shared: 'shared_withdrawn', complete_shared: 'shared_completed' }[action];
        state.recent_events.push({ id: body.command_id, revision: state.revision, category: 'actual', event_type: eventType,
          activity_id: activity.id, text: `Synthetic ${eventType}`, occurred_at: activity.updated_at, source: 'user_command' });
      }
    }
    const result = answer({ ...state, changed, receipt: { command_id: body.command_id, revision: state.revision } });
    receipts.set(body.command_id, { digest, result: copy(result) });
    if (dropEvent === action) { dropEvent = undefined; result.dropResponse = true; }
    if (holdEvent === action) { holdEvent = undefined; result.holdResponse = true; }
    return finish(result);
  }
  return {
    handle, requests, violations, snapshot: () => copy(state), receipts: () => [...receipts.values()].map(item => copy(item.result.data)),
    permit(action) { permits.set(action, (permits.get(action) ?? 0) + 1); },
    dropNextResponse(action) { dropEvent = action; }, holdNextResponse(action) { holdEvent = action; },
    // Read-only synthetic job state enters the real conversation/execution hooks.
    setWork(mode) {
      if (!['idle', 'running', 'approval', 'cleared'].includes(mode)) throw new Error(`Unknown fixture work mode: ${mode}`);
      pending = mode === 'approval' ? { operation_id: 'synthetic-operation', tool_name: 'update_application', human: 'Synthetic approval: update Example Labs',
        confirmation_token: 'synthetic-not-a-credential', args: { application_id: 101, status: 'interview' } } : null;
      execution = mode === 'idle' ? null : { turn_id: mode === 'cleared' ? 'synthetic-cleared-turn' : 'synthetic-work-turn', conversation_id: 401,
        execution_generation: 1, state: mode === 'approval' ? 'waiting_confirmation' : mode === 'cleared' ? 'completed' : 'running' };
    },
  };
}
