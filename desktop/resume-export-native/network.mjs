// Only this startup route is an audited local read: AppShell's desktop owner
// mounts ChatPanel, which lists conversations through the local SQL repository.
export function classifyRequest({ method, origin, pathname }, ownerOrigin) {
  if (origin !== ownerOrigin) return 'rejected-external-origin';
  if (/^\/api\/(chat|ai)(?:\/|$)/.test(pathname)) {
    if (method === 'GET' && pathname === '/api/chat/conversations') return 'allowed-local-conversation-read';
    return 'rejected-chat-or-ai-route';
  }
  return 'allowed-app-request';
}
