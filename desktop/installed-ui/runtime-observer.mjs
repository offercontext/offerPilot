import { classifyRuntimeMessage, publicRequestFailure } from './coverage-model.mjs';

const RESOURCE_NOT_FOUND = 'Failed to load resource: the server responded with a status of 404 (Not Found)';
const MAX_CORRELATIONS = 100;

export function observeRuntime(page) {
  const counts = {};
  const requests = [];
  let ownCriticalFailureCount = 0;
  let requestFailureCount = 0;
  let uncertainMutationOutcome = false;
  const inflightWrites = new Set();
  // Raw URLs exist only in this bounded, private correlation queue. No console
  // text, request URL, stack, or request object is included in snapshots.
  let correlations = [];
  let correlationDisabled = false;
  const add = (name) => { counts[name] = (counts[name] || 0) + 1; };
  const origin = () => {
    try { return new URL(page.url()).origin; } catch { return null; }
  };
  const finalize = (entry) => {
    if (entry.kind === 'console') add('unclassified-console-error');
  };
  const invalidate = (url) => {
    correlations = correlations.filter((entry) => {
      if (url !== undefined && entry.url !== url) return true;
      finalize(entry);
      return false;
    });
  };
  const enqueue = (entry) => {
    if (correlationDisabled) { finalize(entry); return; }
    if (correlations.length >= MAX_CORRELATIONS) {
      // Evicting evidence could let a late response suppress a different error.
      // Fail closed for the rest of the run instead, without dropping counts.
      invalidate();
      correlationDisabled = true;
      finalize(entry);
      return;
    }
    correlations.push(entry);
  };
  const correlate = (url, kind) => {
    if (correlationDisabled) {
      if (kind === 'console') add('unclassified-console-error');
      return;
    }
    const index = correlations.findIndex((entry) => entry.url === url &&
      (kind === 'console' ? entry.kind === 'response' : entry.kind !== 'response'));
    if (index === -1) { enqueue({ url, kind }); return; }
    const [entry] = correlations.splice(index, 1);
    // A console already exposed by snapshot remains an error permanently.
    // Consume its late response so it cannot excuse a subsequent console.
    if (entry.kind !== 'reported-console') add('expected-resource-console');
  };

  page.on('pageerror', (error) => add(classifyRuntimeMessage(error.message, 'pageerror')));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    const url = message.location?.()?.url;
    // Chromium's network-generated console entry has no JS arguments. A page
    // console.error using the same words must still remain a blocking error.
    const args = message.args?.();
    const expectedLocation = typeof url === 'string' && publicRequestFailure(url, origin(), 404, 'GET')?.expectedAbsent;
    if (text === RESOURCE_NOT_FOUND && Array.isArray(args) && args.length === 0 && expectedLocation) {
      correlate(url, 'console');
    } else {
      if (typeof url === 'string') invalidate(url);
      add(classifyRuntimeMessage(text));
    }
  });
  page.on('request', (request) => {
    // A new request must not borrow an older response's one-use credit, even
    // when it reuses the URL with another HTTP method.
    invalidate(request.url());
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) inflightWrites.add(request);
  });
  page.on('requestfinished', (request) => inflightWrites.delete(request));
  page.on('requestfailed', (request) => {
    if (inflightWrites.has(request)) uncertainMutationOutcome = true;
    inflightWrites.delete(request);
    invalidate(request.url());
    const safe = publicRequestFailure(request.url(), origin(), null);
    if (safe) {
      const expectedCancellation = request.failure?.()?.errorText === 'net::ERR_ABORTED';
      requestFailureCount++;
      if (!expectedCancellation) ownCriticalFailureCount++;
      if (requests.length < 100) requests.push({ ...safe, expectedCancellation });
    }
  });
  page.on('response', (response) => {
    const status = response.status();
    const url = response.url();
    const method = response.request?.()?.method?.();
    const safe = publicRequestFailure(url, origin(), status, method || 'GET');
    // Correlation requires an actual GET method from the response's request;
    // the legacy reporting fallback alone cannot supply trusted evidence.
    if (safe?.expectedAbsent && method === 'GET') correlate(url, 'response');
    else invalidate(url);
    if (status < 400) return;
    if (safe) {
      requestFailureCount++;
      if (safe.status >= 500 || safe.category === 'own-asset' || (safe.category === 'own-api' && !safe.expectedAbsent)) ownCriticalFailureCount++;
      if (requests.length < 100) requests.push(safe);
    }
  });
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame?.()) invalidate();
  });
  return {
    attached: true,
    snapshot: () => {
      // Unmatched entries are blocking at every observation boundary. Never
      // decrement published counts: that would mask a later case's new error.
      correlations = correlations.filter((entry) => entry.kind !== 'response');
      for (const entry of correlations) {
        finalize(entry);
        entry.kind = 'reported-console';
      }
      return { classifications: { ...counts }, ownRequestFailures: requests.map((item) => ({ ...item })),
        ownCriticalFailureCount, requestFailureCount, uncertainMutationOutcome, bounded: requestFailureCount > requests.length };
    },
    hasPendingWrite: () => inflightWrites.size > 0 || uncertainMutationOutcome,
  };
}
