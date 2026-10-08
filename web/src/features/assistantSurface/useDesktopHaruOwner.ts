import { useEffect, useRef } from 'react';
import type { PilotConversationController } from './usePilotConversationController';
import type { DesktopHaruCommand, DesktopHaruSnapshot } from './desktopHaru';
import { compactMessageText } from './assistantPresentation';

export function desktopHaruSnapshot(controller: PilotConversationController): Omit<DesktopHaruSnapshot, 'version'> {
  const current = controller.conversations.find(item => item.id === controller.conversationId);
  const context = controller.requestContextSnapshot ?? controller.pinnedContext ?? controller.followingContext;
  // Match the web Haru label order; a draft only describes a new conversation.
  const contextLabel =
    controller.requestContextSnapshot?.entity?.label ||
    controller.requestContextSnapshot?.label ||
    (controller.conversationId === undefined ? controller.draftContext?.context_label : undefined) ||
    current?.context_label ||
    (current?.context_type === 'application' && current.context_ref ? `投递 #${current.context_ref}` : undefined) ||
    context?.entity?.label || context?.label ||
    (controller.conversationId ? '工作台' : '当前页面');
  return {
    conversationId: controller.conversationId ?? null,
    taskState: controller.taskState,
    // Only visible prose crosses this boundary. In particular, do not serialize
    // action cards, tool arguments, active request refs or pending objects.
    messages: (controller.displayTurns ?? controller.turns).slice(-12).map(turn => ({ role: turn.role, content: compactMessageText(turn).slice(0, 12000) })),
    contextLabel: contextLabel.slice(0, 400),
    loading: controller.loading,
    hasPending: Boolean(controller.pending),
    canSend: controller.isActionOwnerReady() && controller.hasKey && !controller.loading && !controller.pending && !controller.activeRequestRef.current && controller.activeConversationSelectionRef.current === null && !controller.activePendingRef.current && controller.executionControl.execution?.state !== 'running',
    canStop: controller.executionControl.canStop,
    stopping: controller.executionControl.stopping,
    error: (controller.lastError || controller.confirmError || '').slice(0, 1200),
    stopMessage: (controller.executionControl.stopMessage || '').slice(0, 500),
  };
}

function controlSignature(controller: PilotConversationController) {
  const data = desktopHaruSnapshot(controller);
  return JSON.stringify([data.conversationId, data.canSend, data.canStop, data.stopping, data.hasPending,
    controller.pending?.operation_id, controller.followingContext, controller.pinnedContext,
    controller.requestContextSnapshot, controller.draftContext, controller.attachments,
    controller.executionControl.execution, controller.activeConversationSelectionRef.current]);
}

export function useDesktopHaruOwner(controller: PilotConversationController, openPending: () => void) {
  const bridge = window.offerpilotDesktop;
  const isOwner = bridge?.role === 'owner';
  const current = useRef({ controller, openPending });
  current.current = { controller, openPending };
  const data = isOwner ? desktopHaruSnapshot(controller) : null;
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
            owner.setLastError('发送失败，请在 Pilot 中核对对话后重试。');
          }).finally(() => { sending.current = false; });
        } catch {
          sending.current = false;
          bridge.reply?.(command.id, { ok: false, reason: 'unavailable' });
        }
      } else bridge.reply?.(command.id, { ok: false, reason: 'unavailable' });
    });
    return () => { off?.(); bridge.disconnect?.(); };
  }, [bridge, isOwner]);

  useEffect(() => {
    if (isOwner && snapshotRef.current.snapshot) bridge?.publish?.(snapshotRef.current.snapshot);
  }, [bridge, isOwner, signature, controls, publishedVersion]);
}
