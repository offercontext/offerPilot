import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { createMockScreenshots, SCREEN_IDS, SKIP_CODES } from '../mock-screenshots.mjs';

// Local fake-Page tests execute the actual renderer guard in a separate VM.
// They neither launch an EXE nor establish installed Windows UI evidence.
class Node {
  constructor({ text = '', tag = 'div', attributes = {}, visible = true, value = '' } = {}) {
    this.text = text; this.tagName = tag.toUpperCase(); this.attributes = attributes;
    this.visible = visible; this.value = value; this.labels = []; this.selectors = new Map();
  }
  get textContent() { return this.text; }
  get innerText() { return this.text; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  getClientRects() { return this.visible ? [{}] : []; }
  add(selector, node = new Node()) {
    this.selectors.set(selector, [...(this.selectors.get(selector) || []), node]); return node;
  }
  querySelectorAll(selector) {
    if (selector === '*') return [...new Set([...this.selectors.values()].flat())];
    return [...new Set(selector.split(',').flatMap(part => this.selectors.get(part.trim()) || []))];
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getElementById(id) { return this.querySelectorAll('*').find(node => node.getAttribute('id') === id) || null; }
}
const TOKEN = 'synthetic-case-token-1234567890';
function fakePage(stage = 'pilot-stream', { haru = false, screenshotError = false, evaluateError = false } = {}) {
  const document = new Node(); document.body = new Node({ text: 'synthetic mock result' }); document.visibilityState = 'visible';
  const state = { connected: true, snapshot: { taskState: 'idle', loading: false, hasPending: false,
    canStop: false, error: '', stopMessage: stage === 'pilot-cancel' ? '任务已停止。' : '' } };
  const bridge = { role: haru ? 'haru' : 'owner', getState: async () => state };
  let surface;
  if (haru) {
    document.add('main[aria-label="Haru 桌面小窗"]');
    surface = document.add('section[aria-label="Haru 对话"]');
    surface.add('article[data-role="assistant"] p', new Node({ text: stage === 'pilot-hitl-reject' ? '已拒绝，未执行' : 'Mock response' }));
  } else if (stage.startsWith('pilot-')) {
    document.add('.op-app-main-pilot');
    surface = document.add('.op-pilot-page-host [data-onboarding-target="pilot"]');
    surface.add('textarea[placeholder="问问领航员，或输入 / 唤起能力"]');
    surface.add('[class*="bubbleAssistant"]', new Node({ text: stage === 'pilot-hitl-reject' ? '已拒绝，未执行' : 'Mock response' }));
  } else {
    const entries = {
      'interview-preparation': ['面试', 'section[aria-label="面试准备建议"]'],
      'resume-structure': ['素材库', '[role="dialog"]'],
      'offer-negotiation': ['Offer', '[data-testid="offer-negotiation-drawer"]'],
    };
    const [nav, selector] = entries[stage];
    document.add('nav[aria-label="主导航"]');
    document.add(`nav[aria-label="主导航"] [aria-current="page"][aria-label="${nav}"]`);
    surface = document.add(selector, new Node({ text: 'AI 简历分类与核对' }));
    if (stage === 'interview-preparation') { surface.add('h3', new Node({ text: '准备方向' })); surface.add('article'); }
    if (stage === 'resume-structure') { surface.add('section[aria-label="分类候选"]'); surface.add('textarea'); }
    if (stage === 'offer-negotiation') { surface.add('[aria-label="谈薪准备草稿"]'); surface.add('[data-testid="offer-negotiation-confirm"]'); }
  }
  if (stage === 'pilot-cancel') surface.add('[role="status"]', new Node({ text: '任务已停止。' }));
  const calls = [];
  let afterScreenshot;
  return {
    document, bridge, state, surface, calls,
    setAfterScreenshot(fn) { afterScreenshot = fn; },
    async evaluate(fn, args) {
      calls.push({ method: 'evaluate' });
      if (evaluateError) throw new Error(`${TOKEN} private-page-url`);
      const result = await vm.runInNewContext(`(${fn.toString()})(args)`, {
        args, document, window: { offerpilotDesktop: bridge },
        getComputedStyle: node => ({ visibility: 'visible', display: node.visible ? 'block' : 'none' }),
      });
      assert.equal(typeof result, 'boolean', 'no renderer data may escape the guard');
      return result;
    },
    async screenshot(options) {
      calls.push({ method: 'screenshot', options });
      if (screenshotError) throw new Error(`${TOKEN} private-browser-details`);
      afterScreenshot?.();
      return Buffer.from('synthetic-png-bytes');
    },
  };
}
async function setup(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'mock-screens-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const screenshots = createMockScreenshots({ directory, mode: 'mock' });
  assert.equal(screenshots.registerToken(TOKEN), true);
  return { directory, screenshots };
}
const countCaptures = page => page.calls.filter(call => call.method === 'screenshot').length;
async function absent(directory) {
  assert.deepEqual(await fs.readdir(directory), [], 'no image or metadata should have been persisted');
}

test('only strict mock mode and a bounded absolute output directory construct a collector', () => {
  for (const mode of [undefined, 'MOCK', 'live', true]) {
    assert.throws(() => createMockScreenshots({ directory: os.tmpdir(), mode }), { code: 'SCREEN_MODE_INVALID' });
  }
  for (const directory of [undefined, '', '.', '../unsafe', path.parse(os.tmpdir()).root, '/tmp/bad\0path']) {
    assert.throws(() => createMockScreenshots({ directory, mode: 'mock' }), { code: 'SCREEN_DIRECTORY_UNSAFE' });
  }
});

test('all fixed successful surfaces capture naturally and expose only safe inventory', async t => {
  const { directory, screenshots } = await setup(t);
  for (const id of SCREEN_IDS.filter(id => !id.startsWith('failure-'))) {
    const stage = id.replace(/^haru-/, '');
    const page = fakePage(stage, { haru: id.startsWith('haru-') });
    assert.deepEqual(await screenshots.capture(id, page, { stage }), { status: 'captured', code: 'SCREEN_CAPTURED' }, id);
    assert.equal(countCaptures(page), 1);
    assert.deepEqual(page.calls.find(call => call.method === 'screenshot').options,
      { type: 'png', animations: 'allow', fullPage: false, timeout: 15000 });
    assert.equal(page.calls.filter(call => call.method === 'evaluate').length, 2);
    assert.equal(await fs.readFile(path.join(directory, 'screens', `${id}.png`), 'utf8'), 'synthetic-png-bytes');
  }
  const snapshot = screenshots.snapshot();
  assert.deepEqual(snapshot, { captured: SCREEN_IDS.filter(id => !id.startsWith('failure-')), skipped: [] });
  assert.doesNotMatch(JSON.stringify(snapshot), /synthetic-case-token|mock-screens-test|private/);
  snapshot.captured.push('untrusted');
  assert.equal(screenshots.snapshot().captured.includes('untrusted'), false);
});

test('credential forms and controls are refused even when empty or hidden', async t => {
  const { directory, screenshots } = await setup(t);
  const cases = [
    page => page.document.add('section[aria-label="AI 设置"]', new Node({ visible: false })),
    page => page.document.add('[data-testid="ai-provider-list"]'),
    page => page.document.add('input[type="password"]', new Node({ visible: false })),
    page => page.document.add('input[autocomplete="current-password"]'),
    page => page.document.add('input', new Node({ tag: 'input', attributes: { id: 'api_key' } })),
    page => { const control = page.document.add('input', new Node({ tag: 'input' })); control.labels = [new Node({ text: 'API 密钥' })]; },
    page => page.document.add('form', new Node({ tag: 'form', text: 'Credential setup' })),
    page => page.document.add('input', new Node({ tag: 'input', attributes: { placeholder: 'Paste API key' } })),
    page => { page.document.add('label', new Node({ text: '密码', attributes: { id: 'secret-label' } })); page.document.add('textarea', new Node({ tag: 'textarea', attributes: { 'aria-labelledby': 'secret-label' } })); },
  ];
  for (const edit of cases) {
    const page = fakePage(); edit(page);
    assert.deepEqual(await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' }), { status: 'skipped', code: 'SCREEN_GUARD_REJECTED' });
    assert.equal(countCaptures(page), 0);
  }
  await absent(directory);
});

test('broker tokens in body, visible values or display attributes refuse capture', async t => {
  const { directory, screenshots } = await setup(t);
  const nextToken = 'next-random-case-token-987654'; screenshots.registerToken(nextToken);
  for (const edit of [
    page => { page.document.body.text = `result ${TOKEN}`; },
    page => page.document.add('input', new Node({ tag: 'input', value: nextToken })),
    page => page.document.add('textarea', new Node({ tag: 'textarea', attributes: { placeholder: TOKEN } })),
    page => { page.document.body.text = 'Bearer private-looking-credential-value'; },
  ]) {
    const page = fakePage(); edit(page);
    assert.equal((await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })).code, 'SCREEN_GUARD_REJECTED');
    assert.equal(countCaptures(page), 0);
  }
  await absent(directory);
  assert.doesNotMatch(JSON.stringify(screenshots.snapshot()), /random-case-token|synthetic-case-token|Bearer/);
});

test('missing and invalid token registration fail closed', async t => {
  const { directory } = await setup(t);
  const screenshots = createMockScreenshots({ directory, mode: 'mock' });
  const page = fakePage();
  assert.equal((await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })).code, 'SCREEN_TOKEN_INVALID');
  assert.equal(screenshots.registerToken(''), false);
  assert.equal(screenshots.registerToken(TOKEN), true);
  assert.equal((await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })).code, 'SCREEN_TOKEN_INVALID');
  assert.equal(page.calls.length, 0); await absent(directory);
});

test('untrusted IDs, connection and mismatched stages never reach the renderer or filenames', async t => {
  const { directory, screenshots } = await setup(t); const page = fakePage();
  for (const id of ['../credentials', '/tmp/pilot-stream', 'pilot-stream.png', TOKEN, 'connection', '__proto__', undefined]) {
    assert.deepEqual(await screenshots.capture(id, page, { stage: 'pilot-stream' }), { status: 'skipped', code: 'SCREEN_ID_INVALID' });
  }
  for (const [id, stage] of [['pilot-stream', 'pilot-cancel'], ['pilot-stream', undefined], ['failure-owner', 'connection'], ['failure-haru', TOKEN]]) {
    assert.equal((await screenshots.capture(id, page, { stage })).code, 'SCREEN_STAGE_INVALID');
  }
  assert.equal(page.calls.length, 0); await absent(directory);
  for (const row of screenshots.snapshot().skipped) { assert.ok(SCREEN_IDS.includes(row.id)); assert.ok(SKIP_CODES.includes(row.code)); }
  assert.equal(JSON.stringify(screenshots.snapshot()).includes(TOKEN), false);
});

test('unknown, wrong-role, inactive and result-free surfaces refuse capture', async t => {
  const { directory, screenshots } = await setup(t);
  for (const edit of [
    page => { page.bridge.role = 'haru'; },
    page => { page.document.visibilityState = 'hidden'; },
    page => { page.document.selectors.delete('.op-app-main-pilot'); },
    page => { page.surface.selectors.delete('[class*="bubbleAssistant"]'); },
    page => { page.state.snapshot.loading = true; },
    page => page.document.add('iframe'),
    page => { page.document.add('div').shadowRoot = {}; },
    page => page.document.add('nav[aria-label="主导航"] [aria-current="page"][aria-label="设置"]'),
  ]) {
    const page = fakePage(); edit(page);
    assert.equal((await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })).code, 'SCREEN_GUARD_REJECTED');
    assert.equal(countCaptures(page), 0);
  }
  const resume = fakePage('resume-structure');
  resume.document.selectors.delete('nav[aria-label="主导航"] [aria-current="page"][aria-label="素材库"]');
  assert.equal((await screenshots.capture('resume-structure', resume, { stage: 'resume-structure' })).code, 'SCREEN_GUARD_REJECTED');
  await absent(directory);
});

test('controlled failure capture still enforces credential and known-surface boundaries', async t => {
  const { screenshots } = await setup(t);
  const unsafe = fakePage(); unsafe.document.add('input[type="password"]');
  assert.equal((await screenshots.capture('failure-owner', unsafe, { stage: 'pilot-stream' })).code, 'SCREEN_GUARD_REJECTED');
  const owner = fakePage(); owner.surface.selectors.delete('[class*="bubbleAssistant"]');
  assert.equal((await screenshots.capture('failure-owner', owner, { stage: 'pilot-stream' })).status, 'captured');
  const haru = fakePage('pilot-stream', { haru: true }); haru.state.snapshot.loading = true;
  assert.equal((await screenshots.capture('failure-haru', haru, { stage: 'pilot-stream' })).status, 'captured');
});

test('renderer and screenshot exceptions become fixed skip codes without raw errors', async t => {
  const { directory, screenshots } = await setup(t);
  for (const [options, code] of [[{ evaluateError: true }, 'SCREEN_GUARD_FAILED'], [{ screenshotError: true }, 'SCREEN_CAPTURE_FAILED']]) {
    const result = await screenshots.capture('pilot-stream', fakePage('pilot-stream', options), { stage: 'pilot-stream' });
    assert.deepEqual(result, { status: 'skipped', code });
  }
  assert.doesNotMatch(JSON.stringify(screenshots.snapshot()), /private|synthetic-case-token/); await absent(directory);
});

test('a credential or token appearing during screenshot discards bytes before disk write', async t => {
  const { directory, screenshots } = await setup(t);
  for (const edit of [page => { page.document.body.text = TOKEN; }, page => page.document.add('input[type="password"]')]) {
    const page = fakePage(); page.setAfterScreenshot(() => edit(page));
    assert.equal((await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })).code, 'SCREEN_GUARD_REJECTED');
    assert.equal(countCaptures(page), 1);
  }
  await absent(directory);
});

test('existing directories and symlinks are never adopted; duplicate images are not overwritten', async t => {
  const { directory, screenshots } = await setup(t);
  await fs.mkdir(path.join(directory, 'screens'));
  assert.equal((await screenshots.capture('pilot-stream', fakePage(), { stage: 'pilot-stream' })).code, 'SCREEN_DIRECTORY_UNSAFE');
  assert.deepEqual(await fs.readdir(path.join(directory, 'screens')), []);
  const fresh = await setup(t);
  assert.equal((await fresh.screenshots.capture('pilot-stream', fakePage(), { stage: 'pilot-stream' })).status, 'captured');
  const again = fakePage();
  assert.equal((await fresh.screenshots.capture('pilot-stream', again, { stage: 'pilot-stream' })).code, 'SCREEN_ALREADY_CAPTURED');
  assert.equal(again.calls.length, 0);
  if (process.platform !== 'win32') {
    const outside = await setup(t); const link = path.join(outside.directory, 'linked-root');
    await fs.symlink(directory, link, 'dir');
    const linked = createMockScreenshots({ directory: link, mode: 'mock' }); linked.registerToken(TOKEN);
    assert.equal((await linked.capture('pilot-stream', fakePage(), { stage: 'pilot-stream' })).code, 'SCREEN_DIRECTORY_UNSAFE');
    const nested = createMockScreenshots({ directory: path.join(link, 'must-not-create'), mode: 'mock' }); nested.registerToken(TOKEN);
    assert.equal((await nested.capture('pilot-stream', fakePage(), { stage: 'pilot-stream' })).code, 'SCREEN_DIRECTORY_UNSAFE');
    assert.equal((await fs.readdir(directory)).includes('must-not-create'), false);
  }
});

test('a conflicting output filename is not overwritten and returns a fixed write skip', async t => {
  const { directory, screenshots } = await setup(t);
  assert.equal((await screenshots.capture('pilot-stream', fakePage(), { stage: 'pilot-stream' })).status, 'captured');
  const target = path.join(directory, 'screens', 'pilot-cancel.png');
  await fs.writeFile(target, 'existing owner data');
  assert.equal((await screenshots.capture('pilot-cancel', fakePage('pilot-cancel'), { stage: 'pilot-cancel' })).code, 'SCREEN_WRITE_FAILED');
  assert.equal(await fs.readFile(target, 'utf8'), 'existing owner data');
});

test('a stalled public read is bounded and does not take a screenshot', async t => {
  const { directory, screenshots } = await setup(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const page = fakePage(); let reads = 0;
  page.evaluate = () => { reads += 1; return new Promise(() => {}); };
  const result = screenshots.capture('failure-owner', page, { stage: 'pilot-stream' });
  await Promise.resolve();
  assert.equal(reads, 1);
  t.mock.timers.tick(5000);
  assert.deepEqual(await result, { status: 'skipped', code: 'SCREEN_GUARD_FAILED' });
  t.mock.timers.reset();
  assert.equal(countCaptures(page), 0); await absent(directory);
});
