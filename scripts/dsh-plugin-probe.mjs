// Test-only DSH plugin. Loaded into an isolated real profile by the acceptance
// runner, never shipped in the OfferPilot package and never calls a model.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';

export const name = 'offerpilot-acceptance-probe';
export const inject = ['tools', 'loader'];

async function exercise(ctx, config) {
  const fixture = JSON.parse(await readFile(config.fixture, 'utf8'));
  let names = [];
  for (let attempt = 0; attempt < 200; attempt++) {
    names = ctx.tools.schemas().map(tool => tool.function?.name ?? tool.name)
      .filter(name => name.startsWith('mcp__offerpilot__'));
    const entry = [...ctx.loader.entries()].find(entry => entry.options.id === 'offerpilot');
    if (config.revoked ? entry?.fiber?.state === 3 : names.length > 0) break;
    await delay(100);
  }
  if (config.revoked) {
    const entry = [...ctx.loader.entries()].find(entry => entry.options.id === 'offerpilot');
    assert.equal(entry?.fiber?.state, 3, 'revoked connection must fail plugin activation');
    assert.equal(names.length, 0, 'revoked connection must expose no tools');
    await assert.rejects(entry.fiber.await(), /initial connection or tool synchronization failed/);
    return { passed: true, revoked: true, plugin_state: 'failed', tools: 0 };
  }
  assert.equal(names.length, config.readOnly ? 6 : 8, 'only granted tools are exposed');
  assert.equal(names.some(name => /approve|confirm/.test(name)), false);
  async function call(name, args = {}) {
    const result = await ctx.tools.execute({
      callId: randomUUID(), name: `mcp__offerpilot__${name}`, arguments: args,
      signal: AbortSignal.timeout(20000),
    });
    assert.ok(!result.isError, JSON.stringify(result));
    const value = result.value.structuredContent;
    assert.equal(value.ok, true, JSON.stringify(value));
    return value.data;
  }
  const application = await call('get_application', { application_id: fixture.application_id });
  assert.equal(application.company_name, '外部 Agent 验收虚构公司');
  assert.equal((await call('list_applications')).items.length, 1);
  assert.equal((await call('list_events')).items.length, 0);
  const denied = await ctx.tools.execute({ callId: randomUUID(),
    name: 'mcp__offerpilot__get_application', arguments: { application_id: fixture.out_of_scope_id },
    signal: AbortSignal.timeout(20000) });
  assert.equal(denied.isError, true, 'existing out-of-scope record must be denied');
  assert.equal(JSON.parse(denied.error.message).error.code, 'scope_denied');
  if (config.readOnly) {
    assert.equal(names.some(name => name.includes('propose_')), false);
    let httpStatus;
    if (config.web) {
      // Only Web profiles provide this service. A child injection keeps the
      // same probe usable in base profiles without undeclared service access.
      await ctx.inject(['webServer', 'connection'], async (webCtx) => {
        let page;
        for (let attempt = 0; attempt < 100; attempt++) {
          const origin = webCtx.webServer.getOrigin();
          const url = webCtx.connection.authenticatedUrl(origin);
          const login = await fetch(url, { redirect: 'manual', signal: AbortSignal.timeout(5000) });
          await login.arrayBuffer();
          assert.equal(login.status, 303, 'launch token must exchange for a browser cookie');
          assert.equal(login.headers.get('location'), './');
          const cookie = login.headers.get('set-cookie')?.split(';')[0];
          assert.ok(cookie, 'browser cookie must be issued');
          // Node fetch has no browser cookie jar; carry the issued cookie only
          // to the same local origin. Never write it or the token to the report.
          page = await fetch(origin, { headers: { Cookie: cookie }, signal: AbortSignal.timeout(5000) });
          if (page.ok) break;
          await page.arrayBuffer();
          await delay(100);
        }
        assert.equal(page?.status, 200, 'real Web profile serves its UI');
        assert.match(await page.text(), /<html/i);
        httpStatus = page.status;
      });
      assert.equal(httpStatus, 200);
    }
    return { passed: true, names, read_only: true, out_of_scope: 'denied', http_status: httpStatus };
  }
  assert.equal((await call('get_request', { request_id: fixture.request_id })).status, 'pending');
  const input = {
    application_id: fixture.application_id, idempotency_key: randomUUID(),
    scheduled_at: '2030-11-01T15:00:00+08:00', duration_minutes: 60,
  };
  const pending = await call('propose_interview', input);
  assert.equal(pending.status, 'pending');
  assert.equal((await call('propose_interview', input)).request_id, pending.request_id);
  assert.equal((await call('list_events')).items.length, 0, 'proposal never commits a business write');
  assert.equal((await call('cancel_request', { request_id: pending.request_id })).status, 'cancelled');
  assert.equal((await call('get_request', { request_id: pending.request_id })).status, 'cancelled');
  return { passed: true, names, application_id: application.id, proposal: 'pending',
    idempotency: 'same request', cancellation: 'cancelled', event_count: 0,
    real_model_calls: 0, user_approval_exercised: false };
}

export function apply(ctx, config) {
  // Run after mounting; do not block other plugins' startup with the probe.
  const timer = setTimeout(async () => {
    let report;
    try { report = await exercise(ctx, config); }
    catch (error) { report = { passed: false, error: String(error.stack ?? error) }; }
    try {
      const tools = ctx.tools;
      await ctx.root.fiber.dispose();
      assert.equal(tools.schemas().some(tool => (tool.function?.name ?? tool.name).startsWith('mcp__offerpilot__')), false);
      report.disposed = true;
      await writeFile(config.report, JSON.stringify(report, null, 2), { flag: 'wx' });
    } catch (error) {
      console.error(error);
      process.exitCode = 1;
      return;
    }
    process.exitCode = report.passed ? 0 : 1;
  }, 0);
  ctx.effect(() => () => clearTimeout(timer));
}
