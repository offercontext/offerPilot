/** Synthetic only: no server, provider, credentials or external requests. */
import { createHash } from 'node:crypto';
import { createSimplificationFixture } from '../simplification/fixture-api.mjs';
// Opaque 160×96 four-color synthetic card, generated with PNG/zlib; no real user data.
export const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAKAAAABgCAIAAAAVRe7OAAABF0lEQVR4nO3RsQnCUAAA0UwigmvoCraZwCa7pbLKOA5hndQWvwzC4+ANcHDT5XYPbPp7QU7VYFyDcT+DX/OXd32vvAbjGoxrMK7BuAbjGoxrMK7BuAbjGoxrMK7BuAbjGoxrMK7BuAbjGoxrMK7BuAbjGoxrMK7BuAbjGoxrMK7BuAbjGoxrMK7BuAbjGoxrMK7BuAbjGoxrMK7BuAbjGoxrMG44eN8evM9z4TUY12Bcg3ENxjUY12Bcg3ENxjUY12Bcg3ENxjUY12Bcg3ENxjUY12Bcg3ENxjUY12Bcg3ENxjUY12Bcg3ENxjUY12Bcg3ENxjUY12Bcg3ENxjUY12Bcg3ENxjUY12Bcg3HDwfE0GNdgXINxB8c57QYoBt9sAAAAAElFTkSuQmCC', 'base64');
export const image = { id: 'img_0123456789abcdef0123456789abcdef', sha256: createHash('sha256').update(png).digest('hex'), mime_type: 'image/png', size_bytes: png.length, width: 160, height: 96, normalization_version: 'image-normalize-v1' };
const answer = (data, status = 200) => ({ data, status });
export function createImageFixture({ vision = true } = {}) {
  const base = createSimplificationFixture();
  const requests = [], unexpected = [], messages = [];
  let rejectNext = true;
  const conversation = { id: 401, title: 'Synthetic image conversation', title_source: 'manual', mode: 'general', context_type: 'workspace', context_ref: '', created_at: '2026-10-23T00:00:00Z', updated_at: '2026-10-23T00:00:00Z' };
  const timeline = () => ({ schema_version: 1, conversation_id: 401, mode: 'snapshot', high_watermark: messages.length, cursor: `synthetic-${messages.length}`, next_cursor: null,
    items: messages.map((message, i) => ({ schema_version: 1, conversation_id: 401, turn_id: 'synthetic-image', item_id: `turn:synthetic-image:message:${message.id}`,
      item_type: `${message.role}_message`, source_refs: [`message:${message.id}`], source_revision: '1', revision: 1, display_revision: 1, ordinal: i + 1,
      change_seq: i + 1, payload_digest: `synthetic-${message.id}`, deleted: false,
      payload: { schema_version: 1, item_id: `message:${message.id}`, message_id: message.id, operation_id: null, kind: `${message.role}_message`, content: message.content, image_attachments: message.image_attachments, action: null } })) });
  async function handle(method, url, body) {
    const p = new URL(url, 'http://localhost').pathname;
    requests.push({ method, path: p, ...(body === undefined ? {} : { body }) });
    if (method === 'POST' && p === '/api/chat/attachments') return answer(image, 201);
    if (method === 'GET' && p === `/api/chat/attachments/${image.id}`) return { status: 200, bytes: png, contentType: 'image/png' };
    if (method === 'POST' && p === '/api/pilot/runtime/v1/turns') {
      if (rejectNext) { rejectNext = false; return answer({ error: 'Synthetic admission rejected', error_code: 'synthetic_rejection' }, 422); }
      if (!body?.request_id || (!body.message && !body.image_attachment_ids?.length)) return answer({ error: 'Invalid synthetic submission' }, 422);
      messages.push({ id: 501 + messages.length, conversation_id: 401, role: 'user', content: body.message, image_attachments: body.image_attachment_ids?.includes(image.id) ? [image] : [] });
      messages.push({ id: 501 + messages.length, conversation_id: 401, role: 'assistant', content: 'Synthetic accepted answer' });
      return answer({ protocol_version: 'pilot-runtime-v1', turn_id: 'synthetic-image', conversation_id: 401, execution_generation: 1, state: 'completed', terminal: { response: { type: 'message', conversation_id: 401, message: 'Synthetic accepted answer' } } });
    }
    if (method === 'GET') {
      if (p === '/api/chat/image-capability') return answer({ enabled: vision, reason: vision ? '' : 'Synthetic model does not accept images', provider_id: 'one', model: 'synthetic', max_images: 4, max_file_bytes: 10485760, max_total_bytes: 20971520, accepted_mime_types: ['image/png', 'image/jpeg', 'image/webp'] });
      if (p === '/api/chat/conversations') return answer(messages.length ? [conversation] : []);
      if (p === '/api/chat/conversations/401') return answer(messages);
      if (p === '/api/chat/conversations/401/timeline') return answer(timeline());
      if (p === '/api/chat/conversations/401/execution') return answer({ execution: null });
      if (p === '/api/chat/conversations/401/readiness-context') return answer({ schema_version: 1, state: 'not_applicable', conversation_id: 401, application_id: 0, target_event_id: null, resume_id: null, ordered_version_ids: [], selection_fingerprint: '', scope_revision: 1, revision: 1 });
      if (/^\/api\/pilot\/runtime\/v1\/requests\//.test(p)) return answer({ error: 'not_found' }, 404);
      const result = await base.handle(method, url, body);
      if (p === '/api/settings') result.data.image_capability = { enabled: vision, reason: vision ? '' : 'Synthetic model does not accept images', provider_id: 'one', model: 'synthetic', max_images: 4, max_file_bytes: 10485760, max_total_bytes: 20971520, accepted_mime_types: ['image/png', 'image/jpeg', 'image/webp'] };
      if (result.status === 501) unexpected.push({ method, path: p });
      return result;
    }
    unexpected.push({ method, path: p });
    return answer({ error: 'Non-allowlisted synthetic request' }, 501);
  }
  return { handle, requests, unexpected, acceptNext: () => { rejectNext = false; } };
}
