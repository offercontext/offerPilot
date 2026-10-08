'use strict';
const { TOKEN_HEADER, isSameOrigin } = require('./lifecycle.cjs');
const CHANNEL = 'offerpilot:haru:';
const TASKS = new Set(['idle', 'running', 'waiting_confirmation', 'completed', 'failed']);
const text = (value, limit) => typeof value === 'string' ? value.slice(0, limit) : '';

// Build a new, bounded DTO. Never forward controllers, config, pending action
// payloads, confirmation tokens, or an arbitrary renderer object across windows.
function sanitizeSnapshot(value) {
  if (!value || !Number.isSafeInteger(value.version) || value.version < 1 || !TASKS.has(value.taskState)) return null;
  return {
    version: value.version,
    conversationId: Number.isSafeInteger(value.conversationId) && value.conversationId > 0 ? value.conversationId : null,
    taskState: value.taskState,
    messages: Array.isArray(value.messages) ? value.messages.slice(-12).filter(item => item && ['user', 'assistant'].includes(item.role)).map(item => ({ role: item.role, content: text(item.content, 12000) })) : [],
    contextLabel: text(value.contextLabel, 400),
    loading: value.loading === true,
    hasPending: value.hasPending === true,
    canSend: value.canSend === true,
    canStop: value.canStop === true,
    stopping: value.stopping === true,
    error: text(value.error, 1200),
    stopMessage: text(value.stopMessage, 500),
  };
}
function validateRequest(value) {
  if (!value || !Number.isSafeInteger(value.version) || value.version < 1) return null;
  if (value.action === 'send' && typeof value.text === 'string' && value.text.trim() && value.text.length <= 16000) {
    return { action: 'send', version: value.version, text: value.text };
  }
  if (['stop', 'open-pending'].includes(value.action)) return { action: value.action, version: value.version };
  return null;
}
function isTrustedFrame(event, contents, origin) {
  return Boolean(contents && !contents.isDestroyed() && event.sender === contents
    && event.senderFrame === contents.mainFrame && isSameOrigin(event.senderFrame?.url, origin));
}
function allowHaruResource(details, origin) {
  if (!isSameOrigin(details.url, origin) || !['GET', 'HEAD'].includes(details.method) || details.resourceType === 'subFrame') return false;
  const url = new URL(details.url);
  // Encoded separators and dot segments must never turn a static-looking path
  // into a backend route after URL decoding in the HTTP server.
  if (url.pathname.includes('%') || url.pathname.split('/').some(part => part === '.' || part === '..')) return false;
  if (url.pathname === '/') return details.resourceType === 'mainFrame' && url.search === '?desktopSurface=haru';
  return !url.search && /^\/(?:assets|live2d)\/[A-Za-z0-9_./-]+$/.test(url.pathname);
}
function authenticatedHeaders(details, { origin, token, role, ownerContents, trustedContents }) {
  const headers = { ...details.requestHeaders };
  for (const name of Object.keys(headers)) if (name.toLowerCase() === TOKEN_HEADER.toLowerCase()) delete headers[name];
  const trusted = [...trustedContents].find(contents => !contents.isDestroyed() && contents.id === details.webContentsId);
  const allowed = role === 'owner' ? trusted && trusted === ownerContents && isSameOrigin(details.url, origin)
    : trusted && trusted !== ownerContents && allowHaruResource(details, origin);
  if (allowed) headers[TOKEN_HEADER] = token;
  return headers;
}
function clampBounds(bounds, workAreas) {
  const areas = workAreas.filter(area => [area.x, area.y, area.width, area.height].every(Number.isFinite) && area.width > 0 && area.height > 0);
  if (!areas.length) throw new Error('No available screen work area');
  const preferred = areas[0];
  const width = Number.isFinite(bounds?.width) ? Math.max(160, Math.min(520, Math.round(bounds.width))) : 260;
  const height = Number.isFinite(bounds?.height) ? Math.max(200, Math.min(820, Math.round(bounds.height))) : 340;
  const x = Number.isFinite(bounds?.x) ? Math.round(bounds.x) : preferred.x + preferred.width - width - 24;
  const y = Number.isFinite(bounds?.y) ? Math.round(bounds.y) : preferred.y + preferred.height - height - 24;
  const overlap = area => Math.max(0, Math.min(x + width, area.x + area.width) - Math.max(x, area.x)) * Math.max(0, Math.min(y + height, area.y + area.height) - Math.max(y, area.y));
  const distance = area => (x + width / 2 - area.x - area.width / 2) ** 2 + (y + height / 2 - area.y - area.height / 2) ** 2;
  const area = [...areas].sort((a, b) => overlap(b) - overlap(a) || distance(a) - distance(b))[0];
  const fittedWidth = Math.min(width, area.width);
  const fittedHeight = Math.min(height, area.height);
  return { x: Math.max(area.x, Math.min(x, area.x + area.width - fittedWidth)), y: Math.max(area.y, Math.min(y, area.y + area.height - fittedHeight)), width: fittedWidth, height: fittedHeight };
}
module.exports = { CHANNEL, sanitizeSnapshot, validateRequest, isTrustedFrame, clampBounds, allowHaruResource, authenticatedHeaders };
