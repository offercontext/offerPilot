import { describe, expect, it } from 'vitest';
import source from './ProactiveSettings.tsx?raw';

describe('ProactiveSettings', () => {
  it('keeps the two proactive modes independently controllable', () => {
    expect(source).toContain('启用普通提醒');
    expect(source).toContain('启用面试准备草稿');
    expect(source).toContain('可能消耗额度');
    expect(source).toContain('不代表你已发送');
  });

  it('exposes explicit scope, quiet hours, timezone, and daily limits', () => {
    expect(source).toContain('proactive-application-scope');
    expect(source).toContain('proactive-timezone');
    expect(source).toContain('安静时段开始');
    expect(source).toContain('普通提醒每日上限');
    expect(source).toContain('准备草稿每日上限');
    expect(source).toContain('移除投递后，相关未完成任务会停止');
  });

  it('shows local service requirements and a one-click shutdown', () => {
    expect(source).toContain('需要本地服务保持运行');
    expect(source).toContain('一键关闭全部');
    expect(source).toContain('保存主动任务设置');
    expect(source).toContain('updateProactiveSettings');
  });
});
