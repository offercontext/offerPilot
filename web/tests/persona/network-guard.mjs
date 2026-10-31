import path from 'node:path';
export const ORIGIN = 'http://127.0.0.1:18763';
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const record = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const keys = (value, required, optional = []) => record(value) && required.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => [...required, ...optional].includes(key));
const integer = value => Number.isSafeInteger(value) && value >= 0;
const envelope = value => uuid.test(value.identity) && uuid.test(value.command_id) && integer(value.expected_revision);
const signals = ['visible', 'typing', 'work_running', 'awaiting_approval', 'mock_interview', 'quiet', 'failure'];
const fields = ['display_name', 'traits', 'speaking_style', 'verbosity', 'interests_text', 'fictional_background', 'additional_style_notes', 'enabled_activity_templates'];
const templates = ['paper_boat', 'cloud_catalog', 'paper_stars'];
function validSpec(value) {
  const limits = { display_name: 24, speaking_style: 200, interests_text: 300, fictional_background: 500, additional_style_notes: 500 };
  if (!keys(value, fields) || !Object.entries(limits).every(([key, limit]) => typeof value[key] === 'string' && [...value[key]].length <= limit)
    || !value.display_name.trim() || /[\n\t]/u.test(value.display_name)
    || !Array.isArray(value.traits) || value.traits.length > 5
    || !value.traits.every(item => typeof item === 'string' && item.trim().length > 0 && [...item].length <= 40 && !/[\n\t]/u.test(item))
    || new Set(value.traits.map(item => item.trim())).size !== value.traits.length
    || !['brief', 'balanced', 'detailed'].includes(value.verbosity)
    || !Array.isArray(value.enabled_activity_templates) || value.enabled_activity_templates.length > 3
    || !value.enabled_activity_templates.every(item => templates.includes(item))
    || new Set(value.enabled_activity_templates).size !== value.enabled_activity_templates.length) return false;
  const texts = [...Object.keys(limits).map(key => value[key]), ...value.traits];
  if (texts.some(text => /[\p{Cc}\p{Cf}\p{Cs}]/u.test(text.replace(/[\n\t]/g, '')))) return false;
  const encoded = JSON.stringify(value);
  return [...encoded].length <= 2000 && Buffer.byteLength(encoded, 'utf8') <= 6000;
}

const reads = new Set([
  '/api/auth/status', '/api/settings', '/api/onboarding', '/api/companion', '/api/companion/persona', '/api/companion/persona/revisions',
  '/api/logs', '/api/applications', '/api/application-events', '/api/calendar', '/api/dashboard', '/api/chat/conversations',
  '/api/questions/stats', '/api/context-policies', '/api/proactive/settings', '/api/proactive/jobs', '/api/reminders/candidates',
  '/api/job-mail/status', '/api/job-mail/secure-setup/status', '/api/job-mail/suggestions', '/api/resumes', '/api/offers', '/api/notes',
  '/api/questions', '/api/questions/due', '/api/confirmed-memory', '/api/knowledge/sources', '/api/knowledge/notes', '/api/interview-stories',
  '/api/interviews', '/api/interview-practice-cases', '/api/interview-practice/recommendations', '/api/interview-practice/plans',
]);
/** The real app uses these read-only mounts; every unknown route/write fails closed. */
export function classifyRequest(method, rawUrl, body, dist) {
  let url;
  try { url = new URL(rawUrl); } catch { return { blocked: 'invalid_url' }; }
  if (url.origin !== ORIGIN || url.username || url.password) return { blocked: 'external_origin_or_url_credentials' };
  if (url.pathname.startsWith('/api')) {
    if (method === 'GET' && reads.has(url.pathname)) return { api: true };
    if (method !== 'POST' || url.search) return { blocked: 'unapproved_api_method_or_route' };
    if (url.pathname === '/api/companion/persona/mutations') {
      const base = ['profile_id', 'expected_revision', 'command_id', 'confirmed', 'operation'];
      if (!record(body) || !uuid.test(body.profile_id) || !uuid.test(body.command_id) || !integer(body.expected_revision) || body.confirmed !== true) return { blocked: 'invalid_persona_command' };
      if (body.operation === 'update' && keys(body, [...base, 'spec']) && validSpec(body.spec)) return { api: true, mutation: true };
      if (body.operation === 'restore' && keys(body, [...base, 'target_revision']) && integer(body.target_revision)) return { api: true, mutation: true };
      return { blocked: 'invalid_persona_command' };
    }
    if (url.pathname === '/api/companion/presence' && keys(body, ['identity', 'client_id', 'sequence', ...signals]) && uuid.test(body.identity) && uuid.test(body.client_id) && integer(body.sequence) && signals.every(key => typeof body[key] === 'boolean')) return { api: true, contentFree: true };
    if (url.pathname === '/api/companion/events' && keys(body, ['identity', 'expected_revision', 'command_id', 'event']) && envelope(body) && ['app_open', 'chat_end', 'work_completed', 'user_interaction'].includes(body.event)) return { api: true, wake: true };
    if (url.pathname === '/api/companion/bid' && keys(body, ['identity', 'expected_revision', 'client_id']) && uuid.test(body.identity) && uuid.test(body.client_id) && integer(body.expected_revision)) return { api: true, contentFree: true };
    return { blocked: 'unapproved_api_write' };
  }
  if (method !== 'GET') return { blocked: 'non_get_asset' };
  let decoded;
  try { decoded = decodeURIComponent(url.pathname); } catch { return { blocked: 'invalid_asset_encoding' }; }
  if (decoded.includes('\\') || decoded.includes('\0') || decoded.split('/').includes('..')) return { blocked: 'unsafe_asset_path' };
  const filename = path.resolve(dist, decoded === '/' ? 'index.html' : decoded.slice(1));
  return filename.startsWith(path.resolve(dist) + path.sep) ? { filename } : { blocked: 'asset_escape' };
}
