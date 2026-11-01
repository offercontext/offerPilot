import { t, useLocale } from '@/i18n';
import { useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { EvidenceTarget, UITurn } from './model';
import ProcessTimeline from './ProcessTimeline';
import PilotTaskCard from './PilotTaskCard';
import styles from './ChatPanel.module.css';
import ChatImagePreview from './ChatImagePreview';
import type { ChatImageReference } from '@/services/chatImages';

/** Code block with a hover copy button. */
function Pre({ children }: { children?: React.ReactNode }) {
  useLocale();
  const ref = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  return (
    <pre ref={ref}>
      <button
        type="button"
        className={styles.copyBtn}
        aria-label={t("复制代码")}
        onClick={async () => {
          const text = ref.current?.querySelector('code')?.textContent ?? '';
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            /* clipboard unavailable */
          }
        }}
      >
        {copied ? t("已复制") : t("复制")}
      </button>
      {children}
    </pre>
  );
}

function MarkdownTable({ children }: { children?: React.ReactNode }) {
  useLocale();
  return <table className={styles.markdownTable}>{children}</table>;
}

interface Props {
  turn: UITurn;
  index: number;
  onOpenEvidence?: (target: EvidenceTarget) => void;
  actionsDisabled?: boolean;
  onAction?: (action: string) => void;
  taskCardsEnabled?: boolean;
  onReferenceImage?: (image: ChatImageReference) => void;
}

export default function MessageBubble({
  turn,
  index,
  onOpenEvidence,
  actionsDisabled = false,
  onAction = () => undefined,
  taskCardsEnabled = true,
  onReferenceImage,
}: Props) {
  useLocale();
  const isUser = turn.role === 'user';
  const hasTaskCard = taskCardsEnabled && !isUser && Boolean(turn.steps?.length || turn.presentation);
  return (
    <div
      className={`${styles.msg} ${isUser ? styles.msgUser : ''}`}
      style={{ animationDelay: `${Math.min(index, 6) * 0.04}s` }}
    >
      <div
        className={`${styles.msgAvatar} ${isUser ? styles.msgAvatarUser : styles.msgAvatarAssistant}`}
        aria-hidden="true"
      >
        {isUser ? t("我") : '✦'}
      </div>
      <div className={styles.msgCol}>
        {hasTaskCard ? (
        <PilotTaskCard
            title={turn.taskTitleIsFallback ? t("本轮任务") : turn.taskTitle ?? t("本轮任务")}
            steps={turn.steps ?? []}
            presentation={turn.presentation}
            disabled={actionsDisabled}
            onAction={onAction}
            onOpenEvidence={onOpenEvidence}
          />
        ) : null}
        {(isUser || turn.content.trim()) ? (
          <div className={`${styles.bubble} ${isUser ? styles.bubbleUser : styles.bubbleAssistant}`}>
            {isUser ? (
              <>
                {turn.content}
                {turn.image_attachments?.length ? <div className={styles.imageRail}>
                  {turn.image_attachments.map((image, imageIndex) => <ChatImagePreview key={image.id} image={image} label={t("图片 {index}", { index: imageIndex + 1 })}
                    onReference={!actionsDisabled && onReferenceImage ? () => onReferenceImage(image) : undefined} />)}
                </div> : null}
              </>
            ) : (
              <div className={styles.markdown}>
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ pre: Pre, table: MarkdownTable }}>
                  {turn.content}
                </ReactMarkdown>
              </div>
            )}
          </div>
        ) : null}
        {!isUser && turn.steps?.length && !hasTaskCard ? (
          <ProcessTimeline steps={turn.steps} onOpenEvidence={onOpenEvidence} />
        ) : null}
      </div>
    </div>
  );
}
