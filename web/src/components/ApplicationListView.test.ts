import { describe, expect, it } from 'vitest';
import source from './ApplicationListView.tsx?raw';

describe('ApplicationListView source contract', () => {
  it('renders the basic pipeline list controls and columns', () => {
    expect(source).toContain('搜索公司、岗位、备注');
    expect(source).toContain('状态');
    expect(source).toContain('下一事件');
    expect(source).toContain('更新时间');
    expect(source).toContain('formatNextApplicationEvent');
  });
});
