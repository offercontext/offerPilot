import assert from 'node:assert/strict';

const reports = new WeakMap();
export const UI_STEPS = Object.freeze(['navigation', 'palette-open', 'palette-query', 'palette-select',
  'button-action', 'button-assertion', 'dialog-dismiss', 'form-fill', 'form-validation',
  'ui-submit', 'response-wait', 'response-validate', 'readback', 'selection-open',
  'selection-search', 'selection-confirm', 'viewport-set', 'screenshot-capture',
  'geometry-check', 'runtime-check', 'recovery-reload', 'startup-reload', 'root-landmark', 'companion-readback', 'companion-context', 'drawer-close-hit', 'offer-local-scroll', 'offer-local-scroll-verify', 'offer-second-preflight', 'companion-visual', 'offer-return-detail', 'offer-reopen-comparison', 'settings-export-profile', 'settings-export-click', 'settings-export-native-terminal', 'settings-export-file-verify', 'clipboard-instrumentation', 'clipboard-0-cancel', 'clipboard-1-allow', 'clipboard-2-cancel', 'installed-ort-owner', 'installed-ort-document-csp', 'installed-ort-initialize']);
export const UI_CONTROLS = Object.freeze(['unspecified', 'quick-open', 'button-pattern',
  '今日复习', '题库', '复盘重点练习', '快速模拟', '展开到 Pilot 工作区', '发送',
  'application', 'application-jd', 'schedule', 'question', 'resume', 'knowledge', 'story', 'offer', 'pilot', 'settings-export', 'offline-ort',
  '取消', 'Cancel', '关闭', 'Close', '确定', 'OK', '创建', '保存', '上传', '加入',
  '返回上一层', '退出沉浸模式，返回原页面', '切换明暗模式', '核对并检查重复', '确认保存',
  '问 Pilot', '保存岗位资料', '查看历史', '调整时间', '下一个月', '上一个月', '今天', '选择面试并开始复盘', '保存复盘',
  '手动添加', '编辑题目', 'AI 生成题目', '上传现有简历', '和 Haru 创建初稿', '高级 JSON',
  '求职意向', '基本信息', '教育经历', '工作经历', '项目经历', '技能', '其他',
  '编辑', '继续编辑', '复制', '对比版本', '关闭版本对比', '开始面试练习', '表达成长', '返回面试',
  '上传 Markdown / Text', '上传图文资料', '粘贴正文', '开始导入', '编辑标题', '永久删除该资料',
  '新建故事', '手动编写并保存', '确认手动保存故事版本', '查看版本', '关闭历史',
  '录入第一份 Offer', '录入另一份 Offer', '返回 Offer 中心', '调整对比项', '恢复全部明细', '准备谈薪',
  '上下文面板', '打开 Pilot tab', '重置 Haru 位置', '配置 AI', '返回设置', '添加偏好', '导出备份', '导出完整数据']);
export function bindUiSteps(page, reporter) { reports.set(page, reporter); }
export function validUiStep(step, control = 'unspecified') {
  assert.ok(UI_STEPS.includes(step), 'unapproved diagnostic step');
  assert.ok(UI_CONTROLS.includes(control), 'unapproved diagnostic control');
  return { step, control };
}
export function markUiStep(page, step, control = 'unspecified') {
  const value = validUiStep(step, control);
  reports.get(page)?.(value);
}
const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
export function visibleButtonPattern(label) {
  assert.equal(typeof label, 'string');
  // Ant Design intentionally renders two-Han-character buttons as "取 消".
  // Match only that known typography change, not arbitrary prefixes/suffixes.
  const text = /^[\u3400-\u9fff]{2}$/u.test(label)
    ? [...label].map(escape).join('\\s*')
    : label.trim().split(/\s+/u).map(escape).join('\\s+');
  return new RegExp(`^\\s*${text}\\s*$`, 'u');
}
function tracked(locator, page, control) {
  return new Proxy(locator, { get(target, key) {
    const value = Reflect.get(target, key, target);
    if (typeof value !== 'function') return value;
    if (['click', 'press', 'focus'].includes(key)) return (...args) => {
      markUiStep(page, 'button-action', control); return value.apply(target, args);
    };
    if (['waitFor', 'isDisabled', 'isVisible', 'count', 'getAttribute'].includes(key)) return (...args) => {
      markUiStep(page, 'button-assertion', control); return value.apply(target, args);
    };
    return value.bind(target);
  } });
}
export function button(scope, label) {
  const page = typeof scope.page === 'function' ? scope.page() : scope;
  const control = typeof label === 'string' && UI_CONTROLS.includes(label) ? label : 'button-pattern';
  // Visible button text excludes the icon's aria-label; explicit aria-label still
  // identifies icon-only controls. Union deduplicates the SAME node, never chooses first.
  const role = scope.getByRole('button');
  const text = role.filter({ hasText: typeof label === 'string' ? visibleButtonPattern(label) : label });
  const named = scope.getByRole('button', { name: label, exact: typeof label === 'string' });
  return tracked(text.or(named).filter({ visible: true }), page, control);
}
export function quickOpenButton(page) {
  const locator = page.locator('header.op-topbar .op-topbar-actions').getByRole('button')
    .filter({ hasText: /^\s*快速打开\s+(?:Ctrl\s*K|⌘\s*K)\s*$/u });
  return tracked(locator, page, 'quick-open');
}
export function safeUiFailure(error) {
  const message = String(error?.message || '');
  if (error?.code === 'UI_VISUAL_FAILURE') return 'visual-assertion-failed';
  if (/strict mode violation/i.test(message)) return 'selector-ambiguous';
  if (/intercepts pointer events|outside of the viewport|not visible|not stable/i.test(message)) return 'control-not-actionable';
  if (error?.name === 'TimeoutError') return 'ui-wait-timeout';
  if (error?.name === 'AssertionError') return 'assertion-failed';
  return 'ui-action-error';
}
