// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest';
import { setLocale } from '@/i18n';
import { displayMailWarning } from './jobMailModel';
afterEach(() => { setLocale('zh-CN'); });
it('localizes only the two authored backend warnings and preserves unknown original content', () => {
  const warnings = ['本次仅写入事件，不改变投递阶段，不发送邮件。', '开始时间变化后，旧提醒将清除；请按新安排另设提醒。'];
  setLocale('en-US');
  expect(warnings.map(displayMailWarning)).toEqual([
    'This only writes the event. It does not change the application stage or send email.',
    'Changing the start time clears the old reminder. Set a new reminder for the updated schedule.',
  ]);
  const unknown = '用户原文：本次仅写入事件，不改变投递阶段，不发送邮件。';
  expect(displayMailWarning(unknown)).toBe(unknown);
  setLocale('zh-CN');
  expect(warnings.map(displayMailWarning)).toEqual(warnings);
});
