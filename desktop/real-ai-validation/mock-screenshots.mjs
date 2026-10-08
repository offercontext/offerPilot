// Optional evidence for the isolated, secret-free MOCK runner only. Importing
// this module does nothing; live validation must never construct or call it.
import fs from 'node:fs/promises';
import path from 'node:path';

export const SCREEN_IDS = Object.freeze([
  'pilot-stream', 'pilot-cancel', 'pilot-hitl-reject',
  'haru-pilot-stream', 'haru-pilot-cancel', 'haru-pilot-hitl-reject',
  'interview-preparation', 'resume-structure', 'offer-negotiation',
  'failure-owner', 'failure-haru',
]);
export const SKIP_CODES = Object.freeze([
  'SCREEN_ID_INVALID', 'SCREEN_STAGE_INVALID', 'SCREEN_PAGE_INVALID',
  'SCREEN_TOKEN_INVALID', 'SCREEN_GUARD_REJECTED', 'SCREEN_GUARD_FAILED',
  'SCREEN_CAPTURE_FAILED', 'SCREEN_DIRECTORY_UNSAFE', 'SCREEN_WRITE_FAILED',
  'SCREEN_ALREADY_CAPTURED',
]);
const STAGES = new Set(SCREEN_IDS.filter(id => !id.startsWith('haru-') && !id.startsWith('failure-')));
const fail = code => { throw Object.assign(new Error(code), { code }); };

// Runs in the renderer, returning ONLY a boolean. Never return DOM, text,
// values, URLs, bridge snapshots or tokens, including on the failure path.
async function safeScreen({ screenId, stage, tokens }) {
  try {
    const visible = node => Boolean(node && node.getClientRects().length &&
      getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none');
    const all = (selector, root = document) => [...root.querySelectorAll(selector)];
    const shown = (selector, root = document) => all(selector, root).filter(visible);
    const one = (selector, root = document) => shown(selector, root).length === 1;
    const hasText = (selector, pattern, root = document) => shown(selector, root)
      .some(node => pattern.test(node.textContent || ''));
    if (!document.body || document.visibilityState !== 'visible') return false;
    // Reject the entire credential surface even if controls are hidden/empty.
    if (document.querySelector('section[aria-label="AI 设置"], [data-testid="ai-provider-list"], input[type="password"], input[autocomplete="current-password"], input[autocomplete="new-password"], input[autocomplete="one-time-code"], [data-credential-form]')) return false;
    if (document.querySelector('nav[aria-label="主导航"] [aria-current="page"][aria-label="设置"]')) return false;
    // Unknown embedded or shadow content cannot be inspected by this guard.
    if (document.querySelector('iframe, frame, object, embed') || all('*').some(node => node.shadowRoot)) return false;
    const credential = /api[\s_-]*(?:key|token)|secret|credential|password|密钥|凭据|口令|密码/i;
    const controls = all('input, textarea, select, [role="textbox"], [role="combobox"], [contenteditable], form, [role="form"]');
    for (const node of controls) {
      const labels = [...(node.labels || [])].map(label => label.textContent || '');
      const labelledBy = (node.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map(id => document.getElementById(id)?.textContent || '');
      const description = ['id', 'name', 'type', 'autocomplete', 'aria-label', 'placeholder', 'title']
        .map(name => node.getAttribute(name) || '').concat(labels, labelledBy,
          node.tagName === 'FORM' || node.getAttribute('role') === 'form' ? [node.textContent || ''] : []).join(' ');
      if (credential.test(description)) return false;
    }
    const texts = [document.body.textContent || '', document.body.innerText || '',
      ...controls.filter(visible).flatMap(node => [typeof node.value === 'string' ? node.value : '',
        node.getAttribute('placeholder') || '', node.getAttribute('title') || '', node.getAttribute('aria-label') || ''])];
    if (texts.some(text => tokens.some(token => text.includes(token)) ||
      /\b(?:sk-[A-Za-z0-9_-]{10,}|Bearer\s+[A-Za-z0-9_.-]{10,})\b/.test(text))) return false;

    const bridge = window.offerpilotDesktop;
    const haru = screenId.startsWith('haru-') || screenId === 'failure-haru';
    if (bridge?.role !== (haru ? 'haru' : 'owner')) return false;
    const failure = screenId.startsWith('failure-');
    let root;
    if (haru) {
      if (!one('main[aria-label="Haru 桌面小窗"]') || !one('section[aria-label="Haru 对话"]')) return false;
      root = shown('section[aria-label="Haru 对话"]')[0];
    } else if (stage.startsWith('pilot-')) {
      // The fullscreen Pilot intentionally has no sidebar navigation.
      if (!one('.op-app-main-pilot') || !one('.op-pilot-page-host [data-onboarding-target="pilot"]')) return false;
      root = shown('.op-pilot-page-host [data-onboarding-target="pilot"]')[0];
      if (!one('textarea[placeholder="问问领航员，或输入 / 唤起能力"]', root)) return false;
    } else {
      const surfaces = {
        'interview-preparation': ['面试', 'section[aria-label="面试准备建议"]'],
        'resume-structure': ['素材库', '[role="dialog"]'],
        'offer-negotiation': ['Offer', '[data-testid="offer-negotiation-drawer"]'],
      };
      const [nav, selector] = surfaces[stage] || [];
      if (!nav || !one('nav[aria-label="主导航"]') ||
        !one(`nav[aria-label="主导航"] [aria-current="page"][aria-label="${nav}"]`) || !one(selector)) return false;
      root = shown(selector)[0];
      if (stage === 'resume-structure' && !/AI 简历分类与核对/.test(root.textContent || '')) return false;
    }
    // A controlled failure may show an incomplete result, but must still be on
    // that exact known surface. Settings/connection never qualify as a stage.
    if (failure) return true;
    if (stage === 'interview-preparation') return hasText('h3', /^准备方向$/, root) && shown('article', root).length > 0;
    if (stage === 'resume-structure') return one('section[aria-label="分类候选"]', root) && shown('textarea', root).length > 0;
    if (stage === 'offer-negotiation') return one('[aria-label="谈薪准备草稿"]', root) && one('[data-testid="offer-negotiation-confirm"]', root);
    const state = await bridge.getState(); // Existing public, read-only API.
    const snapshot = state?.snapshot;
    if (state?.connected !== true || snapshot?.taskState !== 'idle' || snapshot.loading !== false ||
      snapshot.hasPending !== false || snapshot.error || snapshot.canStop !== false) return false;
    if (stage === 'pilot-cancel') return Boolean(snapshot.stopMessage) && hasText('[role="status"]', /停止/, root);
    const messages = haru ? 'article[data-role="assistant"] p' : '[class*="bubbleAssistant"], [data-operation-id]';
    if (stage === 'pilot-hitl-reject') return hasText(messages, /拒绝|未执行/, root);
    return hasText(messages, /\S/, root);
  } catch { return false; }
}

export function createMockScreenshots({ directory, mode } = {}) {
  if (mode !== 'mock') fail('SCREEN_MODE_INVALID');
  if (typeof directory !== 'string' || !directory || !path.isAbsolute(directory) ||
    path.resolve(directory) === path.parse(directory).root || directory.includes('\0')) fail('SCREEN_DIRECTORY_UNSAFE');
  const root = path.resolve(directory);
  const screens = path.join(root, 'screens');
  const tokens = new Set();
  const captured = new Set();
  const skipped = new Map();
  let tokenInvalid = false, ownedDirectory, writing = Promise.resolve();
  const skip = (id, code) => {
    if (SCREEN_IDS.includes(id) && !captured.has(id)) skipped.set(id, code);
    return { status: 'skipped', code };
  };
  async function safeDirectory() {
    // Do not follow links/junctions, adopt somebody else's screens directory,
    // or overwrite an existing image. The output root belongs to this run.
    for (let current = root; ; current = path.dirname(current)) {
      try {
        const stat = await fs.lstat(current);
        if (!stat.isDirectory() || stat.isSymbolicLink()) fail('SCREEN_DIRECTORY_UNSAFE');
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (current === path.dirname(current)) break;
    }
    await fs.mkdir(root, { recursive: true, mode: 0o700 });
    const owner = await fs.lstat(root);
    if (!owner.isDirectory() || owner.isSymbolicLink() ||
      (typeof process.getuid === 'function' && owner.uid !== process.getuid())) fail('SCREEN_DIRECTORY_UNSAFE');
    if (!ownedDirectory) {
      await fs.mkdir(screens, { mode: 0o700 });
      ownedDirectory = await fs.lstat(screens);
    }
    const current = await fs.lstat(screens);
    if (!current.isDirectory() || current.isSymbolicLink() || current.dev !== ownedDirectory.dev || current.ino !== ownedDirectory.ino) fail('SCREEN_DIRECTORY_UNSAFE');
  }
  async function captureOne(screenId, page, options) {
    if (!SCREEN_IDS.includes(screenId)) return skip(screenId, 'SCREEN_ID_INVALID');
    const stage = options?.stage;
    if (!STAGES.has(stage) || (!screenId.startsWith('failure-') && screenId.replace(/^haru-/, '') !== stage)) return skip(screenId, 'SCREEN_STAGE_INVALID');
    if (!page || typeof page.evaluate !== 'function' || typeof page.screenshot !== 'function') return skip(screenId, 'SCREEN_PAGE_INVALID');
    if (tokenInvalid || tokens.size === 0) return skip(screenId, 'SCREEN_TOKEN_INVALID');
    if (captured.has(screenId)) return skip(screenId, 'SCREEN_ALREADY_CAPTURED');
    const clean = async () => {
      if (tokenInvalid || tokens.size === 0) return false;
      let timer;
      try {
        return (await Promise.race([
          page.evaluate(safeScreen, { screenId, stage, tokens: [...tokens] }),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('SCREEN_GUARD_FAILED')), 5000); }),
        ])) === true;
      } finally { clearTimeout(timer); }
    };
    try { if (!await clean()) return skip(screenId, 'SCREEN_GUARD_REJECTED'); }
    catch { return skip(screenId, 'SCREEN_GUARD_FAILED'); }
    let bytes;
    try {
      // Keep natural rendering/animations. No masking, DOM removal, injected
      // styles, animation finishing/cancelling or screenshot-to-disk shortcut.
      bytes = await page.screenshot({ type: 'png', animations: 'allow', fullPage: false, timeout: 15000 });
      if (!Buffer.isBuffer(bytes) || bytes.length === 0) return skip(screenId, 'SCREEN_CAPTURE_FAILED');
    } catch { return skip(screenId, 'SCREEN_CAPTURE_FAILED'); }
    // Drop the in-memory image if navigation, credentials or a broker token
    // appeared while capture was in progress. Nothing has been persisted yet.
    try { if (!await clean()) return skip(screenId, 'SCREEN_GUARD_REJECTED'); }
    catch { return skip(screenId, 'SCREEN_GUARD_FAILED'); }
    try { await safeDirectory(); } catch { return skip(screenId, 'SCREEN_DIRECTORY_UNSAFE'); }
    try { await fs.writeFile(path.join(screens, `${screenId}.png`), bytes, { flag: 'wx', mode: 0o600 }); }
    catch { return skip(screenId, 'SCREEN_WRITE_FAILED'); }
    captured.add(screenId); skipped.delete(screenId);
    return { status: 'captured', code: 'SCREEN_CAPTURED' };
  }
  return Object.freeze({
    registerToken(token) {
      if (typeof token !== 'string' || token.length === 0 || token.length > 8192 || (!tokens.has(token) && tokens.size >= 64)) {
        tokenInvalid = true; return false;
      }
      tokens.add(token); return true;
    },
    capture(screenId, page, options) {
      const pending = writing.then(() => captureOne(screenId, page, options));
      writing = pending.catch(() => undefined);
      return pending;
    },
    snapshot() {
      return { captured: SCREEN_IDS.filter(id => captured.has(id)),
        skipped: SCREEN_IDS.filter(id => skipped.has(id)).map(id => ({ id, code: skipped.get(id) })) };
    },
  });
}
