import { describe, expect, it } from 'vitest';
import source from './ProactiveInbox.tsx?raw';
import { formatProactiveTimestamp } from './ProactiveInbox';

describe('ProactiveInbox', () => {
  it('interprets backend timestamps as UTC Unix seconds', () => {
    expect(formatProactiveTimestamp(0)).toContain('1970');
    expect(formatProactiveTimestamp(Number.NaN)).toBe('时间未知');
  });

  it('makes generated draft boundaries and cancellation visible', () => {
    expect(source).toContain('自动准备草稿 · 未发送');
    expect(source).toContain('不会自动执行业务操作');
    expect(source).toContain('取消任务');
    expect(source).toContain('cancelProactiveJob');
  });
});
