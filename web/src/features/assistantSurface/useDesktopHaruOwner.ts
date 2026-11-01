import type { CompanionMirror } from '@/features/companionLife/contracts';
import { t, useLocale } from '@/i18n';
import { pilotPageContextControlProjection } from '@/lib/pilotPageContext';
import { useEffect, useRef, useState } from 'react';
import type { PilotConversationController } from './usePilotConversationController';
import { DESKTOP_HARU_SEND_FAILURE, type DesktopHaruCommand, type DesktopHaruSnapshot } from './desktopHaru';
import { compactMessageText } from './assistantPresentation';
import { readPilotMascotAnimationLevel, PILOT_MASCOT_ANIMATION_KEY, PILOT_MASCOT_ANIMATION_CHANGED } from '@/features/pilotMascot/pilotMascotPreference';
import { useThemeMode } from '@/theme/ThemeContext';

export function desktopHaruSnapshot(controller: PilotConversationController): Omit<DesktopHaruSnapshot, 'version'> {
  const current = controller.conversations.find(item => item.id === controller.conversationId);
  const context = controller.requestContextSnapshot ?? controller.pinnedContext ?? controller.followingContext;
  // Match the web Haru label order; a draft only describes a new conversation.
  const contextLabel =
    controller.requestContextSnapshot?.entity?.label ||
    controller.requestContextSnapshot?.label ||
    (controller.conversationId === undefined ? controller.draftContext?.context_label : undefined) ||
    current?.context_label ||
    (current?.context_type === 'application' && current.context_ref ? t('投递 #{id}', { id: current.context_ref }) : undefined) ||
    context?.entity?.label || context?.label ||
    (controller.conversationId ? t('工作台') : t('当前页面'));
  return {
    conversationId: controller.conversationId ?? null,
    taskState: controller.taskState,
    // Only visible prose crosses this boundary. In particular, do not serialize
    // action cards, tool arguments, active request refs or pending objects.
    messages: (controller.displayTurns ?? controller.turns).slice(-12).map(turn => ({ role: turn.role, content: compactMessageText(turn).slice(0, 12000) })),
    contextLabel: contextLabel.slice(0, 400),
    loading: controller.loading,
    hasPending: Boolean(controller.pending),
    aiConfigured: controller.hasKey,
    canSend: controller.isActionOwnerReady() && controller.hasKey && !controller.loading && !controller.pending && !controller.activeRequestRef.current && controller.activeConversationSelectionRef.current === null && !controller.activePendingRef.current && controller.executionControl.execution?.state !== 'running',
    canStop: controller.executionControl.canStop,
    stopping: controller.executionControl.stopping,
    error: (controller.lastError || controller.confirmError || '').slice(0, 1200),
    stopMessage: (controller.executionControl.stopMessage || '').slice(0, 500),
  };
}

function controlSignature(controller: PilotConversationController) {
  const data = desktopHaruSnapshot(controller);
  return JSON.stringify([data.conversationId, data.aiConfigured, data.canSend, data.canStop, data.stopping, data.hasPending,
    controller.pending?.operation_id, pilotPageContextControlProjection(controller.followingContext), pilotPageContextControlProjection(controller.pinnedContext),
    pilotPageContextControlProjection(controller.requestContextSnapshot), controller.draftContext, controller.attachments,
    controller.executionControl.execution, controller.activeConversationSelectionRef.current]);
}

export function useDesktopHaruOwner(controller: PilotConversationController, openPending: () => void, companionLife?: CompanionMirror) {
  useLocale();
  const { mode } = useThemeMode();
  const bridge = window.offerpilotDesktop;
  const isOwner = bridge?.role === 'owner';
  const [animationLevel, setAnimationLevel] = useState(readPilotMascotAnimationLevel);
  useEffect(() => {
    if (!isOwner) return;
    const changed = (event: Event) => {
      const level: unknown = (event as CustomEvent).detail;
      if (level === 'full' || level === 'minimal' || level === 'off') setAnimationLevel(level);
    };
    const storageChanged = (event: StorageEvent) => { if (event.key === PILOT_MASCOT_ANIMATION_KEY || event.key === null) setAnimationLevel(readPilotMascotAnimationLevel()); };
    window.addEventListener(PILOT_MASCOT_ANIMATION_CHANGED, changed);
    window.addEventListener('storage', storageChanged);
    return () => { window.removeEventListener(PILOT_MASCOT_ANIMATION_CHANGED, changed); window.removeEventListener('storage', storageChanged); };
  }, [isOwner]);
  const authoredSendFailure = useRef<{ conversationId: number | undefined } | null>(null);
  if (authoredSendFailure.current && (controller.lastError !== DESKTOP_HARU_SEND_FAILURE
    || controller.conversationId !== authoredSendFailure.current.conversationId)) authoredSendFailure.current = null;
  const current = useRef({ controller, openPending });
  current.current = { controller, openPending };
  const data = isOwner ? { ...desktopHaruSnapshot(controller), theme: mode, animationLevel, companionLife,
    ...(authoredSendFailure.current ? { errorKey: 'desktop_send_failed' as const } : {}),
  } : null;
  const signature = JSON.stringify(data);
  const controls = isOwner ? controlSignature(controller) : "";
  const controlRef = useRef({ controls: '', request: controller.activeRequestRef.current });
  const changedControls = controlRef.current.controls !== controls || controlRef.current.request !== controller.activeRequestRef.current;
  const snapshotRef = useRef<{ signature: string; snapshot: DesktopHaruSnapshot | null }>({ signature: '', snapshot: null });
  if (data && (snapshotRef.current.signature !== signature || changedControls)) {
    snapshotRef.current = { signature, snapshot: { ...data, version: (snapshotRef.current.snapshot?.version ?? 0) + (changedControls ? 1 : 0) } };
  }
  controlRef.current = { controls, request: controller.activeRequestRef.current };
  const sending = useRef(false);
  const lastCommandId = useRef(0);
  const controlsAtCommand = (owner: PilotConversationController) => controlSignature(owner) !== controlRef.current.controls || owner.activeRequestRef.current !== controlRef.current.request;
  const publishedVersion = snapshotRef.current.snapshot?.version;

  useEffect(() => {
    if (!isOwner || !bridge) return;
    // Native focus is independent of the mirrored command lease. Opening Pilot
    // only selects the existing workspace; it never sends or approves a turn.
    const offShowPilot = bridge.onShowPilot?.(() => current.current.openPending());
    const off = bridge.onCommand?.((command: DesktopHaruCommand) => {
      const { controller: owner, openPending: showPending } = current.current;
      const snapshot = snapshotRef.current.snapshot;
      if (!Number.isSafeInteger(command.id) || command.id <= lastCommandId.current) return;
      lastCommandId.current = command.id;
      if (!snapshot || command.version !== snapshot.version || controlsAtCommand(owner)) {
        bridge.reply?.(command.id, { ok: false, reason: 'stale' });
        return;
      }
      if (command.action === 'open-pending') {
        showPending();
        void bridge.windowAction('show-main');
        bridge.reply?.(command.id, { ok: true });
      } else if (command.action === 'stop') {
        if (!owner.executionControl.canStop || owner.executionControl.stopping) {
          bridge.reply?.(command.id, { ok: false, reason: 'busy' });
          return;
        }
        owner.stopActiveRequest();
        bridge.reply?.(command.id, { ok: true });
      } else if (command.action === 'send' && typeof command.text === 'string' && command.text.trim() && command.text.length <= 16000) {
        if (sending.current || !desktopHaruSnapshot(owner).canSend) {
          bridge.reply?.(command.id, { ok: false, reason: 'busy' });
          return;
        }
        sending.current = true;
        // Ack ownership immediately, not when the stream completes. A lost ack
        // is ambiguous and must never trigger a second submission in the mirror.
        try {
          const text = command.text;
          const preserveOwnerDraft = Boolean(owner.composerDraft);
          // A separate Haru input must never replace an unfinished main draft.
          if (!preserveOwnerDraft) owner.setComposerDraft(value => value || text);
          const completion = owner.sendMessage(text);
          // ChatPanel acquires its request lease synchronously, before transport.
          // An ignored action has no lease and must not clear the mirror draft.
          const admitted = owner.activeRequestRef.current?.kind === 'chat';
          if (admitted) bridge.reply?.(command.id, { ok: true });
          void completion.then(outcome => {
            if (!admitted) {
              bridge.reply?.(command.id, outcome === 'sent' ? { ok: true } : { ok: false, reason: 'busy' });
              if (outcome === 'sent') void bridge.windowAction('show-main');
            }
            if (outcome === 'sent' && !preserveOwnerDraft) owner.setComposerDraft(value => value === text ? '' : value);
          }).catch(() => {
            if (!admitted) bridge.reply?.(command.id, { ok: false, reason: 'unavailable' });
            // Preserve a source key and explicit provenance so locale changes can
            // re-render this message without translating arbitrary error text.
            authoredSendFailure.current = { conversationId: owner.conversationId };
            owner.setLastError(DESKTOP_HARU_SEND_FAILURE);
          }).finally(() => { sending.current = false; });
        } catch {
          sending.current = false;
          bridge.reply?.(command.id, { ok: false, reason: 'unavailable' });
        }
      } else bridge.reply?.(command.id, { ok: false, reason: 'unavailable' });
    });
    return () => { offShowPilot?.(); off?.(); bridge.disconnect?.(); };
  }, [bridge, isOwner]);

  useEffect(() => {
    if (isOwner && snapshotRef.current.snapshot) bridge?.publish?.(snapshotRef.current.snapshot);
  }, [bridge, isOwner, signature, controls, publishedVersion]);
}
