import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { CloseOutlined, HolderOutlined, RightOutlined, UnorderedListOutlined, UpOutlined, UserOutlined } from '@ant-design/icons';
import type { PilotMascotAnimationLevel } from '@/features/pilotMascot/pilotMascotPreference';
import type { DesktopHaruRequest, DesktopHaruState } from './desktopHaru';
import { live2dPilotMascotRuntime, type PilotMascotActivity } from '@/features/pilotMascot/live2dRuntime';
import { useThemeMode } from '@/theme/ThemeContext';
import haruWindowLight from './assets/haru-window-light.png';
import haruWindowDark from './assets/haru-window-dark.png';
import './DesktopHaruWindow.css';

const STATUS = { idle: '随时待命', running: '正在处理', waiting_confirmation: '等待你确认', completed: '已完成', failed: '需要查看' };
const GREETING = { idle: '慢慢来，我陪你捋清楚。', running: '我正在整理，稍等一下。', waiting_confirmation: '有一项修改，等你确认。', completed: '整理好了，一起看看吧。', failed: '遇到了一点问题，一起看看。' };
const INITIAL: DesktopHaruState = { connected: false, generation: 0, snapshot: null, visible: false, expanded: false, alwaysOnTop: true };

function HaruCanvas({ active, activity, animationLevel }: { active: boolean; activity: PilotMascotActivity; animationLevel: PilotMascotAnimationLevel }) {
  const ref = useRef<HTMLDivElement>(null);
  const [motionRevision, setMotionRevision] = useState(0);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const changed = () => setMotionRevision(value => value + 1);
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, []);
  const [runtimeState, setRuntimeState] = useState<'loading' | 'ready' | 'failed' | 'hidden'>('hidden');
  useEffect(() => {
    if (!active || !ref.current) { setRuntimeState('hidden'); return; }
    // Pixi destroys the WebGL context on dispose. A disposed canvas cannot be
    // reused for another activity or after hiding, including StrictMode remounts.
    const canvas = document.createElement('canvas');
    ref.current.append(canvas);
    const abort = new AbortController();
    let dispose: (() => void) | undefined;
    setRuntimeState('loading');
    // Keep the desktop companion alive while idle. The runtime still honors
    // reduced motion; hiding or expanding disposes its ticker.
    void live2dPilotMascotRuntime.mount(canvas, abort.signal, animationLevel).then(runtime => {
      if (abort.signal.aborted) { runtime.dispose(); return; }
      dispose = () => runtime.dispose();
      runtime.setActivity(activity);
      setRuntimeState('ready');
    }).catch(() => {
      if (!abort.signal.aborted) {
        dispose?.();
        dispose = undefined;
        canvas.remove();
        setRuntimeState('failed');
      }
    });
    return () => { abort.abort(); dispose?.(); canvas.remove(); };
  }, [active, activity, animationLevel, motionRevision]);
  return <div ref={ref} className="desktop-haru-portrait" data-runtime-state={runtimeState} aria-hidden="true" />;
}

export default function DesktopHaruWindow() {
  const bridge = window.offerpilotDesktop;
  const { mode } = useThemeMode();
  const [state, setState] = useState<DesktopHaruState>(INITIAL);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const requestLock = useRef(false);
  const [notice, setNotice] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [contextExpanded, setContextExpanded] = useState(false);
  const [pageVisible, setPageVisible] = useState(document.visibilityState !== 'hidden');
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const contextToggle = useRef<HTMLButtonElement>(null);
  const gesture = useRef<{ pointerId: number; x: number; y: number; dragged: boolean } | null>(null);
  const suppressClick = useRef(false);
  useEffect(() => {
    document.documentElement.classList.add('desktop-haru-root');
    return () => document.documentElement.classList.remove('desktop-haru-root');
  }, []);
  useEffect(() => {
    let alive = true;
    let gotEvent = false;
    const off = bridge?.onState(value => { gotEvent = true; if (alive) setState(value); });
    void bridge?.getState().then(value => { if (alive && value && !gotEvent) setState(value); }).catch(() => setNotice('连接不可用，请打开主窗口。'));
    const visibility = () => setPageVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', visibility);
    return () => { alive = false; off?.(); document.removeEventListener('visibilitychange', visibility); };
  }, [bridge]);
  useEffect(() => { if (state.expanded && state.visible) input.current?.focus(); }, [state.expanded, state.visible]);
  useEffect(() => { if (!state.expanded) setContextExpanded(false); }, [state.expanded]);
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'nearest' }); }, [state.snapshot?.messages]);
  useEffect(() => {
    const cancel = () => {
      if (!gesture.current) return;
      gesture.current = null;
      suppressClick.current = true;
      void bridge?.dragWindow?.('cancel').catch(() => {});
    };
    window.addEventListener('blur', cancel);
    return () => { window.removeEventListener('blur', cancel); cancel(); };
  }, [bridge, state.expanded, state.visible]);
  const snapshot = state.snapshot;
  // Owner reloads clear the snapshot, not the user's last explicit animation choice.
  const retainedAnimationLevel = useRef<PilotMascotAnimationLevel>('full');
  if (snapshot?.animationLevel === 'full' || snapshot?.animationLevel === 'minimal' || snapshot?.animationLevel === 'off') {
    retainedAnimationLevel.current = snapshot.animationLevel;
  }
  const theme = snapshot?.theme ?? mode;
  const portraitSource = theme === 'dark' ? haruWindowDark : haruWindowLight;
  const active = state.visible && pageVisible;
  const activity: PilotMascotActivity = snapshot?.taskState === 'running' ? 'thinking' : snapshot?.hasPending ? 'waiting_confirmation' : snapshot?.taskState === 'failed' ? 'error' : 'idle';
  const windowAction = (action: 'show-main' | 'show-ai-settings' | 'hide-haru' | 'context-menu' | 'toggle-top' | 'expand' | 'collapse') => { void bridge?.windowAction(action).then(ok => { if (!ok) setNotice('窗口操作失败，请使用托盘菜单。'); }).catch(() => setNotice('窗口操作失败，请使用托盘菜单。')); };
  const dragWindow = (phase: 'start' | 'move' | 'end' | 'cancel') => { void bridge?.dragWindow?.(phase).catch(() => setNotice('拖动暂不可用，请重试。')); };
  const moved = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const current = gesture.current;
    return current && Math.hypot(event.screenX - current.x, event.screenY - current.y) >= 5;
  };
  const finishGesture = (event: ReactPointerEvent<HTMLButtonElement>, cancelled = false) => {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) return;
    suppressClick.current = cancelled || current.dragged || Boolean(moved(event));
    gesture.current = null;
    dragWindow(cancelled ? 'cancel' : 'end');
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const request = async (action: DesktopHaruRequest['action']) => {
    if (!bridge?.request || !snapshot || !state.connected || requestLock.current || uncertain) return;
    const submitted = draft;
    requestLock.current = true;
    setBusy(true);
    setNotice('');
    try {
      const result = await bridge.request({ action, version: snapshot.version, generation: state.generation, ...(action === 'send' ? { text: submitted } : {}) });
      if (result.ok) {
        if (action === 'send') setDraft(value => value === submitted ? '' : value);
      } else if (result.reason === 'uncertain') {
        setUncertain(true);
        setNotice('发送结果暂未确认。请打开主窗口核对，避免重复发送。');
      } else setNotice(result.reason === 'stale' ? '对话状态已变化，请核对最新内容后再操作。' : result.reason === 'busy' ? '任务正在处理中，请稍后再试。' : '主窗口连接不可用，请打开主窗口。');
    } catch {
      setUncertain(true);
      setNotice('连接中断，操作可能已经提交。请打开主窗口核对。');
    } finally { requestLock.current = false; setBusy(false); }
  };
  return <main className={`desktop-haru ${state.expanded ? 'expanded' : 'collapsed'}`} data-theme={theme} aria-label="Haru 桌面小窗" onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); if (contextExpanded) { setContextExpanded(false); contextToggle.current?.focus(); } else if (state.expanded) windowAction('collapse'); } }}>
    {state.expanded ? <header className="desktop-haru-bar">
      <strong>Haru</strong>
      <HolderOutlined className="desktop-haru-grip" aria-hidden="true" />
      <nav aria-label="窗口操作">
        <button type="button" aria-label="Haru 始终置顶" aria-pressed={state.alwaysOnTop} onClick={() => windowAction('toggle-top')}>置顶</button>
        <button type="button" aria-label="打开 OfferPilot 主窗口" onClick={() => windowAction('show-main')}>主窗</button>
        <button type="button" aria-label="关闭 Haru 对话" onClick={() => windowAction('collapse')}><CloseOutlined aria-hidden="true" /></button>
      </nav>
    </header> : null}
    <button type="button" className="desktop-haru-avatar" aria-label={state.expanded ? '收起 Haru 对话' : '展开 Haru 对话'}
      onContextMenu={event => { event.preventDefault(); windowAction('context-menu'); }}
      onDragStart={event => event.preventDefault()}
      onPointerDown={event => {
        if (state.expanded || event.button !== 0 || event.isPrimary === false || gesture.current) return;
        suppressClick.current = false;
        gesture.current = { pointerId: event.pointerId, x: event.screenX, y: event.screenY, dragged: false };
        event.currentTarget.setPointerCapture?.(event.pointerId);
        dragWindow('start');
      }}
      onPointerMove={event => {
        const current = gesture.current;
        if (!current || current.pointerId !== event.pointerId || (!current.dragged && !moved(event))) return;
        current.dragged = true;
        dragWindow('move');
      }}
      onPointerUp={event => finishGesture(event)}
      onPointerCancel={event => finishGesture(event, true)}
      onLostPointerCapture={event => finishGesture(event, true)}
      onClick={event => {
        if (suppressClick.current && event.detail !== 0) { suppressClick.current = false; return; }
        suppressClick.current = false;
        windowAction(state.expanded ? 'collapse' : 'expand');
      }}>
      {state.expanded ? <>
      <img className="desktop-haru-static-portrait" src={portraitSource} alt="" aria-hidden="true" draggable={false} />
      <span className="desktop-haru-speech" aria-hidden="true">{state.connected && snapshot ? GREETING[snapshot.taskState] : '打开主窗，我们就能继续聊。'}</span>
      <span className="desktop-haru-presence" role="status"><i aria-hidden="true" data-activity={activity} />{state.connected && snapshot ? STATUS[snapshot.taskState] : '等待主窗口连接'}</span>
      <span className="desktop-haru-expand-hint" aria-hidden="true">收起对话 <UpOutlined /></span>
      </> : <HaruCanvas active={active} activity={activity} animationLevel={retainedAnimationLevel.current} />}
    </button>
    {state.expanded ? <section className="desktop-haru-chat" aria-label="Haru 对话">
      <div className="desktop-haru-context">
        <button type="button" className="desktop-haru-context-toggle" ref={contextToggle} aria-expanded={contextExpanded} aria-controls="desktop-haru-context-detail" onClick={() => setContextExpanded(value => !value)}>
          <UnorderedListOutlined className="desktop-haru-context-icon" aria-hidden="true" />
          <span className="desktop-haru-context-title">当前：{snapshot?.contextLabel || '工作台'}</span><RightOutlined className="desktop-haru-context-chevron" aria-hidden="true" />
        </button>
        {contextExpanded ? <div id="desktop-haru-context-detail" className="desktop-haru-context-detail">
          <p>{snapshot?.contextLabel || '工作台'}</p>
          <span>与主窗口共用对话，切换上下文请前往主窗。</span>
          <button type="button" onClick={() => windowAction('show-main')}>在主窗查看</button>
        </div> : null}
      </div>
      <div className="desktop-haru-conversation-heading"><span>当前对话</span></div>
      <div className="desktop-haru-messages" aria-live="polite" aria-relevant="additions text">
        {!snapshot?.messages.length ? <div className="desktop-haru-empty"><span>今天想聊些什么？</span><p>一起理清思路，慢慢往前走。</p></div> : snapshot.messages.map((message, index) => <article key={index} data-role={message.role}>
          <span className="desktop-haru-message-avatar" aria-hidden={message.role === 'user' ? true : undefined}>{message.role === 'user' ? <UserOutlined /> : <img src={portraitSource} alt="Haru 头像" draggable={false} />}</span>
          <div className="desktop-haru-message-bubble"><b className="desktop-haru-label">{message.role === 'user' ? '你' : 'Haru'}</b><p>{message.content}</p></div>
        </article>)}
        {snapshot?.loading ? <p className="desktop-haru-loading" role="status">正在处理…</p> : null}<div ref={end} />
      </div>
      {snapshot?.hasPending ? <div className="desktop-haru-pending" role="status">有一项修改等待确认。<button type="button" disabled={busy || !state.connected} onClick={() => void request('open-pending')}>到 Pilot 查看并确认</button></div> : null}
      {!state.connected ? <p role="alert">主窗口暂未连接。发送与停止已禁用；请打开主窗口恢复。</p> : null}
      {state.connected && snapshot?.aiConfigured === false ? <div id="desktop-haru-ai-setup" className="desktop-haru-pending" role="status">尚未配置 AI，暂时不能发送。你可以先写草稿，配置后继续。<button type="button" onClick={() => windowAction('show-ai-settings')}>去配置 AI</button></div> : null}
      {snapshot?.error ? <p role="alert">{snapshot.error}</p> : null}
      {notice ? <p role="alert">{notice}</p> : null}
      {uncertain ? <button type="button" className="desktop-haru-reconcile" onClick={() => { setDraft(''); setUncertain(false); setNotice(''); }}>已在主窗口核对，清空此输入</button> : null}
      <footer>
        <div className="desktop-haru-composer">
          <label className="desktop-haru-label" htmlFor="desktop-haru-input">给 Haru 发消息</label>
          <textarea id="desktop-haru-input" ref={input} rows={2} maxLength={16000} value={draft} disabled={!state.connected || (!snapshot?.canSend && snapshot?.aiConfigured !== false) || busy || uncertain || snapshot?.loading || snapshot?.hasPending} aria-describedby={snapshot?.aiConfigured === false ? 'desktop-haru-ai-setup' : undefined} placeholder={snapshot?.hasPending ? '请先到 Pilot 确认' : '和 Haru 说点什么…'} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (draft.trim() && snapshot?.canSend) void request('send'); } }} />
          {snapshot?.canStop || snapshot?.loading ? <button type="button" className="desktop-haru-submit desktop-haru-stop" disabled={!state.connected || !snapshot?.canStop || snapshot.stopping || busy || uncertain} onClick={() => void request('stop')}>{snapshot?.stopping ? '正在停止…' : '停止'}</button> : <button type="button" className="desktop-haru-submit" disabled={!state.connected || !snapshot?.canSend || !draft.trim() || busy || uncertain} onClick={() => void request('send')}>发送</button>}
        </div>
        {snapshot?.stopMessage ? <p role="status">{snapshot.stopMessage}</p> : null}
        <small>关闭小窗不会停止任务。退出并停止服务请使用托盘菜单。</small>
      </footer>
    </section> : null}
  </main>;
}
