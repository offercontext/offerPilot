'use strict';

// Peak CNY prices verified 2026-10-08. Integer micro-yuan only; no token estimates.
// https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
// https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/
// Rate/model changes require renewed review before an authorized live run.
const POLICY = Object.freeze({ model: 'deepseek-flash', contextTokens: 1048576,
  budgetMicroCny: 10000000, reserveMicroCny: 3000000, maxRequests: 8,
  runMs: 600000, requestBytes: 524288, responseBytes: 8388608, eventBytes: 262144 });
const CASES = Object.freeze({
  connection: Object.freeze({ maxTokens: 64, timeoutMs: 15000 }),
  'pilot-stream': Object.freeze({ maxTokens: 4096, timeoutMs: 60000 }),
  'pilot-cancel': Object.freeze({ maxTokens: 4096, timeoutMs: 60000 }),
  'pilot-hitl-reject': Object.freeze({ maxTokens: 4096, timeoutMs: 60000 }),
  'interview-preparation': Object.freeze({ maxTokens: 8192, timeoutMs: 90000 }),
  'resume-structure': Object.freeze({ maxTokens: 4096, timeoutMs: 60000 }),
  'offer-negotiation': Object.freeze({ maxTokens: 8192, timeoutMs: 90000 }),
});
const REASONS = Object.freeze(['AUTH', 'ROUTE', 'CLOSED', 'DEADLINE', 'UNARMED', 'BUSY',
  'CASE', 'BUDGET', 'COUNT', 'BODY', 'MODEL', 'PARAMETER', 'CANCELLED', 'DISCONNECT', 'UPSTREAM_DISCONNECT',
  'TIMEOUT', 'EXPIRED', 'LEDGER', 'UPSTREAM', 'REDIRECT', 'PROTOCOL', 'USAGE', 'SETTLED']);
class BudgetError extends Error {
  constructor(code) { super(REASONS.includes(code) ? code : 'PROTOCOL'); this.code = this.message; }
}
function fail(code) { throw new BudgetError(code); }
const object = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const integer = (v, max = POLICY.contextTokens) => Number.isSafeInteger(v) && v >= 0 && v <= max;

function usageCost(usage, cap) {
  if (!object(usage)) fail('USAGE');
  const p = usage.prompt_tokens; const c = usage.completion_tokens;
  const h = usage.prompt_cache_hit_tokens; const m = usage.prompt_cache_miss_tokens;
  if (![p, c, h, m, usage.total_tokens].every((v) => integer(v)) || c > cap ||
      p !== h + m || usage.total_tokens !== p + c || p + c > POLICY.contextTokens) fail('USAGE');
  if (usage.prompt_tokens_details != null &&
      (!object(usage.prompt_tokens_details) ||
       (usage.prompt_tokens_details.cached_tokens != null && usage.prompt_tokens_details.cached_tokens !== h))) fail('USAGE');
  if (usage.completion_tokens_details != null &&
      (!object(usage.completion_tokens_details) ||
       (usage.completion_tokens_details.reasoning_tokens != null &&
        !integer(usage.completion_tokens_details.reasoning_tokens, c)))) fail('USAGE');
  // cache hit = 0.04 micro-yuan/token. ceil once, conservatively at peak rates.
  const micro = Number((BigInt(h) + 24n) / 25n + 2n * BigInt(m) + 8n * BigInt(c));
  if (!integer(micro, POLICY.reserveMicroCny)) fail('USAGE');
  return { micro, promptTokens: p, completionTokens: c, cacheHitTokens: h, cacheMissTokens: m };
}

function envelope(body, cap) {
  if (!object(body)) fail('BODY');
  if (body.model !== POLICY.model) fail('MODEL');
  const allowed = new Set(['model', 'messages', 'max_tokens', 'stream', 'stream_options',
    'tools', 'tool_choice', 'response_format', 'temperature', 'top_p', 'stop', 'thinking',
    'reasoning_effort', 'frequency_penalty', 'presence_penalty', 'n', 'parallel_tool_calls']);
  if (Object.keys(body).some((key) => !allowed.has(key))) fail('PARAMETER');
  if (!Array.isArray(body.messages) || body.messages.length < 1 || body.messages.length > 256) fail('PARAMETER');
  for (const msg of body.messages) {
    if (!object(msg) || !['system', 'user', 'assistant', 'tool'].includes(msg.role) ||
        (msg.content !== null && typeof msg.content !== 'string') ||
        Object.keys(msg).some((key) => !['role', 'content', 'name', 'tool_calls', 'tool_call_id', 'reasoning_content'].includes(key))) fail('PARAMETER');
  }
  if (body.max_tokens != null && (!integer(body.max_tokens, 393216) || body.max_tokens < 1)) fail('PARAMETER');
  if (body.stream != null && typeof body.stream !== 'boolean') fail('PARAMETER');
  if (body.n != null && body.n !== 1) fail('PARAMETER');
  if (body.parallel_tool_calls != null && typeof body.parallel_tool_calls !== 'boolean') fail('PARAMETER');
  for (const [key, low, high] of [['temperature', 0, 2], ['top_p', 0, 1], ['frequency_penalty', -2, 2], ['presence_penalty', -2, 2]]) {
    if (body[key] != null && (typeof body[key] !== 'number' || !Number.isFinite(body[key]) || body[key] < low || body[key] > high)) fail('PARAMETER');
  }
  if (body.thinking != null && (!object(body.thinking) || Object.keys(body.thinking).length !== 1 || !['enabled', 'disabled'].includes(body.thinking.type))) fail('PARAMETER');
  if (body.reasoning_effort != null && !['none', 'low', 'high', 'max', 'minimal', 'medium', 'xhigh'].includes(body.reasoning_effort)) fail('PARAMETER');
  if (body.tools != null) {
    if (!Array.isArray(body.tools) || body.tools.length > 64) fail('PARAMETER');
    for (const tool of body.tools) {
      if (!object(tool) || tool.type !== 'function' || Object.keys(tool).some((k) => !['type', 'function'].includes(k)) ||
          !object(tool.function) || Object.keys(tool.function).some((k) => !['name', 'description', 'parameters', 'strict'].includes(k)) ||
          typeof tool.function.name !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(tool.function.name) ||
          (tool.function.description != null && typeof tool.function.description !== 'string') ||
          (tool.function.parameters != null && !object(tool.function.parameters)) ||
          (tool.function.strict != null && typeof tool.function.strict !== 'boolean')) fail('PARAMETER');
    }
  }
  if (body.tool_choice != null && !['none', 'auto', 'required'].includes(body.tool_choice) &&
      (!object(body.tool_choice) || body.tool_choice.type !== 'function' ||
       Object.keys(body.tool_choice).some((k) => !['type', 'function'].includes(k)) ||
       !object(body.tool_choice.function) || Object.keys(body.tool_choice.function).length !== 1 ||
       typeof body.tool_choice.function.name !== 'string')) fail('PARAMETER');
  if (body.response_format != null && (!object(body.response_format) ||
      !['text', 'json_object'].includes(body.response_format.type) || Object.keys(body.response_format).length !== 1)) fail('PARAMETER');
  if (body.stop != null && typeof body.stop !== 'string' &&
      (!Array.isArray(body.stop) || body.stop.length > 16 || body.stop.some((item) => typeof item !== 'string'))) fail('PARAMETER');
  if (body.stream_options != null && (!object(body.stream_options) || Object.keys(body.stream_options).some((k) => k !== 'include_usage') || typeof body.stream_options.include_usage !== 'boolean')) fail('PARAMETER');
  if (!body.stream && body.stream_options != null) fail('PARAMETER');
  const maxTokens = Math.min(body.max_tokens ?? cap, cap);
  return { body: { ...body, max_tokens: maxTokens,
    ...(body.stream ? { stream_options: { include_usage: true } } : {}) }, maxTokens,
    envelope: body.max_tokens == null ? 'INSERTED' : body.max_tokens > cap ? 'TIGHTENED' : 'UNCHANGED' };
}

class Ledger {
  #now; #deadline; #closed = false; #armed = null; #active = null; #used = new Set();
  #records = []; #denied = Object.fromEntries(REASONS.map((r) => [r, 0]));
  constructor(now = () => performance.now()) { this.#now = now; this.#deadline = now() + POLICY.runMs; }
  remainingMs() { return Math.max(0, this.#deadline - this.#now()); }
  #check() { if (this.#closed) fail('CLOSED'); if (this.remainingMs() <= 0) fail('DEADLINE'); }
  deny(code) { if (REASONS.includes(code)) this.#denied[code] += 1; }
  armCase(id) {
    this.#check();
    if (!Object.hasOwn(CASES, id) || this.#used.has(id)) fail('CASE');
    if (this.#armed || this.#active) fail('BUSY');
    this.#armed = id;
  }
  claim() {
    this.#check();
    if (this.#active) fail('BUSY');
    if (!this.#armed) fail('UNARMED');
    const ticket = Object.freeze({ caseId: this.#armed, ...CASES[this.#armed] });
    this.#used.add(this.#armed); this.#armed = null; this.#active = ticket;
    return ticket;
  }
  reserve(ticket, maxTokens, adjustment) {
    this.#check();
    if (ticket !== this.#active || this.#records.some((r) => r.ticket === ticket)) fail('CASE');
    if (!integer(maxTokens, ticket.maxTokens) || maxTokens < 1 || !['INSERTED', 'TIGHTENED', 'UNCHANGED'].includes(adjustment)) fail('PARAMETER');
    if (this.#records.length >= POLICY.maxRequests) fail('COUNT');
    const charged = this.#records.reduce((s, r) => s + r.micro, 0);
    if (!integer(charged, POLICY.budgetMicroCny) || charged + POLICY.reserveMicroCny > POLICY.budgetMicroCny) fail('BUDGET');
    // Synchronous critical section before the single outbound HTTPS request.
    this.#records.push({ ticket, caseId: ticket.caseId, cap: maxTokens, envelope: adjustment,
      status: 'RESERVED', micro: POLICY.reserveMicroCny, outboundStarted: false, upstreamResponded: false, clientDisconnectObserved: false });
  }
  markTransport(ticket, field) {
    if (ticket !== this.#active || !['outboundStarted', 'upstreamResponded', 'clientDisconnectObserved'].includes(field)) return;
    const row = this.#records.find((r) => r.ticket === ticket);
    if (row) row[field] = true;
  }
  finish(ticket, code, usage = null) {
    // Terminal requests never settle again, even if usage arrives after an abort.
    if (ticket !== this.#active) return false;
    const row = this.#records.find((r) => r.ticket === ticket);
    this.#active = null;
    if (!row) { this.deny(code); return true; }
    if (code === 'SETTLED') {
      try { const cost = usageCost(usage, row.cap); Object.assign(row, cost, { status: 'SETTLED' }); }
      catch { row.status = 'USAGE'; this.#closed = true; }
    } else row.status = REASONS.includes(code) ? code : 'PROTOCOL';
    return true;
  }
  retainUncertain(ticket) {
    const row = this.#records.find((r) => r.ticket === ticket);
    if (row) {
      row.status = 'LEDGER'; row.micro = POLICY.reserveMicroCny;
      for (const key of ['promptTokens', 'completionTokens', 'cacheHitTokens', 'cacheMissTokens']) delete row[key];
    }
    this.close();
  }
  cancelCase() { if (this.#armed) this.#used.add(this.#armed); this.#armed = null; if (this.#active) this.finish(this.#active, 'CANCELLED'); }
  close() { this.#closed = true; this.cancelCase(); }
  snapshot() {
    return { model: POLICY.model, budgetMicroCny: POLICY.budgetMicroCny, reserveMicroCny: POLICY.reserveMicroCny,
      sentRequests: this.#records.length, settledMicroCny: this.#records.filter((r) => r.status === 'SETTLED').reduce((s, r) => s + r.micro, 0),
      retainedMicroCny: this.#records.filter((r) => r.status !== 'SETTLED').reduce((s, r) => s + r.micro, 0),
      active: Boolean(this.#active), closed: this.#closed, denied: { ...this.#denied },
      requests: this.#records.map(({ ticket, ...row }) => ({ ...row })) };
  }
}

// A bounded streaming parser. Only usage counters survive each event; content is never logged.
class UsageParser {
  #stream; #cap; #decoder = new TextDecoder('utf-8', { fatal: true }); #buffer = '';
  #bytes = 0; #usage = null; #done = false; #terminal = false;
  constructor(stream, cap) { this.#stream = stream; this.#cap = cap; }
  push(chunk) {
    this.#bytes += chunk.length;
    if (this.#bytes > POLICY.responseBytes) fail('BODY');
    this.#buffer += this.#decoder.decode(chunk, { stream: true });
    if (!this.#stream) return;
    this.#buffer = this.#buffer.replace(/\r\n/g, '\n');
    let pos;
    while ((pos = this.#buffer.indexOf('\n\n')) !== -1) {
      const event = this.#buffer.slice(0, pos); this.#buffer = this.#buffer.slice(pos + 2);
      if (Buffer.byteLength(event) > POLICY.eventBytes) fail('BODY');
      this.#event(event);
    }
    if (Buffer.byteLength(this.#buffer) > POLICY.eventBytes) fail('BODY');
  }
  #event(event) {
    const lines = event.split('\n');
    const fields = lines.filter((line) => line && !line.startsWith(':'));
    if (!fields.length) return;
    if (fields.some((line) => !line.startsWith('data:'))) fail('PROTOCOL');
    if (this.#done) fail('PROTOCOL');
    const data = fields.map((line) => line.slice(5).trimStart()).join('\n');
    if (data === '[DONE]') { this.#done = true; return; }
    let value; try { value = JSON.parse(data); } catch { fail('PROTOCOL'); }
    this.#observe(value, true);
  }
  #observe(value, stream) {
    if (!object(value) || value.model !== POLICY.model || !Array.isArray(value.choices) || value.choices.length > 1 || value.error) fail('PROTOCOL');
    if (stream && this.#usage) fail('USAGE');
    const finished = value.choices.length === 1 && ['stop', 'length', 'tool_calls', 'content_filter'].includes(value.choices[0].finish_reason);
    if (finished) this.#terminal = true;
    if (value.usage != null) {
      if (this.#usage || (stream && !finished && !(this.#terminal && value.choices.length === 0))) fail('USAGE');
      usageCost(value.usage, this.#cap);
      // Copy only reviewed numeric fields; discard all unknown provider values.
      this.#usage = Object.fromEntries(['prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'total_tokens'].map((k) => [k, value.usage[k]]));
    }
    if (!stream && !finished) fail('PROTOCOL');
  }
  end() {
    this.#buffer += this.#decoder.decode();
    if (this.#stream) {
      if (this.#buffer.trim() || !this.#done || !this.#terminal || !this.#usage) fail('USAGE');
    } else {
      let value; try { value = JSON.parse(this.#buffer); } catch { fail('PROTOCOL'); }
      this.#observe(value, false);
      this.#buffer = '';
      if (!this.#usage) fail('USAGE');
    }
    return this.#usage;
  }
}
module.exports = { POLICY, CASES, BudgetError, Ledger, envelope, usageCost, UsageParser };
