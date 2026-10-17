import { useEffect, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigProvider, Layout, Row, Col } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ResumeLibraryView from '../../src/components/ResumeLibraryView';
import type { Resume } from '../../src/types/resume';
import zhCN from 'antd/locale/zh_CN';
import OfferCard from '../../src/components/OfferCard';
import OfferNegotiationDrawer from '../../src/components/OfferNegotiationDrawer';
import { CoreTaskSurfaceHost } from '../../src/features/coreTaskSurface/CoreTaskSurfaceHost';
import { createCoreTaskSurfaceController } from '../../src/features/coreTaskSurface/controller';
import { AssistantSurfaceProvider, useAssistantSurface, usePilotConversationController } from '../../src/features/assistantSurface/AssistantSurfaceProvider';
import HaruChatWindow from '../../src/features/assistantSurface/HaruChatWindow';
import InterviewReadinessCenter from '../../src/features/interviewReadiness/InterviewReadinessCenter';
import Sidebar from '../../src/layout/Sidebar';
import TopBar from '../../src/layout/TopBar';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { darkTheme, lightTheme } from '../../src/theme/antdTheme';
import type { Offer } from '../../src/types/offer';
import '../../src/theme/tokens.css';

const params = new URLSearchParams(location.search);
const theme = params.get('theme') === 'light' ? 'light' : 'dark';
const surface = params.get('surface') ?? 'offer';
const label = params.get('language') === 'en'
  ? 'InternationalResearchAndDevelopmentCompanyWithoutSpaces · Senior Software Engineer, Distributed Infrastructure and Reliability'
  : '中文超长公司名称研发创新技术研究中心与跨区域协作团队 · 跨端基础架构与复杂系统可靠性高级软件工程师';
localStorage.setItem('op-theme', theme);
document.documentElement.dataset.theme = theme;
const offer: Offer = {
  id: 7, application_id: 42, company_name: label, position_name: 'Senior Engineer · 高级研发工程师', status: 'pending',
  base_monthly: 28000, months_per_year: 12, signing_bonus: 0, equity: '', perks: '', deadline: '',
  notes: '', assessment: '', total_cash: 336000, created_at: '2026-10-07T00:00:00Z', updated_at: '2026-10-07T00:00:00Z',
};
const resumeClient = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
const resumeBase: Resume = {
  id: 11, name: '', title: '中文超长基础简历名称研发创新与跨区域协作团队'.repeat(3),
  file_path: '', parsed_data: '', parse_status: 'done', is_master: true, parent_resume_id: null,
  source: 'manual', source_file_path: '', content_json: { career_intent: { target_roles: ['SoftwareInfrastructureEngineerWithoutSpaces'.repeat(3)] } },
  deleted_at: null, created_at: '2026-10-07T12:00:00Z', completion_percent: 50,
  missing_sections: ['education', 'experience', 'projects'], is_complete: false,
};
resumeClient.setQueryData(['resumes'], [resumeBase, {
  ...resumeBase, id: 12, title: 'InternationalInfrastructureAndReliabilityEngineerWithoutWhitespace'.repeat(3),
  is_master: false, parent_resume_id: 11,
}]);
const taskController = createCoreTaskSurfaceController();
taskController.launch({ ref: { taskId: 'application.offer_review', applicationId: 42 }, source: 'application_header' });
const active = taskController.getState().active!;
taskController.markOpen(active.generation);

function HaruFixture() {
  const controller = usePilotConversationController();
  const assistant = useAssistantSurface();
  const triggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    controller.setFollowingContext({ view: 'applications-list', label, entity: { kind: 'application', id: '42', label } });
    controller.setAttachments([{ kind: 'resume', id: '11', label: '简历' }]);
    assistant.openHaru();
  }, []);
  // Exercise the production window's narrow 280px placement and its full 392px placement.
  const left = innerWidth <= 1008 ? 520 : innerWidth - 180;
  return <>
    <button ref={triggerRef} type="button" onClick={assistant.openHaru}>重新打开 Haru</button>
    <HaruChatWindow returnFocusRef={triggerRef} anchorRect={{ left, top: 80, right: left + 156, bottom: 128 }} />
  </>;
}

function Fixture() {
  return <ConfigProvider locale={zhCN} theme={theme === 'dark' ? darkTheme : lightTheme}><ThemeProvider>
    <Layout className="op-app-shell" style={{ minHeight: '100dvh', background: 'var(--op-layout-bg)' }} hasSider>
      <Sidebar view={surface === 'offer' ? 'offers' : surface === 'quick' ? 'interview' : surface === 'resumes' ? 'resumes' : 'applications-list'} onChange={() => {}} reminderCount={1} />
      <Layout className="op-app-main" style={{ background: 'var(--op-layout-bg)', minWidth: 0, width: '100%' }}>
        <TopBar compact={surface === 'offer'} onSearch={() => {}} onOpenSettings={() => {}} />
        <Layout.Content className="op-app-content" style={{ padding: '0 24px 24px' }}>
          {surface === 'offer-cards' ? <Row gutter={[16, 16]} data-offer-cards-fixture>{['InternationalInfrastructureCompanyWithoutSpaces'.repeat(3), '中文超长公司名称研发创新团队'.repeat(3)].map((company, index) => <Col key={company} xs={24} sm={12} md={8}><OfferCard offer={{ ...offer, id: 7 + index, company_name: company, position_name: index ? '超长中文岗位名称高级软件研发工程师与跨团队质量负责人'.repeat(3) : 'SeniorSoftwareEngineerWithLongUnbrokenEnglishPosition'.repeat(3), equity: 'UnbrokenEquityDescription'.repeat(4) }} selected={false} onToggleSelect={() => {}} onCoach={() => {}} onView={() => {}} onOpenApplication={() => {}} /></Col>)}</Row> : surface === 'resumes' ? <QueryClientProvider client={resumeClient}><ResumeLibraryView /></QueryClientProvider> : surface === 'offer' ? <CoreTaskSurfaceHost controller={taskController}>
            <OfferNegotiationDrawer open offer={offer} onClose={() => taskController.close(active.generation)} />
          </CoreTaskSurfaceHost> : surface === 'quick' ? <InterviewReadinessCenter fixedMode="quick" resumes={[
            { id: 11, title: `${label} · InternationalInfrastructureResume`, is_master: true, parent_resume_id: null, deleted_at: null },
          ]} /> : <AssistantSurfaceProvider><HaruFixture /></AssistantSurfaceProvider>}
        </Layout.Content>
      </Layout>
    </Layout>
  </ThemeProvider></ConfigProvider>;
}

createRoot(document.getElementById('root')!).render(<Fixture />);
