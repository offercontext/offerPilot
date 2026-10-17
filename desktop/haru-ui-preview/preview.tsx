import { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import DesktopHaruWindow from '../../web/src/features/assistantSurface/DesktopHaruWindow';
import type { DesktopHaruState, DesktopHaruWindowAction } from '../../web/src/features/assistantSurface/desktopHaru';
import './preview.css';

const scenarios = ['idle', 'empty', 'running', 'pending', 'offline', 'offline-running', 'long', 'collapsed'] as const;
type Scenario = typeof scenarios[number];
type PreviewObservation = { scenario: Scenario; connected: boolean; expanded: boolean; visible: boolean; requests: string[]; windowActions: string[]; mainWindowRequests: number; theme: 'light' | 'dark'; dragPhases: string[] };
type PreviewControls = { setScenario: (scenario: Scenario) => void; observe: () => PreviewObservation; setTheme: (theme: 'light' | 'dark') => void };
declare global { interface Window { haruUiPreview?: PreviewControls } }
const params = new URLSearchParams(location.search);
const surface = params.get('surface') === '1';
const suppliedScenario = params.get('scenario') as Scenario;
let scenario: Scenario = scenarios.includes(suppliedScenario) ? suppliedScenario : 'idle';
const initialMessages = [
  { role: 'user' as const, content: '今天的面试有点没发挥好' },
  { role: 'assistant' as const, content: '先不用急着给自己打分。哪一个问题让你卡住了？' },
];
let version = 0;
let theme: 'light' | 'dark' = params.get('theme') === 'dark' ? 'dark' : 'light';
const dragPhases: string[] = [];
let state: DesktopHaruState;
const listeners = new Set<(next: DesktopHaruState) => void>();
const requests: string[] = [];
const windowActions: string[] = [];
let mainWindowRequests = 0;
function publish() {
  if (state.snapshot) state = { ...state, snapshot: { ...state.snapshot, version: ++version } };
  for (const listener of listeners) listener(structuredClone(state));
  window.dispatchEvent(new Event('haru-preview-state'));
}
function setScenario(next: Scenario) {
  if (!scenarios.includes(next)) throw new Error('Unknown synthetic preview scenario');
  scenario = next;
  const running = next === 'running' || next === 'offline-running';
  const pending = next === 'pending';
  const connected = next !== 'offline' && next !== 'offline-running';
  state = {
    connected, generation: 1, visible: true, expanded: next !== 'collapsed', alwaysOnTop: false,
    snapshot: {
      theme, version, conversationId: 101, taskState: running ? 'running' : pending ? 'waiting_confirmation' : 'idle',
      messages: next === 'empty' ? [] : next === 'long' ? [
        { role: 'user', content: '请帮我梳理一下这段面试经历。' },
        { role: 'assistant', content: '【本地合成长回复】\n' + '我们先记录问题、当时的回答，以及后来想到的补充。\n'.repeat(20) },
      ] : structuredClone(initialMessages),
      contextLabel: '测试开发岗位', loading: running, hasPending: pending,
      canSend: connected && !running && !pending, canStop: running, stopping: false, error: '', stopMessage: '',
    },
  };
  publish();
}

if (surface) {
  // This bridge is deliberately only in the fixture. No backend, AI provider,
  // real conversation, native IPC, approval, or product-state write is involved.
  window.offerpilotDesktop = {
    role: 'haru',
    getState: async () => structuredClone(state),
    dragWindow: async phase => { dragPhases.push(phase); return true; },
    onState: listener => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    windowAction: async (action: DesktopHaruWindowAction) => {
      windowActions.push(action);
      if (action === 'toggle-top') state = { ...state, alwaysOnTop: !state.alwaysOnTop };
      if (action === 'collapse' || action === 'expand') state = { ...state, expanded: action === 'expand' };
      if (action === 'hide-haru' || action === 'show-haru') state = { ...state, visible: action === 'show-haru' };
      if (action === 'show-main') mainWindowRequests++;
      publish();
      return true;
    },
    request: async request => {
      const snapshot = state.snapshot;
      if (!snapshot || !state.connected) return { ok: false, reason: 'unavailable' };
      if (request.version !== snapshot.version || request.generation !== state.generation) return { ok: false, reason: 'stale' };
      if (request.action === 'send') {
        if (!snapshot.canSend || !request.text?.trim()) return { ok: false, reason: 'busy' };
        state = { ...state, snapshot: { ...snapshot, taskState: 'running', loading: true, canSend: false, canStop: true, stopMessage: '', messages: [
          ...snapshot.messages, { role: 'user', content: request.text },
          { role: 'assistant', content: '【本地预览合成回复】我在听，我们可以一起把刚才的问题梳理清楚。' },
        ] } };
      } else if (request.action === 'stop') {
        if (!snapshot.canStop) return { ok: false, reason: 'busy' };
        state = { ...state, snapshot: { ...snapshot, taskState: 'idle', loading: false, canSend: true, canStop: false, stopMessage: '已停止本次生成。' } };
      } else {
        if (!snapshot.hasPending) return { ok: false, reason: 'unavailable' };
        mainWindowRequests++;
      }
      requests.push(request.action);
      publish();
      return { ok: true };
    },
  };
  window.haruUiPreview = {
    setScenario,
    setTheme: next => { theme = next; if (state.snapshot) state.snapshot = { ...state.snapshot, theme }; publish(); },
    observe: () => ({ scenario, connected: state.connected, expanded: state.expanded, visible: state.visible,
      requests: [...requests], windowActions: [...windowActions], mainWindowRequests, theme, dragPhases: [...dragPhases] }),
  };
  setScenario(scenario);
}

function PreviewGallery() {
  const frame = useRef<HTMLIFrameElement>(null);
  const [compact, setCompact] = useState(false);
  const [short, setShort] = useState(false);
  const onLoad = () => {
    const child = frame.current?.contentWindow;
    child?.addEventListener('haru-preview-state', () => setCompact(!child.haruUiPreview?.observe().expanded));
  };
  return <div className="preview-gallery">
    <aside>
      <h1>Haru 桌面 UI 预览</h1>
      <p>真实产品界面、原 Live2D 角色和运行时。对话与桌面桥接全部为本地合成数据，没有调用 AI、后端或真实桌面程序。</p>
      <p>“到 Pilot 查看并确认”仅记录转主窗请求，不会批准修改。“发送”进入合成生成状态，可点击“停止”。</p>
      <div className="preview-controls">
        {(['light', 'dark'] as const).map(mode => <button key={mode} onClick={() => frame.current?.contentWindow?.haruUiPreview?.setTheme(mode)}>{mode}</button>)}
        {scenarios.map(id => <button key={id} onClick={() => frame.current?.contentWindow?.haruUiPreview?.setScenario(id)}>{id}</button>)}
        <button onClick={() => setShort(value => !value)}>{short ? '恢复 740 高' : '查看 620 高'}</button>
      </div>
      <p><a href="?surface=1">直接打开窗口界面</a></p>
      <p>浏览器预览不证明 EXE、托盘、IPC、拖动或操作系统置顶功能通过。</p>
    </aside>
    <iframe ref={frame} onLoad={onLoad} title="真实 Haru 界面，合成对话" src="?surface=1" width={compact ? 260 : 420} height={compact ? 340 : short ? 620 : 740} />
  </div>;
}

createRoot(document.getElementById('preview-root')!).render(surface ? <DesktopHaruWindow /> : <PreviewGallery />);
