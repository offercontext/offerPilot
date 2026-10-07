// Browser-only regression fixture. It renders production components and theme;
// synthetic records never touch the user's backend or persistent application data.
import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider, Layout, Tabs, Modal } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import ApplicationListView from '../../src/components/ApplicationListView';
import PilotMascot, { type PilotMascotRuntime } from '../../src/features/pilotMascot/PilotMascot';
import { live2dPilotMascotRuntime } from '../../src/features/pilotMascot/live2dRuntime';
import Sidebar from '../../src/layout/Sidebar';
import TopBar from '../../src/layout/TopBar';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { darkTheme, lightTheme } from '../../src/theme/antdTheme';
import type { Application } from '../../src/types/application';
import '../../src/theme/tokens.css';

const params = new URLSearchParams(location.search);
const themeChoice = params.get('theme') === 'light' ? 'light' : 'dark';
localStorage.setItem('op-theme', themeChoice);
const globalHeader = params.get('surface') === 'header';
const count = Number(params.get('count') ?? 1);
const mode = params.get('mascot') ?? 'failure';
const brokenRuntime: PilotMascotRuntime = { mount: async () => { throw new Error('Simulated model failure'); } };
const observedRuntime: PilotMascotRuntime = {
  mount: async (...args) => {
    document.documentElement.dataset.live2dState = 'loading';
    try {
      const controller = await live2dPilotMascotRuntime.mount(...args);
      document.documentElement.dataset.live2dState = 'ready';
      return controller;
    } catch (error) {
      document.documentElement.dataset.live2dState = 'failed';
      throw error;
    }
  },
};
const records: Application[] = Array.from({ length: count }, (_, i) => ({
  id: i + 1,
  company_name: i % 2 ? 'InternationalCompanyWithAnExtremelyLongUnbrokenEnglishNameForLayoutRegression' : '桌面验收中文公司和特别长的公司名称以验证不会逐字折行',
  position_name: i % 2 ? 'SeniorPlatformEngineerWithAnUnbrokenTitleAndAdditionalResponsibilities' : '本地持久化测试岗位与特别长的岗位名称完整内容',
  status: 'pending', source: 'web', notes: `layout-row-${i + 1}`, job_url: '', applied_at: '',
  created_at: '2026-10-06T12:27:00', updated_at: '2026-10-06T12:27:00',
}));

function Fixture() {
  const [hidden, setHidden] = useState(mode === 'hidden');
  const [detail, setDetail] = useState<Application>();
  const [pilot, setPilot] = useState(false);
  const [checks, setChecks] = useState('pending');
  useEffect(() => {
    // Wait for the real table and fallback layout, not source-string matching.
    let frame = 0;
    const check = () => {
      const table = document.querySelector('.ant-table-content');
      const mascot = document.querySelector<HTMLElement>('aside[aria-label="Haru 助手"]');
      if ((!globalHeader && !table) || (mode === 'failure' && (mascot?.dataset.loadFailed !== 'true' || !mascot.parentElement?.hasAttribute(globalHeader ? 'data-pilot-mascot-global-dock' : 'data-pilot-mascot-fallback-dock')))) {
        frame = requestAnimationFrame(check); return;
      }
      const errors: string[] = [];
      const first = document.querySelector('.ant-table-tbody tr[data-row-key] td');
      const rows = [...document.querySelectorAll('.ant-table-tbody tr[data-row-key]')];
      if (first && first.getBoundingClientRect().width < 240) errors.push('primary column collapsed');
      if (rows.some(row => row.getBoundingClientRect().height > 100)) errors.push('text expanded row height');
      if (table && table.clientWidth < 982 && table.scrollWidth <= table.clientWidth) errors.push('horizontal scroll unavailable');
      if (document.documentElement.scrollWidth > window.innerWidth) errors.push('page overflow');
      if (mode === 'failure' && mascot) {
        const r = mascot.getBoundingClientRect();
        const safe = document.querySelector('[data-pilot-mascot-safe-area]')?.getBoundingClientRect();
        if (r.width !== 156 || r.height !== 48) errors.push('fallback retains invisible hit area');
        if (safe && r.left < safe.right && r.right > safe.left && r.top < safe.bottom && r.bottom > safe.top) errors.push('fallback overlaps table or pagination');
      }
      if (mode === 'hidden' && mascot) errors.push('hidden mascot remains interactive');
      setChecks(errors.length ? errors.join('; ') : 'passed');
    };
    frame = requestAnimationFrame(() => { frame = requestAnimationFrame(check); });
    return () => cancelAnimationFrame(frame);
  }, []);
  return <ConfigProvider locale={zhCN} theme={themeChoice === 'dark' ? darkTheme : lightTheme}><ThemeProvider>
    <Layout className="op-app-shell" style={{ minHeight: '100dvh', background: 'var(--op-layout-bg)' }} hasSider>
      <Sidebar view="applications-list" onChange={() => {}} reminderCount={1} />
      <Layout className="op-app-main" style={{ background: 'var(--op-layout-bg)', minWidth: 0, width: '100%' }}>
        <TopBar onSearch={() => {}} onOpenSettings={() => {}} primaryAction={{ label: '添加投递', onClick: () => {} }} />
        <Layout.Content className="op-app-content" style={{ padding: '0 24px 24px' }}>
          <Tabs activeKey="list" items={[{ key: 'board', label: '看板' }, { key: 'list', label: '列表' }]} />
          {globalHeader ? <div data-global-controls>{Array.from({ length: 10 }, (_, index) => <div key={index} style={{ display: 'flex', justifyContent: 'flex-end', margin: '18px 0' }}><button type="button" onClick={() => setPilot(true)}>全局操作 {index + 1}</button></div>)}</div> : <ApplicationListView applications={records} events={[]} onOpenDetail={setDetail} onAskPilot={() => setPilot(true)} />}
          <output data-layout-result={checks} aria-label="布局回归结果" style={{ fontSize: 11, color: 'var(--op-muted)' }}>{checks}</output>
        </Layout.Content>
      </Layout>
    </Layout>
    {!hidden && <PilotMascot activity={globalHeader ? "thinking" : "idle"} panelOpen={pilot} onHide={() => setHidden(true)} onTogglePilot={() => setPilot(!pilot)} runtime={mode === 'failure' ? brokenRuntime : observedRuntime} />}
    <Modal title="投递详情" open={Boolean(detail)} onCancel={() => setDetail(undefined)} footer={null}><p>{detail?.company_name}</p><p>{detail?.position_name}</p></Modal>
    <Modal title="Pilot" open={pilot} onCancel={() => setPilot(false)} footer={null}>Pilot 入口可用</Modal>
  </ThemeProvider></ConfigProvider>;
}
// Set theme before the first paint; this fixture has its own origin-only storage.
document.documentElement.dataset.theme = themeChoice;
createRoot(document.getElementById('root')!).render(<Fixture />);
