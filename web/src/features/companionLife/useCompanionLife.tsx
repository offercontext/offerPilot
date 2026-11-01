import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { getCompanion, sendCompanionCommand, reportCompanionPresence, claimCompanionBid } from '@/services/companion';
import type { PilotConversationController } from '@/features/assistantSurface/usePilotConversationController';
import type { CompanionBid, CompanionCommand, CompanionEvent, CompanionSettings, CompanionSignals, CompanionSnapshot } from './contracts';
import { acceptCompanionSnapshot, blocksCompanion, canPresentBid, companionMirror } from './model';

export function useCompanionLifeController(controller: PilotConversationController) {
  const [snapshot, setSnapshot] = useState<CompanionSnapshot | null>(null);
  const current = useRef<CompanionSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [bid, setBid] = useState<CompanionBid | null>(null);
  const [optimisticQuiet, setOptimisticQuiet] = useState(false);
  const quiet = optimisticQuiet || snapshot?.presence.session_quiet === true;
  const [mockInterview, setMockInterview] = useState(false);
  const [desktopChatOpen, setDesktopChatOpen] = useState(false);
  const [desktopVisible, setDesktopVisible] = useState(false);
  const [presentationAvailable, setPresentationAvailable] = useState(true);
  const canShow = window.offerpilotDesktop?.role === 'owner' ? desktopVisible && !desktopChatOpen : presentationAvailable;
  const displayRef = useRef(canShow); displayRef.current = canShow;
  const [visible, setVisible] = useState(document.visibilityState !== 'hidden');
  const [inputActive, setInputActive] = useState(false);
  const [networkFailure, setNetworkFailure] = useState(false);
  const client = useRef(crypto.randomUUID());
  const mounted = useRef(false);
  const locked = useRef(false);
  const unresolved = useRef<CompanionCommand | null>(null);
  const epoch = useRef(0);
  const inFlightBid = useRef(false);
  const presenceSequence = useRef(0);
  const signals: CompanionSignals = {
    visible: visible || desktopVisible, typing: inputActive || desktopChatOpen || Boolean(controller.composerDraft?.trim()),
    work_running: controller.loading || controller.executionControl?.execution?.state === 'running',
    awaiting_approval: Boolean(controller.pending || controller.activePendingRef.current),
    mock_interview: mockInterview, quiet,
    failure: networkFailure || Boolean(controller.lastError || controller.confirmError) || controller.taskState === 'failed',
  };
  const signalRef = useRef(signals); signalRef.current = signals;
  const apply = useCallback((next: CompanionSnapshot) => {
    if (!mounted.current) return;
    const accepted = acceptCompanionSnapshot(current.current, next);
    if (current.current && accepted.identity !== current.current.identity) {
      epoch.current += 1; setBid(null); setOptimisticQuiet(false);
      if (unresolved.current) setError('日常状态已变化，请核对后再操作。');
      unresolved.current = null; setUncertain(false);
    }
    current.current = accepted; setSnapshot(accepted);
  }, []);
  const refresh = useCallback(async () => {
    const readEpoch = epoch.current;
    const next = await getCompanion();
    if (readEpoch === epoch.current) apply(next);
    return next;
  }, [apply]);
  const execute = useCallback(async (command: CompanionCommand, reportConflict = true) => {
    if (!mounted.current || locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try {
      const next = await sendCompanionCommand(command);
      if (!mounted.current) return;
      presenceSequence.current += 1;
      apply(next);
      if (command.kind === 'event' && (command.body.event === 'quiet_session' || command.body.event === 'resume_session')) setOptimisticQuiet(false);
      unresolved.current = null; setUncertain(false); setNetworkFailure(false);
    } catch (cause) {
      if (!mounted.current) return;
      if (current.current && current.current.identity !== command.body.identity) {
        unresolved.current = null; setUncertain(false); setOptimisticQuiet(false);
        setError('日常状态已变化，请核对后再操作。'); return;
      }
      const status = (cause as { response?: { status?: number } }).response?.status;
      if (status === 409 || status === 422 || status === 400) {
        if (command.kind === 'event' && (command.body.event === 'quiet_session' || command.body.event === 'resume_session')) setOptimisticQuiet(false);
        unresolved.current = null; setUncertain(false);
        if (reportConflict) setError('日常状态已变化，请核对后再操作。');
        try { await refresh(); } catch { setNetworkFailure(true); }
      } else {
        // A lost response may already have committed. Only retry this exact
        // receipt envelope, never mint a second command for the same click.
        unresolved.current = command; setUncertain(true); setNetworkFailure(true);
        setError('操作结果暂未确认。重试会核对同一次操作，不会重复提交。');
      }
    } finally { locked.current = false; if (mounted.current) setBusy(false); }
  }, [apply, refresh]);
  const event = useCallback((event: CompanionEvent, explicit = false) => {
    const state = current.current;
    if (!state || locked.current || unresolved.current || (!explicit && !state.settings.enabled)) return;
    const activity_id = state.state.activity?.id;
    const wish_id = state.state.wish?.id;
    if (explicit) setBid(null);
    return execute({ kind: 'event', body: { identity: state.identity, expected_revision: state.revision, command_id: crypto.randomUUID(), event,
      ...(activity_id ? { activity_id } : {}), ...(wish_id ? { wish_id } : {}) } }, explicit);
  }, [execute]);
  const setQuiet = useCallback((value: boolean) => {
    if (!current.current || locked.current || unresolved.current) return;
    if (value) { signalRef.current.quiet = true; setOptimisticQuiet(true); setBid(null); }
    // Quiet belongs to the server usage session. Resume only after its receipt.
    return event(value ? 'quiet_session' : 'resume_session', true);
  }, [event]);
  const updateSettings = useCallback((settings: CompanionSettings) => {
    const state = current.current;
    if (!state || unresolved.current) return;
    setBid(null);
    return execute({ kind: 'settings', body: { identity: state.identity, expected_revision: state.revision, command_id: crypto.randomUUID(), settings } });
  }, [execute]);
  const reset = useCallback(() => {
    const state = current.current;
    if (!state || unresolved.current) return;
    return execute({ kind: 'reset', body: { identity: state.identity, expected_revision: state.revision, command_id: crypto.randomUUID(), confirmed: true } });
  }, [execute]);
  const retry = useCallback(() => {
    if (unresolved.current) return execute(unresolved.current);
    setError(''); return refresh().then(() => setNetworkFailure(false)).catch(() => { setNetworkFailure(true); setError('虚拟日常暂时不可用，请稍后重试。'); });
  }, [execute, refresh]);
  const heartbeat = useCallback(async (claim = true) => {
    const state = current.current;
    if (!mounted.current || !state) return;
    const generation = epoch.current;
    const sequence = ++presenceSequence.current;
    let claimed = false;
    try {
      const presence = await reportCompanionPresence(state.identity, client.current, sequence, signalRef.current);
      if (!mounted.current || sequence !== presenceSequence.current || generation !== epoch.current || state.identity !== current.current?.identity || state.revision !== current.current?.revision) return;
      apply({ ...current.current, presence });
      setNetworkFailure(false);
      const latest = current.current;
      if (!claim || !displayRef.current || inFlightBid.current || locked.current || unresolved.current || !latest || presence.blocked || blocksCompanion(signalRef.current)
        || !latest.settings.enabled || latest.settings.proactivity === 'off' || latest.settings.hide_status) return;
      inFlightBid.current = true; claimed = true;
      const candidate = await claimCompanionBid(latest.identity, latest.revision, client.current);
      if (mounted.current && generation === epoch.current && displayRef.current && candidate && canPresentBid(candidate, current.current, signalRef.current)) setBid(candidate);
    } catch { if (mounted.current && generation === epoch.current) { setNetworkFailure(true); setBid(null); } }
    finally { if (claimed) inFlightBid.current = false; }
  }, [apply]);
  useEffect(() => {
    mounted.current = true;
    let active = true;
    void refresh().then(async () => {
      if (!active) return;
      await heartbeat(false);
      if (active) await event('app_open');
    }).catch(() => { if (active) { setNetworkFailure(true); setError('虚拟日常暂时不可用，请稍后重试。'); } });
    const visibility = () => { const next = document.visibilityState !== 'hidden'; signalRef.current.visible = next; setVisible(next); };
    // Text entry anywhere in the workspace is a conservative interruption guard.
    const focus = () => { const target = document.activeElement; const editing = target instanceof HTMLElement && (target.matches('input, textarea, [contenteditable="true"]')); signalRef.current.typing = editing || Boolean(controller.composerDraft?.trim()); setInputActive(editing); };
    document.addEventListener('visibilitychange', visibility);
    document.addEventListener('focusin', focus); document.addEventListener('focusout', focus);
    const bridge = window.offerpilotDesktop;
    const off = bridge?.role === 'owner' ? bridge.onState?.(state => { setDesktopVisible(state.visible); setDesktopChatOpen(state.visible && state.expanded); }) : undefined;
    if (bridge?.role === 'owner') void Promise.resolve(bridge.getState?.()).then(state => { if (active && state) { setDesktopVisible(state.visible); setDesktopChatOpen(state.visible && state.expanded); } }).catch(() => {});
    const interval = window.setInterval(() => {
      if (!signalRef.current.visible) return;
      void refresh().then(() => heartbeat()).catch(() => { if (active) setNetworkFailure(true); });
    }, 20000);
    return () => {
      active = false; mounted.current = false; epoch.current += 1; window.clearInterval(interval); off?.();
      document.removeEventListener('visibilitychange', visibility); document.removeEventListener('focusin', focus); document.removeEventListener('focusout', focus);
      const state = current.current;
      if (state) void reportCompanionPresence(state.identity, client.current, ++presenceSequence.current, { ...signalRef.current, visible: false }).catch(() => {});
    };
  }, [event, heartbeat, refresh]);
  // Transport failure recovery must not recursively retrigger its own heartbeat.
  const signalKey = JSON.stringify({ ...signals, canShow, failure: Boolean(controller.lastError || controller.confirmError) || controller.taskState === 'failed' });
  useEffect(() => {
    if (!displayRef.current || blocksCompanion(signalRef.current)) setBid(null);
    void heartbeat();
  }, [signalKey, heartbeat]);
  const shownBid = canShow && bid && canPresentBid(bid, snapshot, signals) ? bid : null;
  const mirror = companionMirror(snapshot, signals, shownBid);
  return { snapshot, signals, busy, error, uncertain, bid: shownBid,
    mirror: mirror ? { ...mirror, ...(shownBid?.source_event_type ? { invitation: shownBid.source_event_type } : {}) } : undefined, event, updateSettings, reset, retry, quiet, setQuiet, setMockInterview, setPresentationAvailable,
    dismissBid: () => setBid(null) };
}
export type CompanionLifeController = ReturnType<typeof useCompanionLifeController>;
export const CompanionLifeContext = createContext<CompanionLifeController | null>(null);
export const useCompanionLife = () => useContext(CompanionLifeContext);
export function CompanionLifeSignals({ mockInterview, presentationAvailable }: { mockInterview: boolean; presentationAvailable: boolean }) {
  const life = useCompanionLife();
  const setter = life?.setMockInterview;
  const displaySetter = life?.setPresentationAvailable;
  useEffect(() => { displaySetter?.(presentationAvailable); return () => displaySetter?.(false); }, [presentationAvailable, displaySetter]);
  useEffect(() => { setter?.(mockInterview); return () => setter?.(false); }, [mockInterview, setter]);
  return null;
}
