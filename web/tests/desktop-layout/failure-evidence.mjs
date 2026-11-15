import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

// Browser QA uses synthetic data, but arbitrary console text, URL parameters and
// DOM values still do not belong in CI artifacts. Keep structure and error kinds.
export function messageKind(value) {
  const text = String(value);
  if (/TimeoutError|Timeout \d+ms exceeded/.test(text)) return 'timeout';
  if (/AssertionError/.test(text)) return 'assertion';
  if (/Invalid hook call|dispatcher.*null/i.test(text)) return 'react-hook-error';
  if (/does not provide an export|Failed to fetch dynamically imported module|outdated optimized dep/i.test(text)) return 'module-load-error';
  if (/Content Security Policy|unsafe-eval/i.test(text)) return 'content-security-policy';
  if (/Simulated model failure/.test(text)) return 'expected-fixture-model-failure';
  return 'unclassified';
}

export function resourceName(value) {
  try {
    const url = new URL(value);
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) return '[non-loopback]';
    // Never retain credentials, query strings, fragments or API identifiers.
    const knownPaths = ['/src/main.tsx', '/tests/desktop-layout/fixture.html', '/tests/desktop-layout/fixture.tsx', '/@vite/client', '/@react-refresh', '/favicon.ico'];
    if (knownPaths.includes(url.pathname)) return url.pathname;
    const category = /^\/(src|node_modules|tests\/desktop-layout|assets|vendor)\//.exec(url.pathname)?.[1];
    if (category) return `${category}:[${createHash('sha256').update(url.pathname).digest('hex').slice(0, 12)}]`;
    return '[other-loopback]';
  } catch { return '[unavailable]'; }
}

// Serialized by Playwright; it deliberately depends only on browser globals.
export function collectLayoutState() {
  const root = document.getElementById('root');
  const table = document.querySelector('.ant-table-content');
  const mascot = document.querySelector('aside[aria-label="Haru 助手"]');
  const rawResult = document.querySelector('[data-layout-result]')?.getAttribute('data-layout-result');
  const knownResults = ['pending', 'passed', 'primary column collapsed', 'text expanded row height', 'horizontal scroll unavailable', 'page overflow', 'fallback retains invisible hit area', 'fallback overlaps table or pagination', 'hidden mascot remains interactive'];
  const result = rawResult == null ? null : rawResult.split('; ').every(part => knownResults.includes(part)) ? rawResult : '[other]';
  const clone = (document.body ?? document.createElement('body')).cloneNode(true);
  clone.querySelectorAll('script,style,link,meta,template').forEach(node => node.remove());
  const walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_COMMENT);
  const discard = [];
  while (walker.nextNode()) discard.push(walker.currentNode);
  discard.forEach(node => node.remove());
  for (const node of [clone, ...clone.querySelectorAll('*')]) {
    const isRoot = node.id === 'root';
    const knownClasses = ['ant-table-content', 'ant-table-tbody', 'ant-table', 'op-app-shell', 'op-app-main', 'op-app-content', 'op-topbar'];
    const classes = [...node.classList].filter(name => knownClasses.includes(name));
    const flags = ['data-layout-result', 'data-pilot-mascot-fallback-dock', 'data-pilot-mascot-global-dock', 'data-load-failed', 'data-runtime-ready'].filter(name => node.hasAttribute(name));
    [...node.attributes].forEach(attribute => node.removeAttribute(attribute.name));
    if (isRoot) node.id = 'root';
    if (classes.length) node.className = classes.join(' ');
    for (const flag of flags) node.setAttribute(flag, '');
  }
  return {
    readyState: document.readyState,
    visibilityState: document.visibilityState,
    rootPresent: Boolean(root),
    rootChildren: root?.childElementCount ?? null,
    layoutResult: result,
    tablePresent: Boolean(table),
    tableClientWidth: table?.clientWidth ?? null,
    tableScrollWidth: table?.scrollWidth ?? null,
    mascotPresent: Boolean(mascot),
    mascotLoadFailed: mascot?.getAttribute('data-load-failed') === 'true',
    fallbackDock: Boolean(mascot?.parentElement?.hasAttribute('data-pilot-mascot-fallback-dock')),
    globalDock: Boolean(mascot?.parentElement?.hasAttribute('data-pilot-mascot-global-dock')),
    viteErrorOverlay: Boolean(document.querySelector('vite-error-overlay')),
    dom: clone.outerHTML,
  };
}

export function observeLayoutFailure(page, output, name, { maxEvents = 200, captureTimeoutMs = 2000 } = {}) {
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,119}$/.test(name)) throw new Error('Unsafe evidence name');
  const started = Date.now();
  const events = [];
  const pending = new Set();
  const listeners = [];
  let droppedEvents = 0;
  const record = (type, values = {}) => {
    if (events.length >= maxEvents) { events.shift(); droppedEvents++; }
    events.push({ ms: Date.now() - started, type, ...values });
  };
  const listen = (target, type, callback) => { target.on(type, callback); listeners.push([target, type, callback]); };
  const on = (type, callback) => listen(page, type, callback);
  on('console', item => {
    if (['warning', 'error'].includes(item.type())) record('console', { level: item.type(), kind: messageKind(item.text()), resource: resourceName(item.location().url) });
  });
  on('pageerror', error => record('pageerror', { kind: messageKind(error) }));
  on('crash', () => record('crash'));
  on('request', request => pending.add(request));
  on('requestfinished', request => pending.delete(request));
  on('requestfailed', request => { pending.delete(request); record('requestfailed', { resource: resourceName(request.url()), resourceType: request.resourceType() }); });
  on('response', response => { if (response.status() >= 400) record('http-error', { status: response.status(), resource: resourceName(response.url()) }); });
  on('framenavigated', frame => { if (frame === page.mainFrame()) record('navigation', { resource: resourceName(frame.url()) }); });
  on('websocket', socket => {
    listen(socket, 'framereceived', data => {
      try { if (JSON.parse(String(data.payload)).type === 'full-reload') record('vite-full-reload'); } catch { /* No raw WebSocket payloads. */ }
    });
    listen(socket, 'socketerror', () => record('websocket-error'));
  });
  return {
    dispose() { for (const [target, type, callback] of listeners) target.off(type, callback); listeners.length = 0; pending.clear(); },
    async capture(stage, error) {
      // Evidence must never replace the original failed result or raise a retry.
      const captureErrors = [];
      let state = null;
      let domWritten = false;
      let timer;
      let captureTimedOut = false;
      try {
        state = await Promise.race([
          page.evaluate(collectLayoutState),
          new Promise((_, reject) => { timer = setTimeout(() => { captureTimedOut = true; reject(new Error('DOM capture deadline')); }, captureTimeoutMs); }),
        ]);
      } catch { captureErrors.push(captureTimedOut ? 'dom-capture-timeout' : 'dom-unavailable'); }
      finally { clearTimeout(timer); }
      const diagnostic = `${name}-failed.json`;
      const dom = `${name}-failed.dom.html`;
      try {
        await mkdir(output, { recursive: true });
        if (state) {
          try { await writeFile(path.join(output, dom), state.dom); domWritten = true; } catch { captureErrors.push('dom-write-failed'); }
          delete state.dom;
        }
        await writeFile(path.join(output, diagnostic), JSON.stringify({ schemaVersion: 1, name, stage, errorKind: messageKind(error), state, events, droppedEvents, pending: [...pending].slice(0, 200).map(request => ({ resource: resourceName(request.url()), resourceType: request.resourceType() })), captureErrors }, null, 2));
        return { diagnostic, ...(domWritten ? { diagnosticDom: dom } : {}), ...(captureErrors.length ? { diagnosticCaptureErrors: captureErrors } : {}) };
      } catch { return { diagnosticCaptureErrors: [...captureErrors, 'evidence-write-failed'] }; }
    },
  };
}

export async function captureFailureScreenshot(page, output, name) {
  try {
    await page.screenshot({ path: path.join(output, `${name}-failed.png`), animations: 'disabled' });
    return {};
  } catch { return { screenshotUnavailable: true }; }
}
