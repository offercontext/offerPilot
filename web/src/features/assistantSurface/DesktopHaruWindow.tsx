import { useEffect, useRef, useState } from 'react';
import type { DesktopHaruRequest, DesktopHaruState } from './desktopHaru';
import { live2dPilotMascotRuntime, type PilotMascotActivity } from '@/features/pilotMascot/live2dRuntime';
import './DesktopHaruWindow.css';

const STATUS = { idle: '随时待命', running: '正在处理', waiting_confirmation: '等待你确认', completed: '已完成', failed: '需要查看' };
const INITIAL: DesktopHaruState = { connected: false, generation: 0, snapshot: null, visible: false, expanded: false, alwaysOnTop: false };

function HaruCanvas({ active, activity }: { active: boolean; activity: PilotMascotActivity }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!active || !ref.current) return;
    const abort = new AbortController();
    let dispose: (() => void) | undefined;
    setFailed(false);
    // Idle uses a single static frame. Hide unmounts the runtime and its ticker.
    void live2dPilotMascotRuntime.mount(ref.current, abort.signal, activity === 'idle' ? 'off' : 'minimal').then(runtime => {
      if (abort.signal.aborted) { runtime.dispose(); return; }
      runtime.setActivity(activity);
      dispose = () => runtime.dispose();
    }).catch(() => { if (!abort.signal.aborted) setFailed(true); });
    return () => { abort.abort(); dispose?.(); };
  }, [active, activity]);
  return <div className="desktop-haru-portrait" aria-hidden="true"><canvas ref={ref} />{failed ? <span>Haru</span> : null}</div>;
}

export default function DesktopHaruWindow() {
  const bridge = window.offerpilotDesktop;
  const [state, setState] = useState<DesktopHaruState>(INITIAL);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const requestLock = useRef(false);
  const [notice, setNotice] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [pageVisible, setPageVisible] = useState(document.visibilityState !== 'hidden');
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
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
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'nearest' }); }, [state.snapshot?.messages]);
  const snapshot = state.snapshot;
  const active = state.visible && pageVisible;
  const activity: PilotMascotActivity = snapshot?.taskState === 'running' ? 'thinking' : snapshot?.hasPending ? 'waiting_confirmation' : snapshot?.taskState === 'failed' ? 'error' : 'idle';
  const windowAction = (action: 'show-main' | 'hide-haru' | 'toggle-top' | 'expand' | 'collapse') => { void bridge?.windowAction(action).catch(() => setNotice('窗口操作失败，请使用托盘菜单。')); };
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
  return <main className={`desktop-haru ${state.expanded ? 'expanded' : 'collapsed'}`} aria-label="Haru 桌面小窗" onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); windowAction(state.expanded ? 'collapse' : 'hide-haru'); } }}>
    <header className="desktop-haru-bar">
      <strong>Haru</strong>
      <nav aria-label="窗口操作">
        <button type="button" aria-label="Haru 始终置顶" aria-pressed={state.alwaysOnTop} onClick={() => windowAction('toggle-top')}>置顶</button>
        <button type="button" aria-label="打开 OfferPilot 主窗口" onClick={() => windowAction('show-main')}>主窗</button>
        <button type="button" aria-label="将 Haru 收到托盘" onClick={() => windowAction('hide-haru')}>×</button>
      </nav>
    </header>
    <button type="button" className="desktop-haru-avatar" aria-label={state.expanded ? '收起 Haru 对话' : '展开 Haru 对话'} onClick={() => windowAction(state.expanded ? 'collapse' : 'expand')}>
      <HaruCanvas active={active} activity={activity} />
      <span role="status">{state.connected && snapshot ? STATUS[snapshot.taskState] : '等待主窗口连接'}</span>
    </button>
    {state.expanded ? <section className="desktop-haru-chat" aria-label="Haru 对话">
      <p className="desktop-haru-context">当前上下文：{snapshot?.contextLabel || '工作台'}</p>
      <div className="desktop-haru-messages" aria-live="polite" aria-relevant="additions text">
        {!snapshot?.messages.length ? <p>和主窗口共用同一段对话。需要修改内容时，请到 Pilot 确认。</p> : snapshot.messages.map((message, index) => <article key={index} data-role={message.role}><b>{message.role === 'user' ? '你' : 'Haru'}</b><p>{message.content}</p></article>)}
        {snapshot?.loading ? <p role="status">正在处理…</p> : null}<div ref={end} />
      </div>
      {snapshot?.hasPending ? <div className="desktop-haru-pending" role="status">有一项修改等待确认。<button type="button" disabled={busy || !state.connected} onClick={() => void request('open-pending')}>到 Pilot 查看并确认</button></div> : null}
      {!state.connected ? <p role="alert">主窗口暂未连接。发送与停止已禁用；请打开主窗口恢复。</p> : null}
      {snapshot?.error ? <p role="alert">{snapshot.error}</p> : null}
      {notice ? <p role="alert">{notice}</p> : null}
      {uncertain ? <button type="button" onClick={() => { setDraft(''); setUncertain(false); setNotice(''); }}>已在主窗口核对，清空此输入</button> : null}
      <footer>
        <label className="desktop-haru-label" htmlFor="desktop-haru-input">给 Haru 发消息</label>
        <textarea id="desktop-haru-input" ref={input} rows={2} maxLength={16000} value={draft} disabled={!state.connected || !snapshot?.canSend || busy || uncertain} placeholder={snapshot?.hasPending ? '请先到 Pilot 确认' : '问 Haru…'} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (draft.trim() && snapshot?.canSend) void request('send'); } }} />
        {snapshot?.canStop || snapshot?.loading ? <button type="button" disabled={!state.connected || !snapshot?.canStop || snapshot.stopping || busy || uncertain} onClick={() => void request('stop')}>{snapshot?.stopping ? '正在停止…' : '停止'}</button> : <button type="button" disabled={!state.connected || !snapshot?.canSend || !draft.trim() || busy || uncertain} onClick={() => void request('send')}>发送</button>}
        {snapshot?.stopMessage ? <p role="status">{snapshot.stopMessage}</p> : null}
        <small>关闭小窗不会停止任务。退出并停止服务请使用托盘菜单。</small>
      </footer>
    </section> : null}
  </main>;
}
