import assert from 'node:assert/strict';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const signals = ['visible', 'typing', 'work_running', 'awaiting_approval', 'mock_interview', 'quiet', 'failure'];
const keys = ['identity', 'client_id', 'sequence', ...signals].sort();
// Capture frame identity before abort can detach the renderer. This function
// performs no asynchronous work and does not decide whether suppression is safe.
export function capturePresenceRequest(request) {
  const frame = request.frame();
  return { url: request.url(), method: request.method(), contentType: request.headers()['content-type'], body: request.postData(),
    redirected: request.redirectedFrom() !== null, worker: request.serviceWorker() !== null,
    mainFrame: frame.parentFrame() === null, sourceFrame: frame, sourcePage: frame.page(), frameURL: frame.url() };
}
export function createSerialRequestQueue() {
  let tail = Promise.resolve(), failure;
  return {
    // Registration occurs synchronously, before any handler awaits port/abort.
    schedule(operation) {
      const work = tail.then(operation);
      tail = work.catch(error => { failure ??= error; });
      return work;
    },
    async drain() {
      let observed;
      do { observed = tail; await observed; } while (observed !== tail);
      if (failure) throw failure;
    },
  };
}
// Diagnostics carry fixed codes only, never assertion values or request bodies.
function checked(code, operation) {
  try { return operation(); }
  catch { const error = new Error(code); error.presenceCode = code; throw error; }
}
function ownerDocument(raw, origin) {
  checked('owner-document', () => {
    // The only navigation exercised by this read-only scenario is Settings.
    // Compare raw strings so normalization cannot admit other query variants.
    assert.ok(raw === `${origin}/` || raw === `${origin}/?view=settings`);
  });
}
function parse(input) {
  checked('confirmed-abort', () => assert.equal(input.aborted, true));
  checked('request-target', () => {
    assert.equal(input.method, 'POST');
    const origin = new URL(input.origin);
    assert.equal(origin.protocol, 'http:'); assert.equal(origin.hostname, '127.0.0.1');
    assert.ok(origin.port); assert.equal(origin.origin, input.origin);
    assert.equal(input.url, `${input.origin}/api/companion/presence`);
  });
  checked('content-type', () => assert.match(input.contentType, /^application\/json(?:;\s*charset=utf-8)?$/i));
  checked('request-provenance', () => {
    for (const key of ['redirected', 'worker']) assert.equal(input[key], false);
    assert.equal(input.mainFrame, true);
  });
  ownerDocument(input.frameURL, input.origin);
  const body = checked('body-schema', () => {
    assert.equal(typeof input.body, 'string');
    assert.ok(Buffer.byteLength(input.body, 'utf8') <= 2048);
    const body = JSON.parse(input.body);
    assert.ok(body && typeof body === 'object' && !Array.isArray(body));
    assert.deepEqual(Object.keys(body).sort(), keys);
    for (const key of ['identity', 'client_id']) assert.match(body[key], uuid);
    assert.ok(Number.isSafeInteger(body.sequence) && body.sequence >= 0);
    for (const key of signals) assert.equal(typeof body[key], 'boolean');
    return body;
  });
  checked('inactive-signals', () => {
    for (const key of ['work_running', 'awaiting_approval', 'mock_interview', 'quiet']) assert.equal(body[key], false);
  });
  return { origin: input.origin, sourcePage: input.sourcePage, sourceFrame: input.sourceFrame, body };
}

// This changes accounting, never transport: the caller must abort first. Early
// heartbeats remain unaccepted until the real owner and backend opt-out are read.
// Raw request bodies and identifiers are held only in memory, never in evidence.
export function createPresenceSuppression() {
  let context, client, sequence = -1, aborted = 0, validated = 0, rejected = false, firstRejection;
  const pending = [];
  function reject(error) {
    firstRejection ??= error.presenceCode || 'unclassified-validation';
    rejected = true;
    throw error;
  }
  function validate(candidate) {
    checked('owner-provenance', () => {
      assert.equal(candidate.origin, context.origin);
      assert.equal(candidate.sourcePage, context.ownerPage);
      assert.equal(candidate.sourceFrame, context.ownerMainFrame);
    });
    checked('backend-identity', () => assert.equal(candidate.body.identity, context.snapshot.identity));
    checked('client-continuity', () => {
      if (client !== undefined) assert.equal(candidate.body.client_id, client);
    });
    checked('sequence-advance', () => assert.ok(candidate.body.sequence > sequence));
    client = candidate.body.client_id; sequence = candidate.body.sequence; validated++;
  }
  return {
    recordAborted(input) {
      try {
        assert.equal(rejected, false, 'a rejected presence cannot later be accepted');
        const candidate = parse(input); aborted++;
        if (context) validate(candidate); else pending.push(candidate);
      } catch (error) { reject(error); }
    },
    verifyContext(next) {
      try {
        assert.equal(rejected, false, 'a rejected presence cannot later be accepted');
        ownerDocument(next.ownerURL, next.origin);
        checked('owner-context', () => {
          assert.equal(next.ownerRole, 'owner'); assert.ok(next.ownerPage && next.ownerMainFrame);
          if (context) for (const key of ['origin', 'ownerPage', 'ownerMainFrame']) assert.equal(next[key], context[key]);
        });
        checked('backend-opt-out', () => {
          assert.equal(next.snapshot?.enabled, false); assert.match(next.snapshot.identity, uuid);
          if (context) assert.equal(next.snapshot.identity, context.snapshot.identity);
        });
        context = next;
        while (pending.length) { validate(pending[0]); pending.shift(); }
      } catch (error) { reject(error); }
    },
    assertSettled() {
      assert.equal(rejected, false, 'unexpected presence must fail acceptance');
      assert.ok(context, 'presence requires a verified owner and disabled backend');
      assert.equal(pending.length, 0, 'unverified early presence must fail acceptance');
      assert.equal(aborted, validated, 'every suppressed request must have verified abort and provenance');
    },
    summary() { return { aborted, validated, pending: pending.length, rejected, ...(firstRejection ? { firstRejection } : {}) }; },
  };
}
