'use strict';
// Read-only local data for the unmodified AppShell. No provider, persistence,
// credentials, generic route fallback, write, or confirmation endpoint exists.
const conversation = { id: 101, title: '原生窗口合成会话', mode: 'general', context_type: 'workspace', context_ref: null,
  context_label: '原生窗口合成上下文', archived_at: null, pinned_at: null, pending_action: null,
  created_at: '2026-10-12T00:00:00Z', updated_at: '2026-10-12T00:00:00Z' };
const message = { id: 1, conversation_id: 101, role: 'assistant', content: '【本地合成数据】窗口恢复应保留这条会话。', created_at: '2026-10-12T00:00:00Z' };
const responses = new Map([
  ['/api/auth/status', { auth_enabled: false, authenticated: true }],
  ['/api/applications', []], ['/api/application-events', []], ['/api/offers', []], ['/api/resumes', []],
  ['/api/knowledge/notes', { items: [] }],
  ['/api/questions/stats', { total: 0, new: 0, practicing: 0, mastered: 0, due: 0, today_reviews: 0, streak_days: 0 }],
  ['/api/onboarding', { steps: { configure_ai: true, create_primary_resume: true, create_first_application: true, send_first_pilot_message: true }, completed_count: 4, is_complete: true, force_open: false }],
  ['/api/settings', { version: 'synthetic-window-test', data_dir: '', chat_auto_approve_writes: false,
    active_provider_id: '', fallback_provider_ids: [], providers: [], base_url: '', model: '',
    // UI-only flag enables draft editing. There is no API key or provider, and
    // every POST is rejected. Native validation must never press Send.
    has_api_key: true, runtime_mode: 'local', auth_enabled: false, has_auth_token: false, log_level: 'INFO' }],
  ['/api/chat/conversations', [conversation]], ['/api/chat/conversations/101', [message]],
  ['/api/chat/conversations/101/execution', { execution: null }],
  ['/api/chat/conversations/101/readiness-context', { schema_version: 1, state: 'not_applicable', conversation_id: 101,
    application_id: 0, target_event_id: null, resume_id: null, ordered_version_ids: [], selection_fingerprint: '', scope_revision: 0, revision: 0 }],
  ['/api/chat/conversations/101/timeline', { schema_version: 1, conversation_id: 101, mode: 'snapshot', high_watermark: 1,
    items: [{ schema_version: 1, item_id: 'turn:fixture-1:message:1', turn_id: 'fixture-1', conversation_id: 101, item_type: 'assistant_message',
      source_refs: ['message:1'], source_revision: '1', revision: 1, display_revision: 1, ordinal: 1, change_seq: 1,
      payload_digest: 'fixture-only', deleted: false, payload: { schema_version: 1, item_id: 'message:1', kind: 'assistant_message',
        message_id: 1, operation_id: null, content: message.content, action: null } }], next_cursor: null, cursor: 'fixture-end' }],
]);
function fixtureResponse(method, rawURL) {
  const url = new URL(rawURL, 'http://127.0.0.1');
  if (method !== 'GET' || !responses.has(url.pathname)) return null;
  // Reject unknown parameters. Known read-only product list filters are allowed.
  const allowed = url.pathname === '/api/chat/conversations' ? ['include_archived']
    : url.pathname.endsWith('/timeline') ? ['limit', 'cursor']
    : url.pathname === '/api/application-events' ? ['month', 'application_id', 'event_type']
    : url.pathname === '/api/applications' ? ['status'] : [];
  if ([...url.searchParams.keys()].some(key => !allowed.includes(key))) return null;
  return structuredClone(responses.get(url.pathname));
}
module.exports = { fixtureResponse, allowedApiPaths: [...responses.keys()] };
