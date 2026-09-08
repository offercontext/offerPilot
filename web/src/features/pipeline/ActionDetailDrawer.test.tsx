import { App as AntApp } from 'antd';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import ActionDetailDrawer from './ActionDetailDrawer';

describe('ActionDetailDrawer presentation', () => {
  it('renders a stable detail surface and keeps actions user-triggered', () => {
    const onRunAction = vi.fn();
    const markup = renderToStaticMarkup(
      <AntApp>
        <ActionDetailDrawer
          open
          insight={{
            id: 'follow-up',
            title: '跟进面试结果',
            reason: '距离上次沟通已过去三天',
            evidence: ['最近联系：2026-08-09'],
            priority: 'p1',
            primaryAction: { label: '查看投递' },
          } as never}
          onClose={vi.fn()}
          onRunAction={onRunAction}
        />
      </AntApp>,
    );

    expect(markup).toContain('data-testid="action-detail-surface"');
    expect(onRunAction).not.toHaveBeenCalled();
  });
});
