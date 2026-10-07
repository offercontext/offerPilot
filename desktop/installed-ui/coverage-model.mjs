import assert from 'node:assert/strict';

export const WIDTHS = Object.freeze([900, 1008, 1280, 1440]);
export const ROOTS = Object.freeze([
  ['R01', 'dashboard', '今日', '概览'], ['R02', 'reminders', '今日', '提醒'],
  ['R03', 'calendar', '今日', '日历'], ['R04', 'board', '投递', '看板'],
  ['R05', 'applications-list', '投递', '列表'], ['R06', 'interview', '面试', '面试'],
  ['R07', 'questions', '面试', '刷题'], ['R08', 'offers', 'Offer', null],
  ['R09', 'resumes', '素材库', '简历'], ['R10', 'reviews', '素材库', '经历素材'],
  ['R11', 'knowledge', '素材库', '参考资料'], ['R12', 'pilot', null, null],
  ['R13', 'settings', '设置', null],
].map(([id, view, module, tab]) => Object.freeze({ id, view, module, tab })));
export const ROOT_STATES = Object.freeze(['empty-before-fixtures', 'populated-to-supported-extent']);
export const ROOT_CASES = Object.freeze(ROOT_STATES.flatMap((state) => ['dark', 'light'].flatMap((theme) =>
  (theme === 'dark' ? WIDTHS : [1280]).flatMap((width) => ROOTS.map((item) => ({
    surfaceId: item.id, caseId: `${state}-${theme}-${width}-${item.view}`, state, theme, width, view: item.view,
  }))))));
export const SUBVIEWS = Object.freeze([
  'command-palette', 'application-form', 'application-detail', 'application-menu',
  'jd-editor-history', 'schedule-form', 'application-materials', 'interview-readiness',
  'practice-readiness', 'interview-studio', 'interview-review', 'voice-growth',
  'question-editor', 'resume-upload', 'resume-editor', 'resume-comparison',
  'story-editor', 'knowledge-inputs', 'knowledge-detail', 'offer-form',
  'offer-comparison', 'offer-dimensions', 'offer-negotiation', 'pilot-surfaces',
  'haru-runtime', 'settings-ai', 'settings-context-memory-proactive', 'settings-data',
  'settings-appearance', 'settings-voice', 'settings-diagnostics',
].map((view, index) => Object.freeze({ id: `S${String(index + 1).padStart(2, '0')}`, view })));
export const OUTCOMES = Object.freeze(['PASS', 'FAIL', 'BLOCKED', 'NOT RUN', 'N/A']);
export function safeShotName(value) {
  assert.match(value, /^[A-Za-z0-9][A-Za-z0-9_-]{0,150}$/);
  return `${value}.png`;
}
export function summarizeCoverage(cases) {
  const counts = Object.fromEntries(OUTCOMES.map((name) => [name, 0]));
  for (const item of cases) {
    assert.ok(OUTCOMES.includes(item.outcome));
    if (item.outcome === 'PASS') {
      assert.ok(item.assertions?.length, 'PASS requires actual assertions');
      assert.ok(item.screenshots?.length, 'PASS requires captured screen evidence');
    }
    counts[item.outcome]++;
  }
  return { counts, status: counts.FAIL ? 'failed' : counts.BLOCKED || counts['NOT RUN'] ? 'incomplete' : 'passed',
    functionalPasses: cases.filter((item) => item.outcome === 'PASS' && item.kind === 'interaction').length,
    visualPasses: cases.filter((item) => item.outcome === 'PASS' && item.kind === 'visual').length,
    humanVisualReview: 'required-not-automated',
    screenshotAloneProvesFunction: false };
}
// Only fixed classifications leave the process. Never serialize console text, URLs, stacks or messages.
export function classifyRuntimeMessage(text, source = 'console') {
  const value = String(text);
  if (/content security policy|unsafe-eval|evalerror|refused to evaluate|refused to execute/i.test(value)) return 'csp-runtime-block';
  if (/live2d|cubism|haru|pixi/i.test(value)) return 'haru-runtime-error';
  if (/webgl|gpu|context lost/i.test(value)) return 'graphics-runtime-error';
  return source === 'pageerror' ? 'unexpected-page-error' : 'unclassified-console-error';
}
export function publicRequestFailure(url, origin, status, method = 'GET') {
  let parsed;
  try { parsed = new URL(url); } catch { return null; }
  if (parsed.origin !== origin) return null;
  const pathname = parsed.pathname;
  const category = pathname.startsWith('/api/') ? 'own-api' : pathname.startsWith('/assets/') ? 'own-asset' : 'own-resource';
  // Expected read-only resource probes can return 404; record but do not equate them to a crash.
  const expectedAbsent = method === 'GET' && /^\/api\/applications\/\d+\/material-kit$/.test(pathname) && status === 404;
  return { category, status: Number.isInteger(status) ? status : null, expectedAbsent };
}
export function checkGeometry(measured, width) {
  assert.equal(measured.width, width, 'native content viewport differs from requested width');
  assert.ok(measured.documentWidth <= measured.width + 1, 'app-wide horizontal overflow');
  assert.ok(measured.height >= 600, 'native content height too small');
}
