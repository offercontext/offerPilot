/** Isolated synthetic extension of the localization fixture. Never calls a backend. */
import { createFixtureApi, sampleApplication, sampleEvent, sampleMail } from '../localization/fixture-api.mjs';

export const knowledgeTitle = '合成已确认知识：性能排查';
export const referenceTitle = '合成外部参考：性能测量指南';
export const frozenEvidence = '合成冻结依据：先测量热点，再验证最小改动。';
export const allowedWrites = new Set([
  'PATCH /api/onboarding', 'PUT /api/settings', 'POST /api/settings/providers/test',
  'POST /api/applications', 'POST /api/knowledge/notes/7/lifecycle',
  'POST /api/interview-stories', 'POST /api/job-mail/suggestions/mail-fixture-1/preview',
]);
const copy = value => structuredClone(value);
const answer = (data, status = 200) => ({ status, data: copy(data) });
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const presenceSignals = ['visible', 'typing', 'work_running', 'awaiting_approval', 'mock_interview', 'quiet', 'failure'];
const presenceKeys = new Set(['identity', 'client_id', 'sequence', ...presenceSignals]);
const isContentFreePresence = body => Boolean(body && typeof body === 'object' && !Array.isArray(body)
  && Object.keys(body).length === presenceKeys.size && Object.keys(body).every(key => presenceKeys.has(key))
  && typeof body.identity === 'string' && uuid.test(body.identity)
  && typeof body.client_id === 'string' && uuid.test(body.client_id)
  && Number.isSafeInteger(body.sequence) && body.sequence >= 1
  && presenceSignals.every(key => typeof body[key] === 'boolean'));
// Shared by fixture admission, browser write accounting and its full-request
// guard. Malformed/lookalike presence remains a write attempt, never invisible.
export const isContentFreePresenceRequest = request => request.method === 'POST'
  && request.path === '/api/companion/presence' && request.query === ''
  && isContentFreePresence(request.body);
export const isDomainWriteAttempt = request => request.method !== 'GET' && !isContentFreePresenceRequest(request);

export function createSimplificationFixture({ firstUse = false, today = false, materials = false } = {}) {
  const base = createFixtureApi();
  const requests = [], unexpected = [], controls = new Map(), pending = new Set();
  const now = Math.floor(Date.now() / 1000);
  const stamp = new Date(now * 1000).toISOString();
  const applications = firstUse ? [] : [copy(sampleApplication)];
  const events = firstUse ? [] : [{ ...copy(sampleEvent), scheduled_at: new Date((now + 86400) * 1000).toISOString() }];
  const settings = {
    version: 'simplification-synthetic-only', data_dir: 'Synthetic fixture; no persistent data',
    chat_auto_approve_writes: false, active_provider_id: 'one', fallback_provider_ids: ['two'],
    base_url: 'https://one.example.invalid/v1', model: 'model-one', has_api_key: !firstUse,
    runtime_mode: 'local', auth_enabled: false, has_auth_token: false, log_level: 'INFO',
    providers: ['one', 'two'].map(id => ({ id, label: `Synthetic ${id}`, provider: 'openai_compatible',
      base_url: `https://${id}.example.invalid/v1`, model: `model-${id}`, context_window: 16000,
      max_output_tokens: 4000, enabled: true, supports_json_schema: false, has_api_key: !firstUse })),
  };
  const onboarding = {
    steps: { configure_ai: !firstUse, create_primary_resume: !firstUse, create_first_application: !firstUse, send_first_pilot_message: !firstUse },
    completed_count: firstUse ? 0 : 4, is_complete: !firstUse, force_open: false, dismissed: false,
  };
  let note = { id: 7, title: knowledgeTitle, origin_kind: 'confirmed_interview_capture', version_id: 21,
    version_number: 2, archived: false, source_id: 31, source_status: 'source_changed', captured_at: stamp,
    capture_metadata: { source_id: 31, origin_note_id: 41, application_event_id: 201,
      note_fingerprint: 'synthetic-confirmed-capture', capture_schema_version: 'interview-note-capture-v1', captured_at: stamp },
    content: { title: knowledgeTitle, blocks: [{ block_id: 'b1', text: '合成已确认正文，不是未确认草稿。', evidence_refs: [{ fragment_id: 'f1', excerpt: frozenEvidence }] }] },
    evidence: [{ id: 'e1', path: '/questions', excerpt: frozenEvidence, char_start: 0, char_end: frozenEvidence.length,
      line_start: 1, line_end: 2, frozen_at: stamp }],
  };
  const reference = { id: 32, source_kind: 'markdown', title: referenceTitle, display_title: referenceTitle,
    title_hint: '', author: '', published_at: null, main_filename: 'synthetic-performance-guide.md',
    main_media_type: 'text/markdown', total_bytes: 128, token_count: 32, lifecycle: 'active',
    extraction_status: 'extracted', extraction_error_code: '', extraction_error_message: '',
    brief_status: 'not_started', brief_block_reason: '', brief_error_code: '', brief_error_message: '',
    active_snapshot_id: 42, archived_at: null, created_at: stamp, updated_at: stamp,
    provenance: { captured_at: stamp, metadata_extraction_version: 'synthetic-v1' } };
  const receipts = new Map(), mutations = new Set(), stories = [];
  const candidate = { id: 'fixture-written-test', subject_type: 'application_event', subject_id: 202,
    reason_code: 'event_upcoming', source_revision: 'synthetic-1', application_id: 101, event_id: 202,
    event_type: 'written_test', offer_id: null, company: '合成笔试公司', position: '合成工程师',
    due_at: now + 3600, display_from: now - 3600, display_until: now + 7200, delivery_due_at: now + 3600,
    delivery_expires_at: now + 7200, updated_at: now, deadline_label: '合成笔试截止' };
  const job = { application_id: 101, event_id: 201, reason: 'synthetic only', due_at: now - 60,
    created_at: now - 120, published_at: now - 30, turn_id: '', execution_generation: 1, error_code: '' };
  const jobs = today ? [
    { ...job, id: 'same-candidate', kind: 'deadline', candidate_id: candidate.id, state: 'succeeded', result_text: '合成重复提醒不应再显示' },
    { ...job, id: 'old-reminder', kind: 'deadline', candidate_id: 'old-candidate', state: 'succeeded', result_text: '合成历史提醒' },
    { ...job, id: 'ready-draft', kind: 'interview_draft', state: 'succeeded', result_text: '合成待查看准备草稿' },
    { ...job, id: 'unknown-result', kind: 'interview_draft', state: 'result_unknown', result_text: '', error_code: 'runtime_result_requires_reconciliation' },
  ] : [];

  function holdNext(method, pathname) {
    const key = `${method} ${pathname}`;
    if (!allowedWrites.has(key)) throw new Error(`Cannot authorize an unknown write with a hold: ${key}`);
    if (controls.has(key)) throw new Error(`Hold already queued: ${key}`);
    let release;
    const promise = new Promise(resolve => { release = resolve; });
    const control = { promise, release: result => { pending.delete(control); release(result); } };
    controls.set(key, control); pending.add(control);
    return control.release;
  }
  async function handle(method, url, body) {
    const parsed = new URL(url, 'http://127.0.0.1');
    const p = parsed.pathname, key = `${method} ${p}`;
    const request = { method, path: p, query: parsed.search, body: body === undefined ? null : copy(body), at: Date.now() };
    requests.push(request);
    // Disabled life still emits these content-free UI signals. Permit only this
    // exact envelope/route to reach the base fixture, never companion commands
    // or domain/model writes (and never a hold that could arm either).
    const presenceOnly = isContentFreePresenceRequest(request);
    if (method !== 'GET' && !allowedWrites.has(key) && !presenceOnly) {
      unexpected.push({ method, path: p });
      return answer({ error_code: 'unmocked_test_request', detail: 'Strict synthetic fixture rejected write' }, 501);
    }
    const control = controls.get(key);
    if (control) {
      controls.delete(key);
      const result = await control.promise;
      if (result) return result;
    }
    if (method === 'GET') {
      if (p === '/api/settings') return answer(settings);
      if (p === '/api/onboarding') return answer(onboarding);
      if (p === '/api/applications') return answer(applications);
      if (p === '/api/application-events') return answer(events);
      if (p === '/api/dashboard') return answer({ total: applications.length, board: { interview: applications } });
      if (p === '/api/knowledge/notes') return answer({ items: materials && !firstUse ? [note] : [] });
      if (p === '/api/knowledge/sources') return answer(materials && !firstUse ? [reference] : []);
      if (p === '/api/knowledge/note-management') return answer({ items: [note] });
      if (p === '/api/interview-stories') return answer(stories);
      if (p === '/api/interview-story-sources') return answer({ resumes: [], interview_notes: [], mock_turns: [] });
      if (p === '/api/reminders/candidates') return answer({ items: today ? [candidate] : [] });
      if (p === '/api/proactive/jobs') return answer({ items: jobs });
      if (p === '/api/job-mail/suggestions') return answer({ items: today ? [sampleMail] : [], total: today ? 1 : 0, pending_count: today ? 1 : 0, has_more: false });
      if (p === '/api/interviews') return answer({ items: firstUse ? [] : [{ application_id: 101, event_id: 201,
        company_name: sampleApplication.company_name, position_name: sampleApplication.position_name, scheduled_at: events[0].scheduled_at,
        note_id: null, note_source_status: null, has_review_proposal: false, review_summary: null,
        has_confirmed_knowledge: false, preparation_available: true, event_status: 'scheduled', duration_minutes: 60,
        scheduled_at_state: 'present' }], next_cursor: null });
      if (p === '/api/applications/101/events/201/readiness-feedback') return answer({ schema_version: 1, application_id: 101, event_id: 201, items: [] });
      if (/^\/api\/applications\/(101|102)\/job-description$/.test(p)) return answer({ current: null });
      if (/^\/api\/applications\/(101|102)\/(job-description\/versions|notes|opportunity-fit-reviews)$/.test(p)) return answer([]);
      if (/^\/api\/applications\/(101|102)\/material-kit$/.test(p)) return answer({ error_code: 'not_found' }, 404);
    }
    if (key === 'PATCH /api/onboarding') {
      if (typeof body?.force_open !== 'boolean') return answer({ error: 'force_open required' }, 422);
      onboarding.force_open = body.force_open; onboarding.dismissed = !body.force_open;
      return answer(onboarding);
    }
    if (key === 'PUT /api/settings') {
      for (const field of ['providers', 'active_provider_id', 'fallback_provider_ids', 'base_url', 'model', 'chat_auto_approve_writes']) {
        if (body[field] !== undefined) settings[field] = copy(body[field]);
      }
      settings.providers = settings.providers.map(provider => ({ ...provider, has_api_key: !firstUse }));
      return answer(settings);
    }
    if (key === 'POST /api/settings/providers/test') return answer({ ok: true, message: 'Synthetic connection success; no provider contacted' });
    if (key === 'POST /api/applications') {
      if (!body?.idempotency_key || !body.company_name || !body.position_name) return answer({ error: 'invalid fixture create' }, 422);
      if (!receipts.has(body.idempotency_key)) {
        const value = { ...sampleApplication, ...copy(body), id: 102, status: body.status || 'pending', jd_version_id: null, created_at: stamp, updated_at: stamp };
        applications.push(value); receipts.set(body.idempotency_key, value);
        onboarding.steps.create_first_application = true; onboarding.completed_count = Object.values(onboarding.steps).filter(Boolean).length;
      }
      return answer(receipts.get(body.idempotency_key), 201);
    }
    if (key === 'POST /api/knowledge/notes/7/lifecycle') {
      if (!body?.mutation_id || body.confirmed !== true || body.action !== 'revise') return answer({ error: 'invalid fixture mutation' }, 422);
      if (!mutations.has(body.mutation_id)) {
        note = { ...note, title: body.title, version_id: note.version_id + 1, version_number: note.version_number + 1,
          content: { title: body.title, blocks: copy(body.blocks) } };
        mutations.add(body.mutation_id);
      }
      return answer({ ok: true });
    }
    if (key === 'POST /api/interview-stories') {
      if (!body?.idempotency_key || !body.content?.title) return answer({ error: 'invalid fixture story' }, 422);
      if (!receipts.has(body.idempotency_key)) {
        const story = { id: 51, title: body.content.title, status: 'active', current_version_id: 61,
          story_revision: 1, version_number: 1, source_states: [] };
        stories.push(story); receipts.set(body.idempotency_key, story);
      }
      return answer(receipts.get(body.idempotency_key));
    }
    const result = base.handle(method, url, body);
    if (result.status === 501) unexpected.push({ method, path: p });
    return result;
  }
  return { handle, requests, unexpected, holdNext, releaseAll: () => {
    for (const control of pending) control.release(answer({ error_code: 'qa_cancelled' }, 503));
    controls.clear();
  }, snapshot: () => copy({ settings, onboarding, applications, events, note, stories }) };
}
