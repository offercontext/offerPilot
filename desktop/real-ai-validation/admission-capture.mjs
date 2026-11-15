// Observe the one synthetic UI-owned POST identity, without changing its fetch.
// No response-body dependency, extra request, header access, or retained input.
import { randomUUID } from 'node:crypto';
const invalidCapture = () => Object.assign(new Error('ADMISSION_CAPTURE_INVALID'), { proofFault: 'ADMISSION_READ_FAILED', captureInvalid: true });

export function rendererAdmissionCapture(args) {
  const key = '__offerpilotAdmissionCapture';
  const fail = () => ({ phase: 'FAILED', fault: 'INVALID_CONTEXT' });
  if (window.offerpilotDesktop?.role !== 'owner' || location.origin !== args.origin
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(args.nonce)) return fail();
  if (args.operation === 'install') {
    if (Object.hasOwn(window, key) || typeof window.fetch !== 'function') return fail();
    const original = window.fetch;
    const state = { nonce: args.nonce, original, wrapper: null, phase: 'PENDING', fault: null,
      count: 0, requestId: null, closed: false };
    const reject = fault => { state.phase = 'FAILED'; state.fault = fault; state.requestId = null; };
    state.wrapper = function (...parameters) {
      let selected = false;
      try {
        const input = parameters[0], options = parameters[1];
        // PIN uses a string URL. Supporting Request would require inspecting
        // an additional request object; fail closed for that unsupported form.
        if (typeof input === 'string') {
          const url = new URL(input, location.href);
          selected = url.href === `${args.origin}/api/pilot/runtime/v1/turns`
            && String(options?.method || 'GET').toUpperCase() === 'POST';
        }
      } catch { /* Pass application input through unchanged. */ }
      if (selected && !state.closed) {
        state.count += 1;
        if (state.count !== 1) reject('DUPLICATE');
        else {
          try {
            // Only this synthetic UI submission, never headers or credentials.
            // Canonical JSON rejects duplicate/escaped keys and ambiguous IDs.
            const body = parameters[1]?.body;
            if (typeof body !== 'string' || body.length > 65536
              || new TextEncoder().encode(body).byteLength > 65536) throw new Error();
            const value = JSON.parse(body);
            if (!value || Array.isArray(value) || JSON.stringify(value) !== body
              || !Object.hasOwn(value, 'request_id') || !Object.hasOwn(value, 'conversation_id')
              || typeof value.request_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.request_id)
              || value.conversation_id !== 0) throw new Error();
            state.requestId = value.request_id; state.phase = 'READY';
          } catch { reject('REQUEST_INVALID'); }
        }
      }
      // No response subscription: PIN intentionally aborts a hanging POST
      // after exact GET-by-request recovery. Return its original Promise.
      const pending = Reflect.apply(original, this, parameters);
      return pending;
    };
    Object.defineProperty(window, key, { value: state, configurable: true });
    try { window.fetch = state.wrapper; } catch { delete window[key]; return fail(); }
    if (window.fetch !== state.wrapper) { delete window[key]; return fail(); }
    return { phase: 'INSTALLED' };
  }
  const state = window[key];
  if (!state || state.nonce !== args.nonce) return fail();
  if (args.operation === 'dispose') {
    state.closed = true; state.requestId = null;
    const owned = window.fetch === state.wrapper;
    if (owned) window.fetch = state.original;
    delete window[key];
    return { phase: owned && window.fetch === state.original && state.phase !== 'FAILED' ? 'DISPOSED' : 'FAILED' };
  }
  if (!['read', 'check'].includes(args.operation) || state.closed || window.fetch !== state.wrapper) return fail();
  return { phase: state.phase, fault: state.fault, count: state.count, requestId: state.requestId };
}

export async function installAdmissionCapture(page, origin, within) {
  const nonce = randomUUID(); let disposed = false;
  const call = operation => page.evaluate(rendererAdmissionCapture, { operation, origin, nonce });
  const capture = {
    async assertValid(complete = false) {
      const value = await call('check');
      if (disposed || !['PENDING', 'READY'].includes(value?.phase) || ![0, 1].includes(value.count)
        || (complete && (value.phase !== 'READY' || value.count !== 1))) throw invalidCapture();
    },
    async request(requestId) {
      const value = await call('read');
      if (disposed || value?.phase !== 'READY' || value.count !== 1 || value.requestId !== requestId) throw invalidCapture();
      return requestId;
    },
    async dispose() {
      disposed = true; let timer;
      try {
        const value = await Promise.race([call('dispose'), new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error('ADMISSION_CAPTURE_CLEANUP_FAILED')), 5000);
        })]);
        if (value?.phase !== 'DISPOSED') throw new Error('ADMISSION_CAPTURE_CLEANUP_FAILED');
      } finally { clearTimeout(timer); }
    },
  };
  try {
    const installed = await within(() => call('install'), 'ADMISSION_READ');
    if (installed?.phase !== 'INSTALLED') throw invalidCapture();
    return capture;
  } catch (error) {
    try { await capture.dispose(); } catch { /* Preserve installation failure. */ }
    throw error;
  }
}
