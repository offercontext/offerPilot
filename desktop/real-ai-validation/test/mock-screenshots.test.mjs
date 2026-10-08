import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { createMockScreenshots, SCREEN_IDS, SKIP_CODES, GUARD_REASONS } from '../mock-screenshots.mjs';
import { safeScreenshotEvidence } from '../safe-evidence.mjs';

// Local fake-Page tests execute the actual renderer guard in a separate VM.
// They neither launch an EXE nor establish installed Windows UI evidence.
class Node {
  constructor({ text = '', tag = 'div', attributes = {}, visible = true, value = '' } = {}) {
    this.text = text; this.tagName = tag.toUpperCase(); this.attributes = attributes;
    this.visible = visible; this.value = value; this.labels = []; this.selectors = new Map();
    this.isConnected = true; this.parentElement = null; this.animations = [];
    this.style = { visibility: 'visible', display: 'block', opacity: '1', filter: 'none', overflowX: 'visible', overflowY: 'visible' };
    this.rect = { left: 50, top: 150, right: 550, bottom: 250, width: 500, height: 100 };
  }
  get textContent() { return this.text; }
  get innerText() { return this.text; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  getClientRects() { return this.visible ? [{}] : []; }
  getBoundingClientRect() { return { ...this.rect }; }
  getAnimations() { return this.animations; }
  add(selector, node = new Node()) {
    node.parentElement = this;
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
function fakePage(stage = 'pilot-stream', { haru = false, screenshotError = false, evaluateError = false, scrollError = false } = {}) {
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
    if (stage === 'interview-preparation') {
      surface.add(':scope > section > h3', new Node({ text: '准备方向' }));
      surface.add(':scope > section > article > p', new Node({ text: '结合岗位职责准备边界测试案例。' }));
      surface.add('[data-testid="interview-preparation-generate"]', new Node({ tag: 'button', text: '生成面试准备建议' }));
      surface.add(':scope > div > button', new Node({ tag: 'button', text: '关闭' }));
    }
    if (stage === 'resume-structure') { surface.add('section[aria-label="分类候选"]'); surface.add('textarea'); }
    if (stage === 'offer-negotiation') { surface.add('[aria-label="谈薪准备草稿"]'); surface.add('[data-testid="offer-negotiation-confirm"]'); }
  }
  if (stage === 'pilot-cancel') surface.add('[role="status"]', new Node({ text: '任务已停止。' }));
  const calls = [];
  let afterScreenshot, afterScroll, afterEvaluate, onFrame, frames = 0;
  const locate = (selector, roots = [document]) => {
    const aliases = {
      'section[aria-label="面试准备建议"] > section > article > p': ':scope > section > article > p',
      '[role="dialog"] textarea': 'textarea',
      '[data-testid="offer-negotiation-drawer"] [aria-label="谈薪准备草稿"]': '[aria-label="谈薪准备草稿"]',
    };
    const read = aliases[selector] ? () => surface.querySelectorAll(aliases[selector])
      : () => roots.flatMap(root => root.querySelectorAll(selector));
    const locator = nodes => ({
      locator: nested => locate(nested, nodes()),
      filter: ({ hasText }) => locator(() => nodes().filter(node => hasText.test(node.textContent || ''))),
      last: () => locator(() => nodes().slice(-1)),
      async scrollIntoViewIfNeeded(options) {
        calls.push({ method: 'scroll', selector, options, target: nodes()[0] });
        if (scrollError || nodes().length !== 1) throw new Error(`${TOKEN} private-scroll-details`);
        afterScroll?.(nodes()[0]);
      },
      async evaluate(fn, argument, options) {
        assert.deepEqual(options, { timeout: 3000 });
        assert.equal(argument, undefined);
        if (scrollError || nodes().length !== 1) throw new Error(`${TOKEN} private-footer-scroll-details`);
        // The only permitted renderer action in this fixture is native scroll;
        // CSS, animation and content mutation APIs are deliberately absent.
        const result = fn({ scrollIntoView(scrollOptions) {
          calls.push({ method: 'scroll', selector, options: scrollOptions, target: nodes()[0] });
          afterScroll?.(nodes()[0]);
        } });
        assert.equal(result, undefined);
      },
    });
    return locator(read);
  };
  return {
    document, bridge, state, surface, calls,
    setAfterScreenshot(fn) { afterScreenshot = fn; },
    setAfterScroll(fn) { afterScroll = fn; },
    setAfterEvaluate(fn) { afterEvaluate = fn; },
    setOnFrame(fn) { onFrame = fn; },
    locator: locate,
    async evaluate(fn, args) {
      calls.push({ method: 'evaluate', visual: args.visual });
      if (evaluateError) throw new Error(`${TOKEN} private-page-url`);
      const result = await vm.runInNewContext(`(${fn.toString()})(args)`, {
        args, document, window: { offerpilotDesktop: bridge, innerWidth: 1008, innerHeight: 689 },
        getComputedStyle: node => ({ ...node.style, display: node.visible ? node.style.display : 'none' }),
        requestAnimationFrame: callback => queueMicrotask(() => { onFrame?.(++frames); callback(frames * 16); }),
      });
      assert.ok(GUARD_REASONS.includes(result), 'only a fixed enum may escape the guard');
      afterEvaluate?.(args, result);
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
    assert.deepEqual(page.calls.filter(call => call.method === 'evaluate').map(call => call.visual),
      stage === 'interview-preparation' ? [false, false, 'stable', 'instant'] : [false, 'stable', 'instant']);
    assert.equal(page.calls.filter(call => call.method === 'scroll').length, stage === 'interview-preparation' ? 2 : 1);
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

test('fixed guard reason distinguishes known incomplete interview pages; credential rules remain absolute', async t => {
  const { directory, screenshots } = await setup(t);
  const unknown = fakePage('interview-preparation');
  unknown.document.selectors.delete('section[aria-label="面试准备建议"]');
  assert.equal((await screenshots.capture('failure-owner', unknown, { stage: 'interview-preparation' })).code, 'SCREEN_GUARD_REJECTED');
  assert.deepEqual(screenshots.snapshot().skipped, [{ id: 'failure-owner', code: 'SCREEN_GUARD_REJECTED', reason: 'BUSINESS_SURFACE' }]);
  await absent(directory);
  const list = fakePage('interview-preparation'); list.document.selectors.delete('section[aria-label="面试准备建议"]');
  list.document.add('[data-testid="interview-surface"]'); list.document.add('input[type="password"]');
  assert.equal((await screenshots.capture('failure-owner', list, { stage: 'interview-preparation' })).code, 'SCREEN_GUARD_REJECTED');
  assert.equal(screenshots.snapshot().skipped[0].reason, 'CREDENTIAL_SURFACE'); await absent(directory);
  list.document.selectors.delete('input[type="password"]');
  assert.equal((await screenshots.capture('failure-owner', list, { stage: 'interview-preparation' })).status, 'captured');
});
test('real interview proposal board and real HITL cancellation wording qualify without loosening credential guards', async t => {
  const { screenshots } = await setup(t);
  const interview = fakePage('interview-preparation');
  interview.document.selectors.delete('nav[aria-label="主导航"] [aria-current="page"][aria-label="面试"]');
  interview.document.add('nav[aria-label="主导航"] [aria-current="page"][aria-label="投递"]');
  assert.equal((await screenshots.capture('interview-preparation', interview, { stage: 'interview-preparation' })).status, 'captured');
  const haru = fakePage('pilot-hitl-reject', { haru: true });
  haru.surface.querySelector('article[data-role="assistant"] p').text = '已取消这次操作。你可以告诉我接下来想怎么做。';
  assert.equal((await screenshots.capture('haru-pilot-hitl-reject', haru, { stage: 'pilot-hitl-reject' })).status, 'captured');
});

async function advanceVisualDeadline(t, pending) {
  // Exercise the production bound without spending wall-clock seconds in each
  // negative fixture. No production timeout or readiness option is exposed.
  let settled = false;
  pending.finally(() => { settled = true; });
  for (let tick = 0; tick < 80 && !settled; tick += 1) {
    await new Promise(setImmediate);
    t.mock.timers.tick(50);
  }
  assert.equal(settled, true, 'the fixed visual deadline must terminate');
  return pending;
}

function boundOfferPage() {
  const page = fakePage('offer-negotiation');
  page.document.selectors.delete('nav[aria-label="主导航"] [aria-current="page"][aria-label="Offer"]');
  const nav = page.document.add('nav[aria-label="主导航"] [aria-current="page"][aria-label="投递"]');
  page.document.selectors.set('nav[aria-label="主导航"] [aria-current="page"]', [nav]);
  const owner = page.document.add('[data-core-task-owner]', new Node({ attributes: {
    'data-core-task-owner': 'application-offer-review', 'data-core-task-key': 'application.offer_review:applicationId=1',
  } }));
  owner.add('[data-testid="offer-negotiation-drawer"]', page.surface);
  return page;
}

test('bound Offer board captures only after the unchanged natural visibility and stability checks', async t => {
  const { screenshots } = await setup(t); const page = boundOfferPage();
  assert.equal((await screenshots.capture('offer-negotiation', page, { stage: 'offer-negotiation' })).status, 'captured');
  assert.deepEqual(page.calls.filter(call => call.method === 'evaluate').map(call => call.visual), [false, 'stable', 'instant']);
  assert.equal(page.calls.filter(call => call.method === 'scroll').length, 1);
  assert.deepEqual(page.calls.find(call => call.method === 'screenshot').options,
    { type: 'png', animations: 'allow', fullPage: false, timeout: 15000 });
});

test('bound Offer board still refuses clipped results and credentials appearing between stability frames', async t => {
  const { directory, screenshots } = await setup(t);
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  const clipped = boundOfferPage(); const draft = clipped.surface.querySelector('[aria-label="谈薪准备草稿"]');
  draft.rect = { ...draft.rect, top: 1000, bottom: 1100 };
  assert.equal((await advanceVisualDeadline(t, screenshots.capture('offer-negotiation', clipped, { stage: 'offer-negotiation' }))).code,
    'SCREEN_VISUAL_UNSETTLED');
  t.mock.timers.reset();
  assert.equal(countCaptures(clipped), 0);
  const credential = boundOfferPage();
  credential.setOnFrame(frame => { if (frame === 1) credential.document.add('input[type="password"]'); });
  assert.equal((await screenshots.capture('offer-negotiation', credential, { stage: 'offer-negotiation' })).code, 'SCREEN_GUARD_REJECTED');
  assert.equal(screenshots.snapshot().skipped[0].reason, 'CREDENTIAL_SURFACE');
  assert.equal(countCaptures(credential), 0);
  await absent(directory);
});

test('Pilot waits for its naturally finishing ancestor blur and opacity without changing animations', async t => {
  const { screenshots } = await setup(t);
  const page = fakePage(); const bubble = page.surface.querySelector('[class*="bubbleAssistant"]');
  const row = new Node(); row.parentElement = page.surface; bubble.parentElement = row;
  row.style.filter = 'blur(3px)'; row.style.opacity = '0.4';
  const animation = { playState: 'running', pending: false,
    finish() { assert.fail('must not finish animation'); }, cancel() { assert.fail('must not cancel animation'); } };
  row.animations = [animation];
  let waits = 0;
  page.setAfterEvaluate(({ visual }, reason) => {
    if (visual === 'stable' && reason === 'VISUAL_UNSETTLED' && ++waits === 2) {
      // Simulate the renderer reaching its own final frame.
      row.style.filter = 'none'; row.style.opacity = '1'; animation.playState = 'finished';
    }
  });
  page.setAfterScreenshot(() => { assert.equal(waits, 2); assert.equal(animation.playState, 'finished'); });
  assert.equal((await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })).status, 'captured');
  assert.equal(countCaptures(page), 1);
  assert.equal(page.calls.filter(call => call.method === 'scroll').length, 1);
});

test('permanent blur, opacity, or animation fail closed within the visual deadline and remain valid safe evidence', async t => {
  const { directory, screenshots } = await setup(t);
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  for (const edit of [
    node => { node.style.filter = 'blur(3px)'; },
    node => { node.style.opacity = '0.6'; },
    node => { node.animations = [{ playState: 'running', pending: false }]; },
    node => { node.animations = [{ playState: 'paused', pending: true }]; },
  ]) {
    const page = fakePage(); edit(page.surface);
    assert.deepEqual(await advanceVisualDeadline(t, screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })),
      { status: 'skipped', code: 'SCREEN_VISUAL_UNSETTLED' });
    assert.equal(countCaptures(page), 0);
  }
  t.mock.timers.reset();
  assert.deepEqual(safeScreenshotEvidence(screenshots.snapshot(), true), {
    captured: [], skipped: [{ id: 'pilot-stream', code: 'SCREEN_VISUAL_UNSETTLED' }],
  });
  await absent(directory);
});

test('credentials or tokens appearing during natural waiting stop immediately without capture or disk writes', async t => {
  const { directory, screenshots } = await setup(t);
  for (const edit of [
    page => page.document.add('input[type="password"]'),
    page => { page.document.body.text = TOKEN; },
    page => page.document.add('input', new Node({ attributes: { 'aria-label': 'API key' } })),
  ]) {
    const page = fakePage(); page.surface.style.filter = 'blur(3px)';
    page.setAfterEvaluate(({ visual }, reason) => {
      if (visual === 'stable' && reason === 'VISUAL_UNSETTLED') edit(page);
    });
    assert.equal((await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })).code, 'SCREEN_GUARD_REJECTED');
    assert.equal(countCaptures(page), 0);
  }
  assert.doesNotMatch(JSON.stringify(screenshots.snapshot()), /synthetic-case-token|API key/);
  await absent(directory);
});

test('credential, token and stage changes between stability frames are rechecked before capture', async t => {
  const { directory, screenshots } = await setup(t);
  for (const [edit, reason] of [
    [page => page.document.add('input[type="password"]'), 'CREDENTIAL_SURFACE'],
    [page => { page.document.body.text = TOKEN; }, 'TOKEN_VISIBLE'],
    [page => { page.document.selectors.delete('.op-app-main-pilot'); }, 'PILOT_SURFACE'],
    [page => { page.state.snapshot.loading = true; }, 'STATE_UNSETTLED'],
  ]) {
    const page = fakePage(); page.setOnFrame(frame => { if (frame === 1) edit(page); });
    assert.equal((await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })).code, 'SCREEN_GUARD_REJECTED');
    assert.equal(screenshots.snapshot().skipped[0].reason, reason);
    assert.equal(countCaptures(page), 0);
  }
  await absent(directory);
});

test('a changed stage after result scrolling is rejected without screenshot', async t => {
  const { directory, screenshots } = await setup(t); const page = fakePage('interview-preparation');
  page.setAfterScroll(() => { page.document.selectors.delete('section[aria-label="面试准备建议"]'); });
  assert.equal((await screenshots.capture('interview-preparation', page, { stage: 'interview-preparation' })).code, 'SCREEN_GUARD_REJECTED');
  assert.equal(screenshots.snapshot().skipped[0].reason, 'BUSINESS_SURFACE');
  assert.equal(countCaptures(page), 0); await absent(directory);
});

test('interview captures its last generated paragraph after real scrolling, not the inputs or heading', async t => {
  const { screenshots } = await setup(t); const page = fakePage('interview-preparation');
  const last = page.surface.add(':scope > section > article > p', new Node({ text: '需要确认的岗位信息。' }));
  last.rect = { ...last.rect, top: 1200, bottom: 1300 };
  const close = page.surface.querySelector(':scope > div > button');
  const generate = page.surface.querySelector('[data-testid="interview-preparation-generate"]');
  close.rect = { ...close.rect, top: 1400, bottom: 1440, height: 40 };
  generate.rect = { ...generate.rect, top: 1400, bottom: 1440, height: 40 };
  page.setAfterScroll(target => {
    if (target === last) last.rect = { ...last.rect, top: 300, bottom: 400 };
    else {
      assert.equal(target, close);
      close.rect = { ...close.rect, top: 450, bottom: 490 };
      generate.rect = { ...generate.rect, top: 450, bottom: 490 };
    }
  });
  assert.equal((await screenshots.capture('interview-preparation', page, { stage: 'interview-preparation' })).status, 'captured');
  const scroll = page.calls.find(call => call.method === 'scroll');
  assert.equal(scroll.selector, 'section[aria-label="面试准备建议"] > section > article > p');
  assert.deepEqual(scroll.options, { timeout: 3000 });
  assert.equal(page.calls.filter(call => call.method === 'scroll').at(-1).target, close);
  assert.deepEqual(page.calls.filter(call => call.method === 'scroll').at(-1).options,
    { block: 'end', inline: 'nearest', behavior: 'instant' });
  assert.ok(page.calls.findIndex(call => call.method === 'scroll') > page.calls.findIndex(call => call.method === 'evaluate'));
});

test('interview heading, empty articles, and unrelated readiness articles never prove generated content', async t => {
  const { directory, screenshots } = await setup(t);
  for (const text of [null, '', '   \n']) {
    const page = fakePage('interview-preparation');
    page.surface.selectors.delete(':scope > section > article > p');
    page.surface.add('article', new Node({ text: '复盘准备重点。' }));
    if (text !== null) page.surface.add(':scope > section > article > p', new Node({ text }));
    assert.equal((await screenshots.capture('interview-preparation', page, { stage: 'interview-preparation' })).code, 'SCREEN_GUARD_REJECTED');
    assert.equal(screenshots.snapshot().skipped[0].reason, 'RESULT_MISSING');
    assert.equal(page.calls.some(call => call.method === 'scroll'), false);
    assert.equal(countCaptures(page), 0);
  }
  await absent(directory);
});

test('offscreen or ancestor-clipped generated text cannot be accepted as readable evidence', async t => {
  const { directory, screenshots } = await setup(t);
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  for (const clip of [false, true]) {
    const page = fakePage('interview-preparation');
    const paragraph = page.surface.querySelector(':scope > section > article > p');
    if (clip) {
      page.surface.style.overflowY = 'auto';
      page.surface.rect = { ...page.surface.rect, top: 240, bottom: 600, height: 360 };
    } else paragraph.rect = { ...paragraph.rect, top: 1000, bottom: 1100 };
    assert.equal((await advanceVisualDeadline(t, screenshots.capture('interview-preparation', page, { stage: 'interview-preparation' }))).code, 'SCREEN_VISUAL_UNSETTLED');
    assert.equal(countCaptures(page), 0);
  }
  t.mock.timers.reset(); await absent(directory);
});

test('scroll exceptions are fixed codes and never disclose page errors or create artifacts', async t => {
  const { directory, screenshots } = await setup(t);
  const page = fakePage('interview-preparation', { scrollError: true });
  assert.deepEqual(await screenshots.capture('interview-preparation', page, { stage: 'interview-preparation' }),
    { status: 'skipped', code: 'SCREEN_SCROLL_FAILED' });
  assert.equal(countCaptures(page), 0);
  assert.deepEqual(safeScreenshotEvidence(screenshots.snapshot(), true), {
    captured: [], skipped: [{ id: 'interview-preparation', code: 'SCREEN_SCROLL_FAILED' }],
  });
  assert.doesNotMatch(JSON.stringify(screenshots.snapshot()), /synthetic-case-token|private/); await absent(directory);
});

test('visual instability starting during screenshot discards the in-memory image without waiting it clear', async t => {
  const { directory, screenshots } = await setup(t); const page = fakePage();
  page.setAfterScreenshot(() => { page.surface.style.filter = 'blur(3px)'; });
  assert.equal((await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })).code, 'SCREEN_GUARD_REJECTED');
  assert.equal(screenshots.snapshot().skipped[0].reason, 'VISUAL_UNSETTLED');
  assert.equal(countCaptures(page), 1); await absent(directory);
});

test('geometry and content must remain unchanged over the two natural frames', async t => {
  const { screenshots } = await setup(t); const page = fakePage();
  const bubble = page.surface.querySelector('[class*="bubbleAssistant"]');
  page.setOnFrame(frame => {
    if (frame === 1) {
      bubble.rect = { ...bubble.rect, top: 151, bottom: 251 };
      bubble.text = 'Naturally settled mock response';
    }
  });
  assert.equal((await screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })).status, 'captured');
  assert.equal(page.calls.filter(call => call.method === 'evaluate' && call.visual === 'stable').length, 2);
  assert.equal(countCaptures(page), 1);
});

test('a stalled visual read cannot extend the fixed natural-stability deadline', async t => {
  const { directory, screenshots } = await setup(t); const page = fakePage();
  const evaluate = page.evaluate;
  page.evaluate = (fn, args) => args.visual === 'stable' ? new Promise(() => {}) : evaluate(fn, args);
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  assert.deepEqual(await advanceVisualDeadline(t, screenshots.capture('pilot-stream', page, { stage: 'pilot-stream' })),
    { status: 'skipped', code: 'SCREEN_GUARD_FAILED' });
  t.mock.timers.reset(); assert.equal(countCaptures(page), 0); await absent(directory);
});

test('interview body alone cannot pass when either bottom action remains outside the viewport', async t => {
  const { directory, screenshots } = await setup(t);
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'] });
  for (const selector of ['[data-testid="interview-preparation-generate"]', ':scope > div > button']) {
    const page = fakePage('interview-preparation'); const action = page.surface.querySelector(selector);
    action.rect = { ...action.rect, top: 900, bottom: 940, height: 40 };
    assert.equal((await advanceVisualDeadline(t, screenshots.capture('interview-preparation', page, { stage: 'interview-preparation' }))).code, 'SCREEN_VISUAL_UNSETTLED');
    assert.equal(countCaptures(page), 0);
  }
  t.mock.timers.reset(); await absent(directory);
});
