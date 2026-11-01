import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { HARU_ASSET_PATHS, loadHaruAssetExpectations, observeHaruAssets } from '../../live2d-assets.mjs';

const origin = 'http://127.0.0.1:4175';
const secret = 'PRIVATE_TOKEN_query_header_error';
const bodies = HARU_ASSET_PATHS.map((_, index) => Buffer.from(`asset-${index}`));
const expected = HARU_ASSET_PATHS.map((assetPath, index) => ({ path: assetPath, bytes: bodies[index].length,
  sha256: createHash('sha256').update(bodies[index]).digest('hex') }));
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
function fixture() {
  const page = new EventEmitter();
  const observer = observeHaruAssets(page, { origin, expected });
  const bodyCalls = [];
  function request(index, { url = origin + HARU_ASSET_PATHS[index], method = 'GET', redirectedFrom = null, observed = true } = {}) {
    const value = { url: () => url, method: () => method, redirectedFrom: () => redirectedFrom,
      headers: () => { throw new Error(secret); }, failure: () => { throw new Error(secret); } };
    if (observed) page.emit('request', value);
    return value;
  }
  function respond(index, options = {}) {
    const req = options.request || request(index, options);
    const response = { request: () => req, url: () => options.responseUrl ?? req.url(), status: () => options.status ?? 200,
      finished: options.finished || (async () => null), body: options.body || (async () => { bodyCalls.push(index); return bodies[index]; }),
      headers: () => { throw new Error(secret); } };
    page.emit('response', response);
    return req;
  }
  function others(except = -1) { for (let index = 0; index < 4; index++) if (index !== except) respond(index); }
  function clean() { for (const event of ['request', 'response', 'requestfailed']) assert.equal(page.listenerCount(event), 0); }
  async function finish(timeoutMs = 25) {
    const result = await observer.finish({ timeoutMs });
    clean();
    assert.equal(JSON.stringify(result).includes(secret), false);
    return result;
  }
  return { page, observer, request, respond, others, finish, clean, bodyCalls };
}
const firstStatus = result => result.assets[0].responses[0].status;

async function fileFixture(t) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'haru-assets-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const roots = { sourceRoot: path.join(directory, 'source'), servedRoot: path.join(directory, 'served') };
  const model = JSON.stringify({ FileReferences: { Moc: 'haru_greeter_t03.moc3', Textures: [
    'haru_greeter_t03.2048/texture_00.png', 'haru_greeter_t03.2048/texture_01.png'] } });
  for (const root of Object.values(roots)) for (let index = 0; index < 4; index++) {
    const filename = path.join(root, HARU_ASSET_PATHS[index].slice(1));
    await mkdir(path.dirname(filename), { recursive: true });
    await writeFile(filename, index === 0 ? model : bodies[index]);
  }
  return roots;
}

test('reference manifest binds exactly four production model references and public source bytes', async () => {
  const sourceRoot = fileURLToPath(new URL('../../../web/public/', import.meta.url));
  const references = await loadHaruAssetExpectations({ sourceRoot, servedRoot: sourceRoot });
  assert.deepEqual(references.map(asset => asset.path), HARU_ASSET_PATHS);
  for (const asset of references) {
    const body = await readFile(path.join(sourceRoot, asset.path.slice(1)));
    assert.equal(asset.bytes, body.length);
    assert.equal(asset.sha256, createHash('sha256').update(body).digest('hex'));
  }
});
test('matching source and served files produce a manifest', async t => {
  const roots = await fileFixture(t);
  const manifest = await loadHaruAssetExpectations(roots);
  assert.equal(manifest.length, 4);
  assert.equal(Object.isFrozen(manifest), true);
});
test('a same-size served byte change cannot become the expectation', async t => {
  const roots = await fileFixture(t);
  await writeFile(path.join(roots.servedRoot, HARU_ASSET_PATHS[1].slice(1)), Buffer.from('changed'));
  await assert.rejects(loadHaruAssetExpectations(roots), /source\/served mismatch/);
});
test('a missing served file fails without exposing local paths', async t => {
  const roots = await fileFixture(t);
  await rm(path.join(roots.servedRoot, HARU_ASSET_PATHS[3].slice(1)));
  await assert.rejects(loadHaruAssetExpectations(roots), error => {
    assert.equal(error.message.includes(roots.servedRoot), false);
    return /reference unavailable/.test(error.message);
  });
});
for (const [name, value] of [['malformed model', secret], ['changed references', JSON.stringify({ FileReferences: {
  Moc: `https://evil.invalid/${secret}`, Textures: [] } })]]) {
  test(`${name} fails without exposing model content`, async t => {
    const roots = await fileFixture(t);
    for (const root of Object.values(roots)) await writeFile(path.join(root, HARU_ASSET_PATHS[0].slice(1)), value);
    await assert.rejects(loadHaruAssetExpectations(roots), error => !error.message.includes(secret));
  });
}

test('200, successful finish and exact response bytes are all required, with safe evidence only', async () => {
  const f = fixture(); f.others();
  const result = await f.finish();
  assert.equal(result.status, 'passed');
  assert.equal(f.bodyCalls.length, 4);
  assert.deepEqual(Object.keys(result).sort(), ['assets', 'status']);
  for (let index = 0; index < 4; index++) {
    assert.deepEqual(result.assets[index], { ...expected[index], status: 'passed', responses: [
      { status: 'verified', httpStatus: 200, bytes: expected[index].bytes, sha256: expected[index].sha256 }] });
  }
});
test('a missing resource fails, never treating other successes as complete coverage', async () => {
  const f = fixture(); f.others(0);
  const result = await f.finish();
  assert.equal(result.status, 'failed');
  assert.equal(result.assets[0].status, 'missing');
});
for (const status of [404, 302, 304, 500]) {
  test(`HTTP ${status} is a permanent failure despite a later 200 retry`, async () => {
    const f = fixture(); f.respond(0, { status }); f.others();
    const result = await f.finish();
    assert.equal(result.status, 'failed');
    assert.equal(firstStatus(result), 'http-error');
    assert.equal(result.assets[0].responses[1].status, 'verified');
    assert.equal(f.bodyCalls.length, 4);
  });
}
for (const [name, options, status] of [
  ['returned completion error', { finished: async () => new Error(secret) }, 'completion-failed'],
  ['rejected completion', { finished: async () => { throw new Error(secret); } }, 'completion-failed'],
  ['absent completion proof', { finished: async () => undefined }, 'completion-failed'],
  ['rejected body', { body: async () => { throw new Error(secret); } }, 'body-failed'],
  ['non-buffer body', { body: async () => secret }, 'body-failed'],
  ['same-size incorrect body', { body: async () => Buffer.from('wrong-0') }, 'hash-mismatch'],
  ['wrong-size body', { body: async () => Buffer.from('short') }, 'size-mismatch'],
  ['response URL does not match its request', { responseUrl: `https://evil.invalid/${secret}` }, 'invalid-response'],
  ['invalid HTTP status', { status: 999 }, 'invalid-response'],
]) {
  test(`${name} fails safely`, async () => {
    const f = fixture(); f.respond(0, options); f.others(0);
    const result = await f.finish();
    assert.equal(result.status, 'failed');
    assert.equal(firstStatus(result), status);
  });
}
test('request failure before its response cannot be erased by a successful body', async () => {
  const f = fixture(); const request = f.request(0);
  f.page.emit('requestfailed', request);
  f.respond(0, { request }); f.others(0);
  const result = await f.finish();
  assert.equal(firstStatus(result), 'request-failed');
  assert.equal(f.bodyCalls.includes(0), false);
});
test('late failure after body success cannot be erased by a later successful request', async () => {
  const f = fixture(); const request = f.respond(0); f.others(0);
  await tick();
  f.page.emit('requestfailed', request);
  f.respond(0);
  const result = await f.finish();
  assert.equal(result.status, 'failed');
  assert.equal(firstStatus(result), 'request-failed');
  assert.equal(result.assets[0].responses[1].status, 'verified');
});
test('request failure without a response is retained without reading its error', async () => {
  const f = fixture(); const request = f.request(0); f.others(0);
  f.page.emit('requestfailed', request);
  assert.equal(firstStatus(await f.finish()), 'request-failed');
});
test('a request without a response times out, with bounded wait and listener cleanup', async () => {
  const f = fixture(); f.request(0); f.others(0);
  assert.equal(firstStatus(await f.finish()), 'timed-out');
});
for (const phase of ['finished', 'body']) {
  test(`pending ${phase} times out and cannot mutate the frozen evidence later`, async () => {
    const f = fixture(); const later = deferred();
    f.respond(0, { [phase]: () => later.promise }); f.others(0);
    const result = await f.finish();
    assert.equal(firstStatus(result), 'timed-out');
    const before = JSON.stringify(result);
    later.resolve(phase === 'body' ? bodies[0] : null);
    await tick();
    assert.equal(JSON.stringify(result), before);
    assert.equal(f.bodyCalls.includes(0), false);
  });
}
test('finish waits for an actual pending body rather than reporting a request as success', async () => {
  const f = fixture(); const later = deferred();
  f.respond(0, { body: () => later.promise }); f.others(0);
  let completed = false;
  const result = f.finish(1000).then(value => { completed = true; return value; });
  await tick(); assert.equal(completed, false);
  later.resolve(bodies[0]);
  assert.equal((await result).status, 'passed');
});
test('resources arriving during the bounded finish window are included', async () => {
  const f = fixture();
  const result = f.finish(1000);
  await tick(); f.others();
  assert.equal((await result).status, 'passed');
});
test('a late failed request during the finish wait is permanent', async () => {
  const f = fixture(); const later = deferred();
  const first = f.respond(0); f.respond(1); f.respond(2); f.respond(3, { body: () => later.promise });
  const result = f.finish(1000);
  await tick(); f.page.emit('requestfailed', first); later.resolve(bodies[3]);
  assert.equal(firstStatus(await result), 'request-failed');
});
for (const [label, url] of [
  ['foreign origin', `https://evil.invalid${HARU_ASSET_PATHS[0]}?${secret}`],
  ['foreign port', `http://127.0.0.1:4176${HARU_ASSET_PATHS[0]}`],
  ['query suffix', `${origin}${HARU_ASSET_PATHS[0]}?token=${secret}`],
  ['empty query suffix', `${origin}${HARU_ASSET_PATHS[0]}?`],
  ['fragment suffix', `${origin}${HARU_ASSET_PATHS[0]}#${secret}`],
  ['userinfo', `http://${secret}@127.0.0.1:4175${HARU_ASSET_PATHS[0]}`],
  ['encoded path', `${origin}${HARU_ASSET_PATHS[0].replace('haru-receptionist', '%68aru-receptionist')}`],
  ['lookalike name', `${origin}${HARU_ASSET_PATHS[0]}.png`],
]) {
  test(`${label} cannot satisfy an asset or leak into evidence`, async () => {
    const f = fixture(); f.respond(0, { url }); f.others(0);
    const result = await f.finish();
    assert.equal(result.assets[0].status, 'missing');
    assert.equal(f.bodyCalls.includes(0), false);
  });
}
test('a non-GET request cannot satisfy an asset', async () => {
  const f = fixture(); f.respond(0, { method: 'POST' }); f.others(0);
  assert.equal(firstStatus(await f.finish()), 'unexpected-method');
});
test('observation overflow fails closed and keeps public evidence bounded', async () => {
  const f = fixture(); for (let index = 0; index < 40; index++) f.respond(0); f.others(0);
  const result = await f.finish();
  assert.equal(result.status, 'failed');
  assert.equal(result.assets[0].status, 'observation-limit');
  assert.equal(result.assets[0].responses.length, 32);
});
test('dispose releases listeners and the active bounded wait; finish is idempotent', async () => {
  const f = fixture(); f.request(0); f.others(0);
  const one = f.observer.finish({ timeoutMs: 30000 });
  assert.equal(f.observer.finish(), one);
  f.observer.dispose(); f.observer.dispose(); f.clean();
  const result = await one;
  assert.equal(firstStatus(result), 'disposed');
  assert.equal(await f.observer.finish(), result);
});
test('a completion rejection after disposal is consumed without mutating evidence', async () => {
  const f = fixture(); const later = deferred();
  f.respond(0, { finished: () => later.promise }); f.others(0);
  f.observer.dispose();
  const result = await f.finish();
  later.reject(new Error(secret)); await tick();
  assert.equal(firstStatus(result), 'disposed');
});
test('invalid contracts and unbounded timeouts are rejected before observation', () => {
  for (const contract of [
    { origin: 'https://example.invalid', expected },
    { origin, expected: expected.map((asset, index) => index ? asset : { ...asset, path: `/${secret}` }) },
    { origin, expected: expected.slice(1) },
  ]) {
    const page = new EventEmitter();
    assert.throws(() => observeHaruAssets(page, contract), /Invalid Haru/);
    assert.equal(page.eventNames().length, 0);
  }
  const f = fixture();
  for (const timeoutMs of [Infinity, -1, 30001, NaN]) assert.throws(() => f.observer.finish({ timeoutMs }), /Invalid Haru/);
  f.observer.dispose(); f.clean();
});


test('browser asset completion is awaited before all existing final safety guards', async () => {
  const source = await readFile(new URL('../../../web/tests/simplification/run.mjs', import.meta.url), 'utf8');
  const work = source.indexOf('    await work({ page, api, check, result, snap:');
  const finish = source.indexOf('    result.live2dAssets = await haruAssets.finish();', work);
  const guards = [
    'No unmocked API endpoint',
    'No external network or websocket attempt',
    'No uncaught browser runtime error',
    'Every write is explicitly synthetic or validated content-free presence',
  ].map(label => source.indexOf(`    check('${label}'`, work));
  const assetAssertion = source.indexOf("    assert.equal(result.live2dAssets.status, 'passed'", work);
  const passed = source.indexOf("    result.status = 'passed';", work);
  const ordered = [work, finish, ...guards, assetAssertion, passed];
  assert.ok(ordered.every(position => position >= 0));
  assert.deepEqual([...ordered].sort((left, right) => left - right), ordered);
  assert.equal(new Set(ordered).size, ordered.length);
  assert.match(source.slice(guards[3], assetAssertion),
    /api\.requests\.filter\(request => request\.method !== 'GET'\)\s*\.every\(request => allowedWrites\.has\(`\$\{request\.method\} \$\{request\.path\}`\) \|\| isContentFreePresenceRequest\(request\)\)/);

});


test('the current Windows workflow executes the installed entry point containing the asset hook', async () => {
  const workflow = await readFile(new URL('../../../.github/workflows/desktop-localization.yml', import.meta.url), 'utf8');
  assert.match(workflow, /^\s+node desktop\/localization-ui\/smoke\.mjs\s*$/m);
  const source = await readFile(new URL('../../localization-ui/smoke.mjs', import.meta.url), 'utf8');
  assert.match(source, /import \{ loadHaruAssetExpectations, observeHaruAssets \} from '\.\.\/live2d-assets\.mjs';/);
  assert.match(source, /sourceRoot: path\.join\(productRoot, 'web', 'public'\), servedRoot: path\.join\(paths\.install, 'resources', 'web'\)/);
  assert.match(source, /active\.info\.live2dAssets = await haruAssets\.finish\(\)/);
  assert.match(source, /assert\.equal\(active\.info\.live2dAssets\.status, 'passed'/);
});
test('installed asset reload is first-launch only, after guards and before Haru expansion, with cleanup', async () => {
  const source = await readFile(new URL('../../localization-ui/smoke.mjs', import.meta.url), 'utf8');
  const launch = source.slice(source.indexOf('async function launch(number, locale)'), source.indexOf('async function assertLocale('));
  const match = launch.match(/  if \(number === 1\) \{([\s\S]*?)\n  \}/);
  assert.ok(match, 'the asset observation block must run only on first launch');
  assert.equal((source.match(/\.reload\(/g) || []).length, 1, 'no restart or other surface may add a reload');
  const hook = match[1];
  const positions = [
    launch.indexOf('await installRequestGuard(app)'),
    launch.indexOf("assert.equal(url.origin, active.origin"),
    launch.indexOf("assert.deepEqual(security.unsafeSwitches, []"),
    launch.indexOf('assertDevToolsProbe(detail.devToolsProbe)'),
    launch.indexOf('validateSockets(snapshot, owned[1].pid, active.port)'),
    launch.indexOf('active.info.haruApiReadIsolation ='),
    match.index,
    launch.indexOf("await launchPhase('observe-tray-and-expand-haru')"),
    launch.indexOf("await active.haru.getByRole('button', { name: TEXT[locale].expand"),
  ];
  assert.ok(positions.every(position => position >= 0));
  assert.deepEqual([...positions].sort((left, right) => left - right), positions);
  const observer = hook.indexOf('const haruAssets = observeHaruAssets(');
  const reload = hook.indexOf("await active.haru.reload({ waitUntil: 'domcontentloaded' })");
  const finish = hook.indexOf('active.info.live2dAssets = await haruAssets.finish()');
  const safety = hook.indexOf('ensureSafe()', finish);
  const assertion = hook.indexOf("assert.equal(active.info.live2dAssets.status, 'passed'", finish);
  assert.ok(observer >= 0 && observer < reload && reload < finish && finish < safety && safety < assertion);
  assert.match(hook, /finally \{\s+active\.info\.live2dAssets \|\|= await haruAssets\.finish\(\);\s+haruAssets\.dispose\(\);\s+await saveReport\(\);/);
  assert.deepEqual([...source.matchAll(/await launch\((\d),/g)].map(match => Number(match[1])), [1, 2, 3]);
  assert.match(source, /nativeMenuPointerValidated: false, unsignedSmartScreenValidated: false, undebuggedLaunchValidated: false/);
  assert.match(source, /await waitForPaintedCharacter\(active\.haru\)/);
});


for (const source of ['query', 'foreign-origin']) {
  test(`provenance: ${source} redirect into the exact asset URL fails despite correct final bytes and retry`, async () => {
    const f = fixture();
    const previous = f.request(0, { url: source === 'query' ? `${origin}${HARU_ASSET_PATHS[0]}?token=${secret}`
      : `https://elsewhere.invalid${HARU_ASSET_PATHS[0]}` });
    f.respond(0, { request: previous, status: 302 });
    // Only the existence of a predecessor is relevant. Never inspect its URL,
    // headers, method, or any other data once it becomes redirect provenance.
    previous.url = () => { throw new Error(secret); };
    f.respond(0, { redirectedFrom: previous });
    f.others();
    const result = await f.finish();
    assert.equal(result.status, 'failed');
    assert.equal(firstStatus(result), 'redirected-request');
    assert.equal(result.assets[0].responses[1].status, 'verified');
    assert.equal(f.bodyCalls.filter(index => index === 0).length, 1);
  });
}
test('provenance: response-only events cannot satisfy any required asset', async () => {
  const f = fixture();
  for (let index = 0; index < 4; index++) f.respond(index, { observed: false });
  const result = await f.finish();
  assert.equal(result.status, 'failed');
  assert.ok(result.assets.every(asset => asset.status === 'missing' && asset.responses.length === 0));
  assert.deepEqual(f.bodyCalls, []);
});
test('provenance: failure-only old request events are ignored instead of creating records', async () => {
  const f = fixture();
  const old = f.request(0, { observed: false });
  f.page.emit('requestfailed', old); f.others(0);
  const result = await f.finish();
  assert.equal(result.status, 'failed');
  assert.equal(result.assets[0].status, 'missing');
  assert.deepEqual(result.assets[0].responses, []);
});
test('provenance: an old failure followed by its response still cannot create an observed request', async () => {
  const f = fixture();
  const old = f.request(0, { observed: false });
  f.page.emit('requestfailed', old); f.respond(0, { request: old }); f.others(0);
  const result = await f.finish();
  assert.equal(result.assets[0].status, 'missing');
  assert.deepEqual(result.assets[0].responses, []);
});
test('provenance: only current-window direct requests count, old events cannot satisfy or poison them', async () => {
  const f = fixture();
  const old = f.request(0, { observed: false });
  f.page.emit('requestfailed', old); f.respond(0, { request: old });
  f.others(); await tick();
  f.page.emit('requestfailed', old);
  const result = await f.finish();
  assert.equal(result.status, 'passed');
  assert.ok(result.assets.every(asset => asset.responses.length === 1 && asset.responses[0].status === 'verified'));
  assert.equal(f.bodyCalls.length, 4);
});
test('provenance: a same-URL response with a different request identity cannot finish an observed request', async () => {
  const f = fixture(); f.request(0);
  f.respond(0, { observed: false }); f.others(0);
  const result = await f.finish();
  assert.equal(firstStatus(result), 'timed-out');
  assert.equal(result.assets[0].responses.length, 1);
  assert.equal(f.bodyCalls.includes(0), false);
});
