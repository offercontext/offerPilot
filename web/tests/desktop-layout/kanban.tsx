// Production board and shell with synthetic records only. No backend is needed.
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider, Layout, Modal, Tabs } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { DndContext, PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import KanbanBoard from '../../src/components/KanbanBoard';
import Sidebar from '../../src/layout/Sidebar';
import TopBar from '../../src/layout/TopBar';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { darkTheme, lightTheme } from '../../src/theme/antdTheme';
import type { Application } from '../../src/types/application';
import '../../src/theme/tokens.css';

const params = new URLSearchParams(location.search);
const theme = params.get('theme') === 'light' ? 'light' : 'dark';
const records: Application[] = Array.from({ length: Number(params.get('count') ?? 12) }, (_, index) => ({
  id: index + 1,
  company_name: index === 1 ? 'InternationalCompanyWithAnExtremelyLongUnbrokenEnglishNameForLayoutRegression'
    : index === 2 ? '桌面验收中文公司和特别长的公司名称以验证卡片内容始终保持可读'
      : `QA-20261007-37738260699-分页公司-${String(12 - index).padStart(2, '0')}`,
  position_name: index === 1 ? 'SeniorPlatformEngineerWithAnUnbrokenTitleAndAdditionalResponsibilities'
    : index === 2 ? '高级基础架构工程师与跨区域协作团队复杂平台可靠性负责人' : `合成岗位 ${12 - index}`,
  status: 'pending', source: 'web', job_url: '',
  notes: index === 1 ? 'SyntheticNotesWithoutWhitespaceForCardLayoutRegression'.repeat(3)
    : 'QA-20261007-37738260699 仅供真实布局验收的合成记录，不会提交给服务端。',
  applied_at: '2026-10-08T00:00:00Z', created_at: '2026-10-08T00:00:00Z',
  updated_at: `2026-10-08T00:${String(12 - index).padStart(2, '0')}:00Z`,
}));
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
localStorage.setItem('op-theme', theme);
document.documentElement.dataset.theme = theme;

function Fixture() {
  const [detail, setDetail] = useState<Application>();
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));
  return <ConfigProvider locale={zhCN} theme={theme === 'dark' ? darkTheme : lightTheme}><ThemeProvider>
    <QueryClientProvider client={client}><DndContext sensors={sensors}>
      <Layout className="op-app-shell" style={{ minHeight: '100dvh', background: 'var(--op-layout-bg)' }} hasSider>
        <Sidebar view="board" onChange={() => {}} reminderCount={2} />
        <Layout className="op-app-main" style={{ background: 'var(--op-layout-bg)', minWidth: 0, width: '100%' }}>
          <TopBar onSearch={() => {}} onOpenSettings={() => {}} primaryAction={{ label: '添加投递', onClick: () => {} }} />
          <Layout.Content className="op-app-content" style={{ padding: '0 24px 24px' }}>
            <Tabs activeKey="board" items={[{ key: 'board', label: '看板' }, { key: 'list', label: '列表' }]} />
            <div data-kanban-fixture><KanbanBoard applications={records} onOpenDetail={setDetail} /></div>
          </Layout.Content>
        </Layout>
      </Layout>
      <Modal title="投递详情" open={Boolean(detail)} onCancel={() => setDetail(undefined)} footer={null}>
        <p>{detail?.company_name}</p><p>{detail?.position_name}</p><p>{detail?.notes}</p>
      </Modal>
    </DndContext></QueryClientProvider>
  </ThemeProvider></ConfigProvider>;
}

createRoot(document.getElementById('root')!).render(<Fixture />);
