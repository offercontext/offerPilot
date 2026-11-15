import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';

// This is deliberately not a general network recorder. Only these four public
// production Haru assets can enter evidence, never raw URLs, headers or errors.
const BASE = '/live2d/haru-receptionist/';
const MODEL = 'haru_greeter_t03.model3.json';
const MOC = 'haru_greeter_t03.moc3';
const TEXTURES = ['haru_greeter_t03.2048/texture_00.png', 'haru_greeter_t03.2048/texture_01.png'];
export const HARU_ASSET_PATHS = Object.freeze([MODEL, MOC, ...TEXTURES].map(file => BASE + file));
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const MAX_REQUESTS_PER_ASSET = 32;
const pending = status => status.startsWith('pending-');

export async function loadHaruAssetExpectations({ sourceRoot, servedRoot }) {
  const assets = [];
  for (const assetPath of HARU_ASSET_PATHS) {
    let source, served;
    try {
      [source, served] = await Promise.all([sourceRoot, servedRoot].map(root => readFile(path.join(root, assetPath.slice(1)))));
    } catch { throw new Error(`Haru asset reference unavailable: ${assetPath}`); }
    if (!source.equals(served)) throw new Error(`Haru asset source/served mismatch: ${assetPath}`);
    if (assetPath === HARU_ASSET_PATHS[0]) {
      let model;
      try { model = JSON.parse(source.toString('utf8')); }
      catch { throw new Error('Haru model reference is not valid JSON'); }
      if (model?.FileReferences?.Moc !== MOC || JSON.stringify(model?.FileReferences?.Textures) !== JSON.stringify(TEXTURES)) {
        throw new Error('Haru model references differ from the four-asset contract');
      }
    }
    assets.push(Object.freeze({ path: assetPath, bytes: source.length, sha256: sha256(source) }));
  }
  return Object.freeze(assets);
}

export function observeHaruAssets(page, { origin, expected }) {
  let parsedOrigin;
  try { parsedOrigin = new URL(origin); } catch { /* Reject below without exposing input. */ }
  if (parsedOrigin?.origin !== origin || parsedOrigin.protocol !== 'http:' || parsedOrigin.hostname !== '127.0.0.1'
    || !Array.isArray(expected) || expected.length !== HARU_ASSET_PATHS.length
    || expected.some((asset, index) => asset.path !== HARU_ASSET_PATHS[index]
      || !Number.isSafeInteger(asset.bytes) || asset.bytes <= 0 || !/^[a-f0-9]{64}$/.test(asset.sha256))) {
    throw new Error('Invalid Haru asset observation contract');
  }
  const assets = expected.map(asset => ({ ...asset, requests: [], overflow: false }));
  const byUrl = new Map(assets.map(asset => [origin + asset.path, asset]));
  const byRequest = new WeakMap();
  const waiters = new Set();
  let stopped = false;
  let finishing;
  const changed = () => { for (const wake of [...waiters]) wake(); };
  // Only a request event inside this observation window establishes provenance.
  // Late response/failure events for older in-flight requests cannot create it.
  const find = request => stopped ? null : byRequest.get(request);
  const onRequest = request => {
    if (stopped || byRequest.has(request)) return;
    // Exact URL equality excludes query/fragment, credentials, encoded paths,
    // another origin/port and lookalike names; none can satisfy a required asset.
    const asset = byUrl.get(request.url());
    if (!asset) return;
    if (asset.requests.length >= MAX_REQUESTS_PER_ASSET) { asset.overflow = true; changed(); return; }
    // A redirect into an allowlisted URL is still a redirect, not a direct load.
    // Inspect only predecessor presence, never its URL or other private data.
    const entry = { status: request.redirectedFrom() !== null ? 'redirected-request'
      : request.method() === 'GET' ? 'pending-response' : 'unexpected-method',
      httpStatus: null, bytes: null, sha256: null };
    asset.requests.push(entry);
    byRequest.set(request, { asset, entry });
    changed();
  };
  const onFailure = request => {
    const match = find(request);
    if (match) { match.entry.status = 'request-failed'; changed(); }
  };
  const onResponse = response => {
    const request = response.request();
    const match = find(request);
    if (!match) return;
    const { asset, entry } = match;
    // A failure is permanent for this request, even if a response/body arrives
    // later. A successful retry is a separate entry and cannot erase it either.
    if (entry.status !== 'pending-response') return;
    const status = response.status();
    entry.httpStatus = Number.isInteger(status) && status >= 100 && status <= 599 ? status : null;
    if (response.url() !== origin + asset.path || entry.httpStatus === null) {
      entry.status = 'invalid-response'; changed(); return;
    }
    if (status !== 200) { entry.status = 'http-error'; changed(); return; }
    entry.status = 'pending-completion';
    void (async () => {
      try {
        const failure = await response.finished();
        if (stopped || entry.status !== 'pending-completion') return;
        if (failure !== null) { entry.status = 'completion-failed'; return; }
        entry.status = 'pending-body';
        const body = await response.body();
        if (stopped || entry.status !== 'pending-body') return;
        if (!Buffer.isBuffer(body)) { entry.status = 'body-failed'; return; }
        entry.bytes = body.length;
        entry.sha256 = sha256(body);
        entry.status = body.length !== asset.bytes ? 'size-mismatch'
          : entry.sha256 !== asset.sha256 ? 'hash-mismatch' : 'verified';
      } catch {
        if (!stopped && pending(entry.status)) {
          entry.status = entry.status === 'pending-body' ? 'body-failed' : 'completion-failed';
        }
      } finally { changed(); }
    })();
  };
  page.on('request', onRequest);
  page.on('response', onResponse);
  page.on('requestfailed', onFailure);

  const complete = () => assets.every(asset => asset.requests.length > 0 && asset.requests.every(entry => !pending(entry.status)));
  const stop = status => {
    if (stopped) return;
    stopped = true;
    page.off('request', onRequest);
    page.off('response', onResponse);
    page.off('requestfailed', onFailure);
    for (const asset of assets) for (const entry of asset.requests) if (pending(entry.status)) entry.status = status;
    changed();
  };
  const snapshot = () => {
    const records = assets.map(asset => ({ path: asset.path, bytes: asset.bytes, sha256: asset.sha256,
      status: asset.overflow ? 'observation-limit' : asset.requests.length === 0 ? 'missing'
        : asset.requests.every(entry => entry.status === 'verified') ? 'passed' : 'failed',
      responses: asset.requests.map(entry => ({ ...entry })) }));
    return { status: records.every(asset => asset.status === 'passed') ? 'passed' : 'failed', assets: records };
  };
  const waitForChange = ms => new Promise(resolve => {
    const wake = () => { clearTimeout(timer); waiters.delete(wake); resolve(); };
    const timer = setTimeout(wake, ms);
    waiters.add(wake);
  });
  return {
    // Observe until the caller's existing UI work ends, then give unfinished or
    // missing resources a bounded chance to complete. No new request or reload.
    finish({ timeoutMs = 10000 } = {}) {
      if (finishing) return finishing;
      if (!Number.isFinite(timeoutMs) || timeoutMs < 0 || timeoutMs > 30000) throw new Error('Invalid Haru observation timeout');
      finishing = (async () => {
        const deadline = performance.now() + timeoutMs;
        while (!stopped && !complete()) {
          const remaining = deadline - performance.now();
          if (remaining <= 0) break;
          await waitForChange(remaining);
        }
        stop('timed-out');
        return snapshot();
      })();
      return finishing;
    },
    dispose() { stop('disposed'); },
  };
}
