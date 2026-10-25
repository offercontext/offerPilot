import { t, useLocale, formatNumber } from '@/i18n';
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type MutableRefObject,
} from 'react';
import {
  ArrowUpOutlined,
  BellOutlined,
  CloseOutlined,
  ExpandAltOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { Modal } from 'antd';
import ProactiveInbox from '@/components/ProactiveInbox';
import { displayPilotUiMessage } from '@/components/ChatPanel/model';
import { useAssistantSurface, usePilotConversationController } from './AssistantSurfaceProvider';
import { contextDisplayLabel } from './usePilotConversationController';
import { recentConversationTurns } from './assistantPresentation';
import CompactMessageRenderer from './CompactMessageRenderer';
import { ActionCard } from '@/features/actionPresentation/ActionCard';
import { PresentationRecovery } from '@/features/actionPresentation/PresentationRecovery';
import { PendingStartRecovery } from '@/features/actionPresentation/PendingStartRecovery';
import { agentActionCommands, pendingPresentationActions } from '@/features/actionPresentation/commands';
import { positionHaruWindow, type HaruRect } from './haruWindowPosition';
import styles from './AssistantSurface.module.css';
import HaruComposer from './HaruComposer';
import { chatImageScope, type ChatImageReference } from '@/services/chatImages';

const TASK_COPY = {
  idle: '随时可以开始',
  running: '正在处理',
  waiting_confirmation: '等待确认',
  completed: '已完成',
  failed: '处理失败',
} as const;

function measureDesktopViewport() {
  const width = typeof document === 'undefined'
    ? window.innerWidth
    : document.documentElement.clientWidth || window.innerWidth;
  const height = typeof document === 'undefined'
    ? window.innerHeight
    : document.documentElement.clientHeight || window.innerHeight;
  const navigation = typeof document === 'undefined'
    ? null
    : document.querySelector<HTMLElement>('.op-sidebar');
  const navigationRect = navigation?.getBoundingClientRect();
  const navigationRight = navigationRect
    && navigationRect.height > height / 2
    && navigationRect.right < width
      ? navigationRect.right + 12
      : 12;
  return { width, height, navigationRight };
}

interface Props {
  returnFocusRef: MutableRefObject<HTMLElement | null>;
  onExpand?: () => void;
  anchorRect?: HaruRect;
}

export default function HaruChatWindow({ returnFocusRef, onExpand, anchorRect }: Props) {
  useLocale();
  const surface = useAssistantSurface();
  const controller = usePilotConversationController();
  const draft = controller.composerDraft;
  const setDraft = controller.setComposerDraft;
  const [viewport, setViewport] = useState(() => (
    typeof window === 'undefined'
      ? { width: 1440, height: 900, navigationRight: 12 }
      : measureDesktopViewport()
  ));
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const closingRef = useRef(false);
  const [inboxOpen, setInboxOpen] = useState(false);
  const [imagePreviewKey, setImagePreviewKey] = useState<string>();
  const [imageError, setImageError] = useState<string>();
  const imageContext = controller.buildRequestContext({
    conversationId: controller.conversationId, draftContext: controller.draftContext,
    pageContext: controller.conversationId === undefined ? controller.followingContext : controller.pinnedContext,
    attachments: [],
  });
  const { scope: imageScope, scopeKey: imageScopeKey } = chatImageScope(controller.conversationId, imageContext);
  const imageDrafts = controller.imageDrafts.drafts.filter((item) => item.scopeKey === imageScopeKey);
  const imageDisabledReason = controller.imageCapability?.enabled ? undefined
    : controller.imageCapability?.reason ?? t("正在检查图片输入能力");
  const composerDisabled = !controller.hasKey || controller.loading || Boolean(controller.pending)
    || controller.executionControl.execution?.state === 'running';
  const addImages = (files: File[]) => {
    if (composerDisabled || imageDisabledReason) return;
    setImageError(controller.imageDrafts.add(files, imageScope, imageScopeKey));
  };
  const referenceImage = (image: ChatImageReference) => {
    if (composerDisabled || imageDisabledReason) return;
    setImageError(controller.imageDrafts.reference(image, imageScope, imageScopeKey));
  };
  useEffect(() => { setImageError(undefined); setImagePreviewKey(undefined); }, [imageScopeKey]);
  useEffect(() => {
    if (imagePreviewKey?.startsWith('draft:') && !imageDrafts.some((item) => `draft:${item.key}` === imagePreviewKey)) {
      setImagePreviewKey(undefined);
    }
  }, [imageDrafts, imagePreviewKey]);

  useEffect(() => {
    if (surface.surface !== 'haru_chat') { setInboxOpen(false); setImagePreviewKey(undefined); return; }
    closingRef.current = false;
    if (controller.hasKey && !controller.pending) inputRef.current?.focus();
    else dialogRef.current?.focus();
  }, [controller.hasKey, controller.pending, surface.surface]);

  useLayoutEffect(() => {
    const syncViewport = () => setViewport(measureDesktopViewport());
    syncViewport();
    window.addEventListener('resize', syncViewport);
    window.visualViewport?.addEventListener('resize', syncViewport);
    return () => {
      window.removeEventListener('resize', syncViewport);
      window.visualViewport?.removeEventListener('resize', syncViewport);
    };
  }, []);

  useEffect(() => {
    if (surface.surface !== 'haru_chat') return;
    if (typeof endRef.current?.scrollIntoView === 'function') {
      endRef.current.scrollIntoView({ block: 'nearest' });
    }
  }, [controller.turns, controller.pending, surface.surface]);

  if (surface.surface !== 'haru_chat') return null;
  const pendingNeedsRefresh = pendingPresentationActions(controller.presentationSnapshot, controller.pending?.operation_id)?.length === 0;

  const close = () => {
    if (closingRef.current) return;
    closingRef.current = true;
    surface.closeSurface();
    window.setTimeout(() => returnFocusRef.current?.focus(), 0);
  };
  const submit = async () => {
    if (composerDisabled || (!draft.trim() && !imageDrafts.length)
      || imageDrafts.some((item) => item.status !== 'ready') || (imageDrafts.length > 0 && imageDisabledReason)) return;
    // Admission owns clearing the submitted draft, across both surfaces.
    await controller.sendMessage(draft, { includeImageDrafts: true });
  };
  const activeConversation = controller.conversations.find(
    (conversation) => conversation.id === controller.conversationId,
  );
  const visiblePageContext = controller.requestContextSnapshot
    ?? controller.pinnedContext
    ?? controller.followingContext;
  const contextLabel =
    contextDisplayLabel(controller.requestContextSnapshot) ||
    (controller.conversationId === undefined ? controller.draftContext?.context_label : undefined) ||
    activeConversation?.context_label ||
    (activeConversation?.context_type === 'application' && activeConversation.context_ref
      ? t('投递 #{id}', { id: activeConversation.context_ref })
      : undefined) ||
    contextDisplayLabel(visiblePageContext) ||
    t(controller.conversationId ? '工作台' : '当前页面');
  const attachmentSuffix = controller.attachments.length > 0
    ? t(' · {count} 个附件', { count: formatNumber(controller.attachments.length) })
    : '';
  const anchorPrefersLeft = anchorRect
    ? (anchorRect.left + anchorRect.right) / 2 >= viewport.width / 2
    : true;
  const horizontalAnchorSpace = anchorRect
    ? anchorPrefersLeft
      ? anchorRect.left - 12 - viewport.navigationRight
      : viewport.width - 24 - anchorRect.right
    : viewport.width - viewport.navigationRight - 12;
  const surfaceSize = {
    width: Math.min(
      392,
      Math.max(0, viewport.width - viewport.navigationRight - 12),
      Math.max(280, horizontalAnchorSpace),
    ),
    height: Math.min(620, Math.max(0, viewport.height - 24)),
  };
  const fallbackGap = viewport.width < 768 ? 16 : 32;
  const windowPosition = anchorRect
    ? positionHaruWindow({
        anchor: anchorRect,
        viewport,
        surface: surfaceSize,
        bounds: { left: viewport.navigationRight },
      })
    : {
        left: Math.max(fallbackGap, viewport.width - surfaceSize.width - fallbackGap),
        top: Math.max(fallbackGap, viewport.height - surfaceSize.height - fallbackGap),
        direction: 'left-up' as const,
      };

  return (<>
    <section
      id="haru-chat-window"
      ref={dialogRef}
      className={styles.window}
      role="dialog"
      aria-modal="false"
      aria-label={t("Haru 轻量对话")}
      tabIndex={-1}
      data-expand-direction={windowPosition.direction}
      style={{ left: windowPosition.left, top: windowPosition.top, width: surfaceSize.width }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          if (imagePreviewKey) setImagePreviewKey(undefined);
          else close();
        }
      }}
    >
      <header className={styles.header}>
        <div>
          <strong>Haru</strong>
          <span
            role="status"
            aria-live="polite"
            aria-atomic="true"
            data-task-state={controller.taskState === 'idle' ? surface.taskState : controller.taskState}
          >
            {t(TASK_COPY[controller.taskState === 'idle' ? surface.taskState : controller.taskState])}
          </span>
        </div>
        <div className={styles.headerActions}>
          <button type="button" aria-label={t("查看主动提醒与草稿")} onClick={() => setInboxOpen(true)}>
            <BellOutlined />
          </button>
          <button
            type="button"
            aria-label={t("展开到 Pilot 工作区")}
            onClick={() => {
              surface.openPilot();
              onExpand?.();
            }}
          >
            <ExpandAltOutlined />
          </button>
          <button type="button" aria-label={t("关闭 Haru 对话")} onClick={close}>
            <CloseOutlined />
          </button>
        </div>
      </header>

      <div className={styles.contextStack}>
        <div className={styles.context} aria-label={t("当前上下文")}>
          <span>{t("当前上下文")}</span>
          <b title={`${contextLabel}${attachmentSuffix}`}>{contextLabel}{attachmentSuffix}</b>
        </div>

        {controller.contextChangeNotice ? (
          <div className={styles.contextChange} role="status" aria-label={t("页面上下文已变化")}>
            <div className={styles.contextRows}>
              <span>{t("当前会话")}</span>
              <b>{controller.contextChangeNotice.currentConversationLabel}</b>
              <span>{t("当前页面")}</span>
              <b>{controller.contextChangeNotice.currentPageLabel}</b>
            </div>
            <div className={styles.contextActions}>
              <button
                type="button"
                onClick={controller.switchToFollowingContext}
                disabled={controller.loading || Boolean(controller.pending)}
              >
                {t("切换到当前页面")}
              </button>
              <button type="button" onClick={controller.dismissContextChangeNotice}>
                {t("保持原上下文")}
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <div className={styles.messages} aria-live="polite" aria-relevant="additions text">
        {controller.turns.length === 0 ? (
          <div className={styles.empty}>
            <strong>{t("Haru 是 Pilot 的轻量窗口")}</strong>
            <span>
              {t("简单问题可以在这里完成；需要查看资料、编辑内容或确认操作时，会展开到 Pilot。对话不会丢失。")}
            </span>
          </div>
        ) : (
          recentConversationTurns(controller.displayTurns ?? controller.turns).map((turn, index) => turn.action ? (
            <ActionCard key={turn.id} action={turn.action} busy={controller.loading || controller.presentationRefreshing} commands={agentActionCommands(turn.action, controller)} />
          ) : (
            <CompactMessageRenderer key={turn.id ?? `transient:${controller.conversationId}:${index}`} turn={turn}
              onReferenceImage={!composerDisabled && !imageDisabledReason ? referenceImage : undefined}
              previewKeyPrefix={`message:${turn.id ?? index}`} activePreviewKey={imagePreviewKey} onPreviewChange={setImagePreviewKey} />
          ))
        )}
        <PresentationRecovery failed={controller.presentationFailed} busy={controller.loading || controller.presentationRefreshing} onRefresh={controller.refreshPresentation} />
        <PendingStartRecovery busy={controller.loading} conversationId={controller.conversationId} onOpen={controller.selectConversation} />
        {controller.backgroundExecution && (
          <button type="button" onClick={() => void controller.selectConversation(controller.backgroundExecution!.conversation_id, { refresh: true }).catch(() => controller.setLastError('加载对话失败，请重试。'))}>
            {t("返回正在执行的对话")}
          </button>
        )}
        {controller.loading && !controller.hasStreamingAssistantContent ? (
          <div className={styles.thinking} role="status">
            {displayPilotUiMessage(controller.loadingLabel || '正在理解你的问题')}
          </div>
        ) : null}
        <div ref={endRef} />
      </div>

      {controller.pending ? (
        <div className={styles.pending} role="status">
          <span>{pendingNeedsRefresh ? t('操作状态需要重新核对，请在完整工作区刷新。') : t('这一步会修改「{context}」的内容，需要在完整工作区确认。', { context: contextLabel })}</span>
          <button
            type="button"
            data-testid="haru-open-pending"
            onClick={() => {
              surface.openPending();
              onExpand?.();
            }}
          >
            {t(pendingNeedsRefresh ? '核对操作状态' : '查看修改内容')}
            <ArrowUpOutlined />
          </button>
        </div>
      ) : null}

      {controller.lastError ? (
        <div className={styles.error} role="alert">
          <span>{displayPilotUiMessage(controller.lastError)}</span>
          {controller.lastSubmissionRef.current ? <button type="button" onClick={controller.retryLastMessage} disabled={composerDisabled}>
            {t("重试")}
          </button> : null}
        </div>
      ) : null}

      <HaruComposer value={draft} onChange={setDraft} onSend={() => void submit()} disabled={composerDisabled}
        inputRef={inputRef} drafts={imageDrafts} imageDisabledReason={imageDisabledReason}
        placeholder={controller.pending ? t('请先在 Pilot 中确认') : undefined}
        onAdd={addImages} onRemove={(key) => { controller.imageDrafts.remove([key]); setImageError(undefined); }}
        onRetry={(image) => { void controller.imageDrafts.retry(image); }}
        activePreviewKey={imagePreviewKey} onPreviewChange={setImagePreviewKey}
        stopAction={controller.loading || controller.executionControl.canStop ? (
          <button
            type="button"
            className={styles.stopButton}
            aria-label={t("停止生成")}
            disabled={!controller.executionControl.canStop || controller.executionControl.stopping}
            onClick={() => {
              controller.stopActiveRequest();
            }}
          >
            <StopOutlined />
            {controller.executionControl.stopping ? t('正在停止…') : controller.executionControl.retryingStop ? t('重试停止') : null}
          </button>
        ) : undefined}>
        {imageError ? <p role="alert" className={styles.imageValidationError}>{imageError}</p> : null}
        {controller.executionControl.stopMessage && <p role="status">{controller.executionControl.stopMessage}</p>}
        {controller.executionControl.execution?.protocol === 'pilot-runtime-v1'
          && controller.executionControl.execution.state === 'running'
          && <p role="status">{t("关闭窗口后任务会继续；需要终止时，请点击停止。")}</p>}
      </HaruComposer>
    </section>
    <Modal open={inboxOpen} title={t("主动提醒与草稿")} onCancel={() => setInboxOpen(false)} footer={null} width={720} destroyOnClose>
      <ProactiveInbox />
    </Modal>
  </>);
}
