import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type MutableRefObject,
} from 'react';
import type { ConfirmationInput } from '@/services/chat';
import type { ChatSubmission } from '@/services/chatSubmission';
import { settlePendingStartForExecution } from '@/services/chatSubmission';
import {
  streamChat as streamChatService,
  streamConfirmAction as streamConfirmActionService,
  getConversation,
  listConversations,
  type ChatContextInput,
  type ChatStreamRequestOptions,
} from '@/services/chat';
import type {
  ChatResponse,
  ChatStartRequest,
  ChatUndo,
  Conversation,
  PendingAction,
  PilotContextAttachment,
  PilotPageContext,
  PilotExecution,
} from '@/types/chat';
import type {
  ActiveConversationRequestOwner,
  UITurn,
} from '@/components/ChatPanel/model';
import { buildChatRequestContext, buildTurns, pendingActionForConversation, pendingAutoSelectReducer } from '@/components/ChatPanel/model';
import { pageContextKey } from '@/lib/pilotPageContext';
import { usePilotPresentation } from '@/features/actionPresentation/usePilotPresentation';
import type { AssistantTaskState } from './assistantSurfaceReducer';
import { sameExecution, usePilotExecution } from './usePilotExecution';
import { getRuntimeRequestExecution, observeRuntimeTurn, RuntimeEndedError, RuntimeSubscriptionError } from '@/services/pilotRuntime';

export type SendMessageOutcome = 'sent' | 'stopped' | 'failed' | 'ignored';

export interface ConfirmationExecution {
  conversationId: number;
  confirmationToken: string;
}

export interface ContextChangeNotice {
  currentConversationLabel: string;
  currentPageLabel: string;
  currentContext: PilotPageContext;
  followingContext: PilotPageContext;
}

export interface ActiveConversationRequest extends ActiveConversationRequestOwner {
  protocol?: 'pilot-runtime-v1';
  controller: AbortController;
  runId?: string;
  execution?: PilotExecution;
  requestId?: string;
  visibleGeneration?: number;
}

export interface PilotConversationActions {
  undoOperation?: (operationId: string) => Promise<void>;
  sendMessage: (text: string) => Promise<SendMessageOutcome>;
  selectConversation: (conversationId: number, options?: { refresh?: boolean }) => Promise<void>;
  startNewChat: () => boolean;
  retryLastMessage: () => void;
  clearLastFailure: () => void;
  handleConfirm: (input: ConfirmationInput) => Promise<void>;
  retryConfirmAction: () => void;
  refreshConfirmationStatus: () => Promise<void>;
  clearActiveContext: () => Promise<void>;
}

interface BuildRequestContextInput {
  conversationId?: number;
  draftContext: ChatStartRequest | null;
  offerApplicationId?: number;
  offerId?: number;
  pageContext?: PilotPageContext;
  attachments: PilotContextAttachment[];
}

type KnownRuntimeTerminalState = Exclude<PilotExecution['state'], 'running' | 'result_unknown'>;
const RUNTIME_TERMINAL_STATES = new Set<string>([
  'waiting_confirmation', 'completed', 'failed', 'interrupted', 'stopped',
]);
function terminalResponseState(response: ChatResponse, target: PilotExecution): KnownRuntimeTerminalState | null {
  if (target.protocol !== 'pilot-runtime-v1' || response.conversation_id !== target.conversation_id
    || response.turn_id !== target.turn_id || response.execution_generation !== target.execution_generation) return null;
  if (response.type === 'message') return 'completed';
  if (response.type === 'confirmation_required') return 'waiting_confirmation';
  const turn = response.turn;
  return turn.turn_id === target.turn_id && turn.conversation_id === target.conversation_id
    && turn.execution_generation === target.execution_generation && RUNTIME_TERMINAL_STATES.has(turn.state)
    ? turn.state as KnownRuntimeTerminalState : null;
}

const CHAT_ATTACHMENT_LIMIT = 5;

/**
 * Preserve a start request's exact entity binding while accepting the user's
 * other current attachments. A scoped Offer replaces any ambient Offer so a
 * negotiation can never silently drift to another Offer from the same
 * Application.
 */
export function mergePilotRequestAttachments(
  scoped: readonly PilotContextAttachment[] = [],
  ambient: readonly PilotContextAttachment[] = [],
): PilotContextAttachment[] {
  const scopedOfferId = scoped.find((item) => item.kind === 'offer')?.id;
  const merged = scopedOfferId
    ? [...scoped, ...ambient.filter((item) => item.kind !== 'offer')]
    : [...scoped, ...ambient];
  const seen = new Set<string>();
  const result: PilotContextAttachment[] = [];
  for (const item of merged) {
    const key = `${item.kind}:${item.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({ ...item });
    if (result.length === CHAT_ATTACHMENT_LIMIT) break;
  }
  return result;
}

function contextDisplayLabel(context: PilotPageContext): string {
  return context.entity?.label || context.label;
}

function conversationPageContext(conversation: Conversation | undefined): PilotPageContext | undefined {
  if (!conversation) return undefined;
  if (conversation.context_type === 'application' && conversation.context_ref) {
    const label = conversation.context_label || `投递 #${conversation.context_ref}`;
    return {
      view: 'applications-list',
      label,
      entity: {
        kind: 'application',
        id: conversation.context_ref,
        label,
      },
    };
  }
  return {
    view: 'dashboard',
    label: conversation.context_label || '工作台',
  };
}

const unavailableActions: PilotConversationActions = {
  sendMessage: async () => 'ignored',
  selectConversation: async () => undefined,
  startNewChat: () => false,
  retryLastMessage: () => undefined,
  clearLastFailure: () => undefined,
  handleConfirm: async () => undefined,
  retryConfirmAction: () => undefined,
  refreshConfirmationStatus: async () => undefined,
  clearActiveContext: async () => undefined,
};

export function usePilotConversationControllerState(observationEnabled = true) {
  const [turns, setTurns] = useState<UITurn[]>([]);
  const [recoveredProgress, setRecoveredProgress] = useState<{
    owner: object; target: PilotExecution; visibleGeneration: number; sequence: number; content: string;
    priorCanonicalMessages: ReadonlySet<string>;
  } | null>(null);
  const [recoveryRetry, setRecoveryRetry] = useState(0);
  const [conversationId, setConversationId] = useState<number>();
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [requestLoading, setLoading] = useState(false);
  const [autoApprove, setAutoApprove] = useState(false);
  const [hasKey, setHasKey] = useState(true);
  const [degraded, setDegraded] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [, dispatchPendingAutoSelect] = useReducer(pendingAutoSelectReducer, false);
  const [draftContext, setDraftContext] = useState<ChatStartRequest | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [lastFailedText, setLastFailedText] = useState('');
  const lastSubmissionRef = useRef<ChatSubmission | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [confirmPhase, setConfirmPhaseState] = useState<'idle' | 'saving' | 'success' | 'error'>('idle');
  const [lastUndo, setLastUndo] = useState<ChatUndo | null>(null);
  const [loadingLabel, setLoadingLabel] = useState<string>();
  const [hasStreamingAssistantContent, setHasStreamingAssistantContent] = useState(false);
  const [composerResetKey, setComposerResetKey] = useState(0);
  const [composerDraft, setComposerDraft] = useState('');
  const [followingContext, setFollowingContextState] = useState<PilotPageContext>();
  const [pinnedContext, setPinnedContext] = useState<PilotPageContext>();
  const [contextChangeNotice, setContextChangeNotice] = useState<ContextChangeNotice | null>(null);
  const [requestContextSnapshot, setRequestContextSnapshot] = useState<PilotPageContext>();
  const [attachments, setAttachments] = useState<PilotContextAttachment[]>([]);
  const { displayTurns: canonicalDisplayTurns, presentationSnapshot, refreshPresentation, acceptRuntimeSnapshot, presentationFailed, presentationRefreshing } = usePilotPresentation(conversationId, turns, pending, requestLoading, confirmPhase === 'error');
  const renderedBoundaryRef = useRef({ snapshot: presentationSnapshot, failed: presentationFailed });
  renderedBoundaryRef.current = { snapshot: presentationSnapshot, failed: presentationFailed };

  const activeRequestRef = useRef<ActiveConversationRequest | null>(null);
  const streamingAssistantActiveRef = useRef(false);
  const titleRefreshTimeoutsRef = useRef<number[]>([]);
  const lastConfirmationInputRef = useRef<ConfirmationInput | null>(null);
  const activePendingRef = useRef<PendingAction | null>(null);
  const activeConversationIdRef = useRef<number>();
  const confirmationMonitorRef = useRef(0);
  const confirmationLocksRef = useRef(new Map<number, ConfirmationExecution>());
  const confirmationReconcileOnOpenRef = useRef<ConfirmationExecution | null>(null);
  const lockedConfirmationRef = useRef<ConfirmationExecution | null>(null);
  const acceptedStartRequestKeyRef = useRef<number | null>(null);
  const startedRequestKeyRef = useRef<number | null>(null);
  const pendingAutoSelectSuppressedRef = useRef(false);
  const conversationSelectionRequestRef = useRef(0);
  const activeConversationSelectionRef = useRef<number | null>(null);
  const conversationListRequestRef = useRef(0);
  const visibleRequestGenerationRef = useRef(0);
  const showArchivedRef = useRef(showArchived);
  const consumedOnboardingFocusTokenRef = useRef(0);
  const consumedConversationRequestRef = useRef(0);
  const openRef = useRef(false);
  const actionsRef = useRef<PilotConversationActions>(unavailableActions);
  const actionsOwnerRef = useRef<object | null>(null);
  const pinnedContextByConversationRef = useRef(new Map<number, PilotPageContext>());
  const stopFeedbackRef = useRef<(() => void) | null>(null);
  const taskStateReporterRef = useRef<((taskState: AssistantTaskState, conversationId?: number) => void) | null>(null);
  const confirmPhaseRef = useRef(confirmPhase);
  const followingContextRef = useRef<PilotPageContext>();
  const pinnedContextRef = useRef<PilotPageContext>();
  const conversationsRef = useRef(conversations);
  const loadingRef = useRef(requestLoading);

  const stoppedExecution = useCallback((target: PilotExecution) => {
    const request = activeRequestRef.current;
    const endedNormally = ['completed', 'waiting_confirmation', 'failed', 'stopped', 'interrupted'].includes(target.state);
    // Persisted terminal state also releases a subscriber whose network stream
    // stalled. Recovery never relies on that stream delivering its final frame.
    const ownsRequest = Boolean(request?.execution && sameExecution(request.execution, target));
    const recoveredRequestId = ownsRequest ? request?.requestId : target.submission_request_id;
    settlePendingStartForExecution(target, ownsRequest ? request?.requestId : undefined);
    if (request && ownsRequest) {
      request.controller.abort();
      activeRequestRef.current = null;
      setLoading(activeConversationSelectionRef.current !== null);
      taskStateReporterRef.current?.('idle', target.conversation_id);
    }
    if (endedNormally && activeConversationIdRef.current === target.conversation_id && (!request || ownsRequest)) {
      const generation = visibleRequestGenerationRef.current;
      void Promise.all([getConversation(target.conversation_id), listConversations(true)]).then(([messages, summaries]) => {
        if (activeConversationIdRef.current !== target.conversation_id || activeRequestRef.current
          || visibleRequestGenerationRef.current !== generation || activeConversationSelectionRef.current !== null) return;
        setTurns(buildTurns(messages));
        const nextPending = pendingActionForConversation(summaries, target.conversation_id);
        setPending(nextPending);
        setLastUndo(summaries.find((item) => item.id === target.conversation_id)?.last_write_undo ?? null);
        // Clear only the failed submission whose durable result we just read.
        // Another task's completion must not hide a newer error or offer a
        // misleading resend after this request has already succeeded.
        const recoveredSubmission = lastSubmissionRef.current;
        if (['completed', 'waiting_confirmation'].includes(target.state)
          && recoveredRequestId && recoveredSubmission?.requestId === recoveredRequestId) {
          setLastError(null);
          setLastFailedText('');
          setComposerDraft((draft) => draft === recoveredSubmission.message ? '' : draft);
          lastSubmissionRef.current = null;
        }
        taskStateReporterRef.current?.(nextPending ? 'waiting_confirmation' : target.state === 'failed' ? 'failed' : 'completed', target.conversation_id);
      }).catch(() => {
        if (activeConversationIdRef.current === target.conversation_id && visibleRequestGenerationRef.current === generation
          && !activeRequestRef.current) setLastError('任务已结束，暂时无法读取记录，请重新打开对话。');
      });
    }
    void refreshPresentation();
  }, [refreshPresentation]);
  const executionControl = usePilotExecution(conversationId, stoppedExecution);
  // Subscriber ownership can transfer to GET recovery while execution keeps
  // running. Every visible consumer uses the same busy value, scoped to the
  // currently selected conversation. Pending keeps its distinct HITL state.
  const visibleExecutionRunning = conversationId !== undefined
    && executionControl.execution?.conversation_id === conversationId
    && executionControl.execution.state === 'running';
  const loading = requestLoading || (!pending && visibleExecutionRunning);
  const recoveredVisible = Boolean(recoveredProgress?.content && observationEnabled && !pending
    && presentationSnapshot?.conversation_id === conversationId && !presentationFailed
    // An uncertain original subscriber may leave a local prefix behind. Until
    // an authorized canonical snapshot replaces it, do not add a second reply.
    && !canonicalDisplayTurns.some(turn => turn.role === 'assistant' && turn.id?.startsWith('transient:'))
    && !requestLoading && !activeRequestRef.current && activeConversationSelectionRef.current === null
    && recoveredProgress.target.conversation_id === conversationId && visibleExecutionRunning
    && sameExecution(recoveredProgress.target, executionControl.execution!)
    && recoveredProgress.visibleGeneration === visibleRequestGenerationRef.current
    && !presentationSnapshot?.items.some(item => item.kind === 'assistant_message'
      && item.turn_id === recoveredProgress.target.turn_id && !recoveredProgress.priorCanonicalMessages.has(item.item_id)));
  const displayTurns = useMemo(() => recoveredVisible && recoveredProgress
    ? [...canonicalDisplayTurns, { id: `transient:runtime:${recoveredProgress.target.turn_id}:${recoveredProgress.target.execution_generation}`,
      role: 'assistant' as const, content: recoveredProgress.content }]
    : canonicalDisplayTurns, [canonicalDisplayTurns, recoveredVisible, recoveredProgress]);
  const settleRuntimeResponse = useCallback((response: ChatResponse, target: PilotExecution): PilotExecution | null => {
    const state = terminalResponseState(response, target);
    return state && executionControl.settleExecution(target, state) ? { ...target, state } : null;
  }, [executionControl.settleExecution]);
  const settleRuntimeError = useCallback((error: unknown, target: PilotExecution): PilotExecution | null => {
    if (!(error instanceof RuntimeEndedError) || error.target.conversation_id !== target.conversation_id
      || error.target.turn_id !== target.turn_id || error.target.execution_generation !== target.execution_generation
      || !RUNTIME_TERMINAL_STATES.has(error.state)) return null;
    const state = error.state as KnownRuntimeTerminalState;
    return executionControl.settleExecution(target, state) ? { ...target, state } : null;
  }, [executionControl.settleExecution]);
  const observedExecutionRef = useRef(executionControl.execution);
  observedExecutionRef.current = executionControl.execution;

  showArchivedRef.current = showArchived;
  activeConversationIdRef.current = conversationId;
  activePendingRef.current = pending;
  followingContextRef.current = followingContext;
  pinnedContextRef.current = pinnedContext;
  conversationsRef.current = conversations;
  loadingRef.current = loading;

  const setConfirmPhase = useCallback((phase: 'idle' | 'saving' | 'success' | 'error') => {
    confirmPhaseRef.current = phase;
    setConfirmPhaseState(phase);
  }, []);

  const isActionOwnerReady = useCallback(() => actionsOwnerRef.current !== null, []);

  const bindActions = useCallback((owner: object, actions: PilotConversationActions) => {
    actionsOwnerRef.current = owner;
    actionsRef.current = actions;
  }, []);

  const releaseActions = useCallback((owner: object) => {
    if (actionsOwnerRef.current !== owner) return;
    actionsOwnerRef.current = null;
    actionsRef.current = unavailableActions;
  }, []);

  const updateContextChangeNotice = useCallback((
    currentContext: PilotPageContext | undefined,
    nextContext: PilotPageContext | undefined,
  ) => {
    if (
      !currentContext
      || !nextContext
      || pageContextKey(currentContext) === pageContextKey(nextContext)
    ) {
      setContextChangeNotice(null);
      return;
    }
    setContextChangeNotice({
      currentConversationLabel: contextDisplayLabel(currentContext),
      currentPageLabel: contextDisplayLabel(nextContext),
      currentContext,
      followingContext: nextContext,
    });
  }, []);

  const setFollowingContext = useCallback((next: PilotPageContext | undefined) => {
    followingContextRef.current = next;
    setFollowingContextState((current) => (
      JSON.stringify(current) === JSON.stringify(next) ? current : next
    ));
    updateContextChangeNotice(pinnedContextRef.current, next);
  }, [updateContextChangeNotice]);

  const pinConversationContext = useCallback((id: number, context?: PilotPageContext) => {
    if (context) pinnedContextByConversationRef.current.set(id, context);
    else pinnedContextByConversationRef.current.delete(id);
    pinnedContextRef.current = context;
    setPinnedContext(context);
    updateContextChangeNotice(context, followingContextRef.current);
  }, [updateContextChangeNotice]);

  const activateConversationContext = useCallback((id?: number) => {
    const context = id === undefined
      ? undefined
      : pinnedContextByConversationRef.current.get(id)
        ?? conversationPageContext(conversationsRef.current.find((conversation) => conversation.id === id));
    if (id !== undefined && context) pinnedContextByConversationRef.current.set(id, context);
    pinnedContextRef.current = context;
    setPinnedContext(context);
    updateContextChangeNotice(context, followingContextRef.current);
  }, [updateContextChangeNotice]);

  const switchToFollowingContext = useCallback(() => {
    if (loadingRef.current || activeRequestRef.current || activePendingRef.current) return false;
    const next = followingContextRef.current;
    if (!next) {
      setContextChangeNotice(null);
      return true;
    }
    const id = activeConversationIdRef.current;
    if (id !== undefined) {
      pinnedContextByConversationRef.current.set(id, next);
      pinnedContextRef.current = next;
      setPinnedContext(next);
    }
    setContextChangeNotice(null);
    return true;
  }, []);

  const dismissContextChangeNotice = useCallback(() => {
    setContextChangeNotice(null);
  }, []);

  const bindStopFeedback = useCallback((callback: (() => void) | null) => {
    stopFeedbackRef.current = callback;
  }, []);

  const bindTaskStateReporter = useCallback((callback: ((taskState: AssistantTaskState, conversationId?: number) => void) | null) => {
    taskStateReporterRef.current = callback;
  }, []);

  const beginActiveRequest = useCallback((
    kind: ActiveConversationRequest['kind'],
    conversationId?: number,
    confirmationToken?: string,
  ): ActiveConversationRequest | null => {
    if (activeRequestRef.current || activeConversationSelectionRef.current !== null) return null;
    const request: ActiveConversationRequest = {
      controller: new AbortController(),
      kind,
      conversationId,
      ...(confirmationToken ? { confirmationToken } : {}),
    };
    activeRequestRef.current = request;
    setLoading(true);
    taskStateReporterRef.current?.('running', conversationId);
    return request;
  }, []);

  const finishActiveRequest = useCallback((request: ActiveConversationRequest): boolean => {
    if (activeRequestRef.current !== request) return false;
    activeRequestRef.current = null;
    setLoading(activeConversationSelectionRef.current !== null);
    const conversation = request.execution?.conversation_id ?? request.conversationId;
    const generation = visibleRequestGenerationRef.current;
    if (conversation !== undefined && activeConversationIdRef.current === conversation
      && request.visibleGeneration !== undefined && request.visibleGeneration !== generation) {
      // Returning to a running conversation invalidates the old stream callbacks.
      // Recover persisted content independently of the optional timeline endpoint.
      void Promise.all([getConversation(conversation), listConversations(true)]).then(([messages, summaries]) => {
        if (activeConversationIdRef.current !== conversation || visibleRequestGenerationRef.current !== generation
          || activeRequestRef.current || activeConversationSelectionRef.current !== null) return;
        setTurns(buildTurns(messages));
        setPending(pendingActionForConversation(summaries, conversation));
        setLastUndo(summaries.find((item) => item.id === conversation)?.last_write_undo ?? null);
      }).catch(() => {
        if (activeConversationIdRef.current === conversation && visibleRequestGenerationRef.current === generation
          && !activeRequestRef.current) setLastError('回复状态暂时无法读取，请重新打开对话。');
      });
    }
    if (request.kind === 'confirmation' || request.kind === 'undo') {
      if (confirmPhaseRef.current === 'success') {
        taskStateReporterRef.current?.('completed', request.conversationId);
      } else if (confirmPhaseRef.current === 'error') {
        taskStateReporterRef.current?.('failed', request.conversationId);
      }
    }
    taskStateReporterRef.current?.('idle', request.conversationId);
    return true;
  }, []);

  const beginConversationSelection = useCallback(() => {
    const requestId = ++conversationSelectionRequestRef.current;
    activeConversationSelectionRef.current = requestId;
    setLoading(true);
    return requestId;
  }, []);

  const finishConversationSelection = useCallback((requestId: number): boolean => {
    if (activeConversationSelectionRef.current !== requestId) return false;
    activeConversationSelectionRef.current = null;
    setLoading(activeRequestRef.current !== null);
    return true;
  }, []);

  const cancelConversationSelection = useCallback(() => {
    conversationSelectionRequestRef.current += 1;
    activeConversationSelectionRef.current = null;
    setLoading(activeRequestRef.current !== null);
  }, []);

  const ensureOwnedRequest = useCallback((request: ActiveConversationRequest) => {
    if (activeRequestRef.current === request) return;
    const error = new Error('Request ownership changed');
    error.name = 'AbortError';
    throw error;
  }, []);

  const buildRequestContext = useCallback((input: BuildRequestContextInput): ChatContextInput => {
    const base = input.conversationId === undefined && input.draftContext
      ? {
          context_type: input.draftContext.context_type,
          context_ref: input.draftContext.context_ref,
          mode: input.draftContext.mode,
          ...(input.draftContext.pilot_action
            ? { pilot_action: input.draftContext.pilot_action }
            : {}),
          ...(input.pageContext ? { page_context: input.pageContext } : {}),
        }
      : buildChatRequestContext({
          conversationId: input.conversationId,
          offerApplicationId: input.offerApplicationId,
          offerId: input.offerId,
          pageContext: input.pageContext,
        });
    const requestAttachments = mergePilotRequestAttachments(
      input.draftContext?.attachments,
      input.attachments,
    );
    return {
      ...base,
      ...(requestAttachments.length ? { attachments: requestAttachments } : {}),
    };
  }, []);

  const streamChatRequest = useCallback((
    request: ActiveConversationRequest,
    message: string,
    activeConversationId: number | undefined,
    context: ChatContextInput,
    options: Omit<ChatStreamRequestOptions, 'signal'>,
  ) => {
    ensureOwnedRequest(request);
    request.visibleGeneration = visibleRequestGenerationRef.current;
    request.protocol = 'pilot-runtime-v1';
    request.requestId = options.requestId ?? crypto.randomUUID();
    return streamChatService(message, activeConversationId, context, {
      ...options, requestId: request.requestId,
      onSnapshot: (page) => acceptRuntimeSnapshot(page, () => activeRequestRef.current === request
        && !request.controller.signal.aborted && request.visibleGeneration === visibleRequestGenerationRef.current).then(() => undefined),
      onAccepted: (identity) => {
        if (activeRequestRef.current !== request || request.controller.signal.aborted) return;
        if (identity.executionGeneration) {
          request.execution = { turn_id: identity.turnId, conversation_id: identity.conversationId,
            execution_generation: identity.executionGeneration, state: 'running', protocol: identity.protocol };
          executionControl.acceptExecution(request.execution);
        }
        options.onAccepted?.(identity);
      },
      signal: request.controller.signal,
    }).then(response => {
      if (activeRequestRef.current === request && !request.controller.signal.aborted && request.execution)
        settleRuntimeResponse(response, request.execution);
      return response;
    }).catch((error: unknown) => {
      if (activeRequestRef.current === request && !request.controller.signal.aborted && request.execution)
        settleRuntimeError(error, request.execution);
      throw error;
    });
  }, [ensureOwnedRequest, executionControl.acceptExecution, acceptRuntimeSnapshot, settleRuntimeResponse, settleRuntimeError]);

  const streamConfirmationRequest = useCallback((
    request: ActiveConversationRequest,
    activeConversationId: number,
    input: ConfirmationInput,
    options: Omit<ChatStreamRequestOptions, 'signal'>,
  ) => {
    ensureOwnedRequest(request);
    request.visibleGeneration = visibleRequestGenerationRef.current;
    request.protocol = 'pilot-runtime-v1';
    request.requestId = options.requestId ?? crypto.randomUUID();
    return streamConfirmActionService(activeConversationId, input, {
      ...options, requestId: request.requestId,
      onSnapshot: (page) => acceptRuntimeSnapshot(page, () => activeRequestRef.current === request
        && !request.controller.signal.aborted && request.visibleGeneration === visibleRequestGenerationRef.current).then(() => undefined),
      onAccepted: (identity) => {
        if (activeRequestRef.current !== request || request.controller.signal.aborted) return;
        if (identity.executionGeneration) {
          request.execution = { turn_id: identity.turnId, conversation_id: identity.conversationId,
            execution_generation: identity.executionGeneration, state: 'running', protocol: identity.protocol };
          executionControl.acceptExecution(request.execution);
        }
        options.onAccepted?.(identity);
      },
      onEvent: (event) => {
        if (activeRequestRef.current === request && event.turn_id && event.execution_generation
          && event.conversation_id === activeConversationId) {
          request.execution = { turn_id: event.turn_id, conversation_id: activeConversationId,
            execution_generation: event.execution_generation, state: 'running', protocol: request.execution?.protocol };
          executionControl.acceptExecution(request.execution);
        }
        options.onEvent?.(event);
      },
      signal: request.controller.signal,
    }).then(response => {
      if (activeRequestRef.current === request && !request.controller.signal.aborted && request.execution)
        settleRuntimeResponse(response, request.execution);
      return response;
    }).catch((error: unknown) => {
      if (activeRequestRef.current === request && !request.controller.signal.aborted && request.execution)
        settleRuntimeError(error, request.execution);
      throw error;
    });
  }, [ensureOwnedRequest, executionControl.acceptExecution, acceptRuntimeSnapshot, settleRuntimeResponse, settleRuntimeError]);

  const stopActiveRequest = useCallback((options: { silent?: boolean } = {}) => {
    if (!options.silent) {
      void executionControl.stop();
      return executionControl.canStop;
    }
    const activeRequest = activeRequestRef.current;
    if (!activeRequest) return false;
    activeRequest.controller.abort();
    activeRequestRef.current = null;
    setLoading(activeConversationSelectionRef.current !== null);
    taskStateReporterRef.current?.('idle');
    return true;
  }, [executionControl.stop, executionControl.canStop]);

  useEffect(() => {
    if (!observationEnabled && (activeRequestRef.current?.protocol === 'pilot-runtime-v1'
      || activeRequestRef.current?.execution?.protocol === 'pilot-runtime-v1')) {
      stopActiveRequest({ silent: true });
    }
  }, [observationEnabled, stopActiveRequest]);

  const recoveryExecution = executionControl.execution;
  useEffect(() => {
    const target = executionControl.execution;
    if (!observationEnabled || !target || target.protocol !== 'pilot-runtime-v1' || target.state !== 'running'
      || activeRequestRef.current || target.conversation_id !== conversationId) return;
    const subscription = new AbortController();
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const owner = {};
    const visibleGeneration = visibleRequestGenerationRef.current;
    let sequence = 0;
    let snapshotBaseline: ReadonlySet<string> | null = null;
    const current = () => !subscription.signal.aborted && !activeRequestRef.current
      && activeConversationSelectionRef.current === null && activeConversationIdRef.current === target.conversation_id
      && visibleRequestGenerationRef.current === visibleGeneration
      && sameExecution(observedExecutionRef.current, target) && observedExecutionRef.current?.state === 'running';
    const clear = () => setRecoveredProgress(value => value?.owner === owner ? null : value);
    void observeRuntimeTurn(target, {
      signal: subscription.signal,
      onSnapshot: async (page) => {
        const snapshot = await acceptRuntimeSnapshot(page, current);
        if (!current()) return;
        const timeline = snapshot?.conversation_id === target.conversation_id ? snapshot.timeline : null;
        // Hydration also refreshes P2 after the runtime's frozen watermark.
        // Those newer canonical messages must supersede buffered SSE, never
        // become part of the segment's earlier baseline.
        snapshotBaseline = timeline ? new Set(Object.values(timeline.items)
          .filter(item => !item.deleted && item.payload?.kind === 'assistant_message'
            && item.turn_id === target.turn_id && item.change_seq <= page.high_watermark)
          .map(item => item.payload!.item_id)) : null;
      },
      onAssistantProgress: (progress) => {
        if (!current() || progress.conversation_id !== target.conversation_id || progress.turn_id !== target.turn_id
          || progress.execution_generation !== target.execution_generation || !Number.isSafeInteger(progress.sequence)
          || progress.sequence < sequence || typeof progress.content !== 'string' || progress.content.length > 1_048_576) return;
        sequence = progress.sequence;
        if (!progress.content) { clear(); return; }
        const rendered = renderedBoundaryRef.current;
        const renderedBaseline = !rendered.failed && rendered.snapshot?.conversation_id === target.conversation_id
          ? rendered.snapshot.items.filter(item => item.kind === 'assistant_message' && item.turn_id === target.turn_id).map(item => item.item_id) : null;
        // An independent P2 read can establish an empty boundary after a failed
        // initial read. If it already has same-turn prose, its generation is
        // ambiguous: keep canonical text rather than guessing a replay prefix.
        // A direct boundary still preserves prior HITL-generation messages and
        // covers same-batch hydration before React renders the snapshot.
        if (!snapshotBaseline && (!renderedBaseline || renderedBaseline.length > 0)) return;
        // A later rendered P2 message may have overtaken buffered deltas. Do
        // not expand an established boundary to authorize its replay again.
        const priorCanonicalMessages = snapshotBaseline ?? new Set(renderedBaseline!);
        setRecoveredProgress(value => value?.owner === owner && value.sequence === progress.sequence && value.content === progress.content
          ? value : { owner, target, visibleGeneration, sequence: progress.sequence, content: progress.content,
            priorCanonicalMessages: value?.owner === owner ? value.priorCanonicalMessages : priorCanonicalMessages });
      },
      onEvent: (event) => {
        if (!current()) return;
        if (event.event !== 'assistant_delta') void refreshPresentation();
      },
    }).then(response => {
      if (subscription.signal.aborted || activeRequestRef.current) return;
      const terminal = settleRuntimeResponse(response, target);
      if (terminal) stoppedExecution(terminal);
    }).catch((error: unknown) => {
      if (subscription.signal.aborted || activeRequestRef.current) return;
      const terminal = settleRuntimeError(error, target);
      if (terminal) stoppedExecution(terminal);
      // A network failure or result_unknown is not completion proof. Preserve
      // durable busy for exact-identity polling, never start another POST.
      // A spent GET retry budget must not leave this same-identity lease dead
      // forever. Retry transport failures only; terminal/auth failures retain
      // their existing recovery behavior and never enter an automatic loop.
      if (error instanceof RuntimeSubscriptionError && current()) {
        retryTimer = setTimeout(() => {
          if (current()) setRecoveryRetry(value => value + 1);
        }, 2000);
      }
    });
    return () => { subscription.abort(); clearTimeout(retryTimer); clear(); };
    // Same-identity polling returns fresh objects. It must not repeatedly abort
    // this GET lease and replay its deltas into a replacement projection.
  }, [observationEnabled, conversationId, recoveryExecution?.conversation_id, recoveryExecution?.turn_id,
    recoveryExecution?.execution_generation, recoveryExecution?.state, recoveryExecution?.protocol,
    requestLoading, recoveryRetry, refreshPresentation, acceptRuntimeSnapshot, stoppedExecution, settleRuntimeResponse, settleRuntimeError]);

  useEffect(() => {
    if (!observationEnabled || !requestLoading) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const lookup = new AbortController();
    const read = async () => {
      const request = activeRequestRef.current;
      if (!request || request.execution || request.protocol !== 'pilot-runtime-v1' || !request.requestId) return;
      try {
        const target = await getRuntimeRequestExecution(request.requestId, lookup.signal);
        if (cancelled || !target || activeRequestRef.current !== request || request.execution) return;
        if (request.conversationId !== undefined && request.conversationId !== target.conversation_id) return;
        request.execution = target;
        executionControl.acceptExecution(target);
        request.controller.abort();
        activeRequestRef.current = null;
        setLoading(activeConversationSelectionRef.current !== null);
        if (request.visibleGeneration === visibleRequestGenerationRef.current && activeConversationIdRef.current === undefined) {
          activeConversationIdRef.current = target.conversation_id;
          setConversationId(target.conversation_id);
        }
        if (target.state !== 'running') stoppedExecution(target);
      } catch { /* Missing proof never permits another POST or guessing a turn. */ }
      finally { if (!cancelled && activeRequestRef.current === request) timer = setTimeout(() => void read(), 2000); }
    };
    timer = setTimeout(() => void read(), 2000);
    return () => { cancelled = true; clearTimeout(timer); lookup.abort(); };
  }, [observationEnabled, requestLoading, executionControl.acceptExecution, stoppedExecution]);

  const sendMessage = useCallback((text: string) => executionControl.execution?.state === 'running'
    ? Promise.resolve('ignored' as const) : actionsRef.current.sendMessage(text), [executionControl.execution]);
  const selectConversation = useCallback(
    (id: number, options?: { refresh?: boolean }) => actionsRef.current.selectConversation(id, options),
    [],
  );
  const startNewChat = useCallback(() => actionsRef.current.startNewChat(), []);
  const retryLastMessage = useCallback(() => actionsRef.current.retryLastMessage(), []);
  const clearLastFailure = useCallback(() => actionsRef.current.clearLastFailure(), []);
  const retryConfirmAction = useCallback(() => actionsRef.current.retryConfirmAction(), []);
  const refreshConfirmationStatus = useCallback(
    () => actionsRef.current.refreshConfirmationStatus(),
    [],
  );
  const clearActiveContext = useCallback(() => actionsRef.current.clearActiveContext(), []);
  const undoOperation = useCallback(async (operationId: string) => {
    await actionsRef.current.undoOperation?.(operationId);
  }, []);

  const approvePending = useCallback(
    (editedArgs?: Record<string, unknown>) => {
      const action = activePendingRef.current;
      if (!action) return Promise.resolve();
      return actionsRef.current.handleConfirm({
        approved: true,
        operation_id: action.operation_id,
        confirmation_token: action.confirmation_token,
        ...(editedArgs ? { edited_args: editedArgs } : {}),
      });
    },
    [],
  );

  const rejectPending = useCallback((rejectionFeedback?: string) => {
    const action = activePendingRef.current;
    if (!action) return Promise.resolve();
    return actionsRef.current.handleConfirm({
      approved: false,
      operation_id: action.operation_id,
      confirmation_token: action.confirmation_token,
      ...(rejectionFeedback ? { rejection_feedback: rejectionFeedback } : {}),
    });
  }, []);

  const taskState: AssistantTaskState = pending
    ? 'waiting_confirmation'
    : loading
      ? 'running'
      : lastError || confirmError
        ? 'failed'
        : 'idle';

  const backgroundExecution = activeRequestRef.current?.execution?.conversation_id !== conversationId
    ? activeRequestRef.current?.execution ?? null : null;

  return useMemo(() => ({
    undoOperation,
    displayTurns,
    presentationSnapshot,
    refreshPresentation,
    presentationFailed,
    presentationRefreshing,
    turns,
    setTurns,
    conversationId,
    setConversationId,
    pending,
    setPending,
    loading,
    setLoading,
    autoApprove,
    setAutoApprove,
    hasKey,
    setHasKey,
    degraded,
    setDegraded,
    conversations,
    setConversations,
    showArchived,
    setShowArchived,
    dispatchPendingAutoSelect,
    draftContext,
    setDraftContext,
    lastError,
    setLastError,
    lastFailedText,
    setLastFailedText,
    lastSubmissionRef,
    confirmError,
    setConfirmError,
    confirmPhase,
    setConfirmPhase,
    lastUndo,
    setLastUndo,
    loadingLabel,
    setLoadingLabel,
    hasStreamingAssistantContent: hasStreamingAssistantContent || recoveredVisible,
    setHasStreamingAssistantContent,
    composerResetKey,
    setComposerResetKey,
    composerDraft,
    setComposerDraft,
    followingContext,
    setFollowingContext,
    pinnedContext,
    pinConversationContext,
    activateConversationContext,
    contextChangeNotice,
    switchToFollowingContext,
    dismissContextChangeNotice,
    requestContextSnapshot,
    setRequestContextSnapshot,
    attachments,
    setAttachments,
    taskState,
    activeRequestRef,
    streamingAssistantActiveRef,
    titleRefreshTimeoutsRef,
    lastConfirmationInputRef,
    activePendingRef,
    activeConversationIdRef,
    confirmationMonitorRef,
    confirmationLocksRef,
    confirmationReconcileOnOpenRef,
    lockedConfirmationRef,
    acceptedStartRequestKeyRef,
    startedRequestKeyRef,
    pendingAutoSelectSuppressedRef,
    conversationSelectionRequestRef,
    activeConversationSelectionRef,
    conversationListRequestRef,
    visibleRequestGenerationRef,
    showArchivedRef,
    consumedOnboardingFocusTokenRef,
    consumedConversationRequestRef,
    openRef,
    isActionOwnerReady,
    bindActions,
    releaseActions,
    bindStopFeedback,
    bindTaskStateReporter,
    beginActiveRequest,
    beginConversationSelection,
    finishConversationSelection,
    cancelConversationSelection,
    buildRequestContext,
    finishActiveRequest,
    streamChatRequest,
    streamConfirmationRequest,
    stopActiveRequest,
    executionControl,
    backgroundExecution,
    sendMessage,
    selectConversation,
    startNewChat,
    retryLastMessage,
    clearLastFailure,
    retryConfirmAction,
    refreshConfirmationStatus,
    clearActiveContext,
    approvePending,
    rejectPending,
  }), [
    undoOperation,
    displayTurns,
    recoveredVisible,
    presentationSnapshot,
    refreshPresentation,
    presentationFailed,
    presentationRefreshing,
    autoApprove,
    activateConversationContext,
    isActionOwnerReady,
    bindActions,
    bindStopFeedback,
    bindTaskStateReporter,
    beginActiveRequest,
    beginConversationSelection,
    buildRequestContext,
    cancelConversationSelection,
    clearActiveContext,
    clearLastFailure,
    composerResetKey,
    composerDraft,
    confirmError,
    confirmPhase,
    contextChangeNotice,
    conversationId,
    conversations,
    degraded,
    draftContext,
    followingContext,
    finishActiveRequest,
    finishConversationSelection,
    hasKey,
    hasStreamingAssistantContent,
    lastError,
    lastFailedText,
    lastUndo,
    loading,
    loadingLabel,
    pending,
    pinnedContext,
    pinConversationContext,
    requestContextSnapshot,
    refreshConfirmationStatus,
    rejectPending,
    releaseActions,
    retryConfirmAction,
    retryLastMessage,
    selectConversation,
    sendMessage,
    showArchived,
    setFollowingContext,
    switchToFollowingContext,
    dismissContextChangeNotice,
    startNewChat,
    stopActiveRequest,
    executionControl,
    backgroundExecution,
    streamChatRequest,
    streamConfirmationRequest,
    taskState,
    turns,
    approvePending,
    attachments,
  ]);
}

export type PilotConversationController = ReturnType<typeof usePilotConversationControllerState>;

export type ControllerMutableRef<T> = MutableRefObject<T>;
