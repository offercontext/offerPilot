import { t, useLocale } from '@/i18n';
import type { UITurn } from '@/components/ChatPanel/model';
import { compactMessageText } from './assistantPresentation';
import styles from './AssistantSurface.module.css';
import ChatImagePreview from '@/components/ChatPanel/ChatImagePreview';
import type { ChatImageReference } from '@/services/chatImages';

export default function CompactMessageRenderer({ turn, onReferenceImage, previewKeyPrefix, activePreviewKey, onPreviewChange }: {
  turn: UITurn; onReferenceImage?: (image: ChatImageReference) => void;
  previewKeyPrefix?: string; activePreviewKey?: string; onPreviewChange?: (key: string | undefined) => void;
}) {
  const text = compactMessageText(turn);
  useLocale();
  return (
    <article
      className={`${styles.message} ${turn.role === 'user' ? styles.userMessage : styles.assistantMessage}`}
      data-role={turn.role}
    >
      <span className={styles.messageAuthor}>{turn.role === 'user' ? t('你') : 'Haru'}</span>
      {turn.image_attachments?.length ? <div className={styles.messageImages} aria-label={t("消息图片")}>
        {turn.image_attachments.map((image, index) => <ChatImagePreview key={image.id} image={image}
          label={t("图片 {index}", { index: index + 1 })} compact onReference={onReferenceImage ? () => onReferenceImage(image) : undefined}
          previewVisible={onPreviewChange ? activePreviewKey === `${previewKeyPrefix}:${image.id}` : undefined}
          onPreviewVisibleChange={onPreviewChange ? (visible) => onPreviewChange(visible ? `${previewKeyPrefix}:${image.id}` : undefined) : undefined} />)}
      </div> : null}
      {text ? <p>{text}</p> : null}
    </article>
  );
}
