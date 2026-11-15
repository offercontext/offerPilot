import { t, useLocale } from '@/i18n';
import { useEffect, useState } from 'react';
import { Button, Image } from 'antd';
import { readChatImage, type ChatImageReference } from '@/services/chatImages';
import styles from './ChatPanel.module.css';

/** Fetch through the authenticated client; credentials never enter image URLs. */
export default function ChatImagePreview({ image, localUrl, onReference, label = t("消息图片"), compact = false, previewVisible, onPreviewVisibleChange }: {
  image?: ChatImageReference; localUrl?: string; onReference?: () => void; label?: string; compact?: boolean;
  onPreviewVisibleChange?: (visible: boolean) => void;
  previewVisible?: boolean;
}) {
  useLocale();
  const [url, setUrl] = useState<string>();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (localUrl || !image) return;
    let active = true;
    let objectUrl: string | undefined;
    setFailed(false);
    setUrl(undefined);
    void readChatImage(image.id).then((blob) => {
      if (!active) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [image?.id, localUrl, attempt]);
  return <div className={`${styles.imagePreview} ${compact ? styles.imagePreviewCompact : ''}`}>
    {!compact ? <span>{label}</span> : null}
    {localUrl || url ? <Image src={localUrl ?? url} width={compact ? 68 : 88} height={compact ? 60 : 72} style={{ objectFit: 'cover' }} alt={t("{label}，点击放大", { label })}
      role="button" tabIndex={0} onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.currentTarget.click(); }
      }}
      preview={{ visible: previewVisible, onVisibleChange: onPreviewVisibleChange }} />
      : failed ? <Button size="small" onClick={() => setAttempt((value) => value + 1)}>{t("重新加载图片")}</Button>
        : <span role="status">{t("加载图片…")}</span>}
    {onReference ? <Button size="small" type="text" onClick={onReference}>{t("再次引用")}</Button> : null}
  </div>;
}
