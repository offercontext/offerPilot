/** In-memory layout transport. Reads, content-free presence, and the two
 * observed lifecycle wake commands only; no backend, database, or provider. */
import { createCompanionFixture, ORIGIN as COMPANION_ORIGIN } from '../companion/fixture-api.mjs';
import { isContentFreePresenceRequest } from '../simplification/fixture-api.mjs';
export const TASK_PANEL_ORIGIN = 'http://127.0.0.1:5174';
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const wakeKeys = new Set(['identity', 'expected_revision', 'command_id', 'event', 'activity_id']);
export function createTaskPanelFixture() {
  const companion = createCompanionFixture();
  // Layout exercises enabled life without unsolicited invitations. Configure
  // only this in-memory instance through the existing settings transition;
  // the browser transport below never admits PUT/settings or bid requests.
  const initial = companion.snapshot();
  companion.permit('settings');
  const configured = companion.handle('PUT', `${COMPANION_ORIGIN}/api/companion/settings`, {
    identity: initial.identity, expected_revision: initial.revision,
    command_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    settings: { ...initial.settings, enabled: true, proactivity: 'off' },
  });
  if (configured.status !== 200 || configured.data.settings.proactivity !== 'off') {
    throw new Error('Could not initialize the synthetic non-inviting layout state');
  }
  function handle(method, url, body) {
    const parsed = new URL(url, TASK_PANEL_ORIGIN);
    if (parsed.origin !== TASK_PANEL_ORIGIN || parsed.username || parsed.password
      || parsed.search || parsed.hash) return undefined;
    const request = { method, path: parsed.pathname, query: parsed.search, body };
    const snapshot = companion.snapshot();
    const presence = isContentFreePresenceRequest(request) && body.identity === snapshot.identity;
    // Wake commands carry a CAS/receipt envelope and are not content-free
    // presence. Keep their allowlist independent from the presence exemption.
    const wake = method === 'POST' && parsed.pathname === '/api/companion/events'
      && body && typeof body === 'object' && !Array.isArray(body)
      && Object.keys(body).length === wakeKeys.size && Object.keys(body).every(key => wakeKeys.has(key))
      && typeof body.identity === 'string' && body.identity === snapshot.identity
      && typeof body.command_id === 'string' && uuid.test(body.command_id)
      && Number.isSafeInteger(body.expected_revision) && body.expected_revision >= 0
      && ['app_open', 'chat_end'].includes(body.event)
      && typeof body.activity_id === 'string' && body.activity_id === snapshot.state.activity.id;
    const read = method === 'GET' && ['/api/companion', '/api/companion/persona'].includes(parsed.pathname);
    if (read || presence || wake) {
      // This is an in-process fixture call, not a request to the helper origin.
      return companion.handle(method, new URL(parsed.pathname, COMPANION_ORIGIN).href, body);
    }
    if (method === 'GET' && ['/api/offers/7/negotiation/proposals', '/api/offers/comparison-dimensions', '/api/offers/7/comparison-values'].includes(parsed.pathname)) {
      return { status: 200, data: [] };
    }
    return undefined;
  }
  return { handle };
}
