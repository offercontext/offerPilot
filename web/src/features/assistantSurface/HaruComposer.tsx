import { t, useLocale } from '@/i18n';
import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { CloseOutlined, PictureOutlined, SendOutlined } from '@ant-design/icons';
import ChatImagePreview from '@/components/ChatPanel/ChatImagePreview';
import type { ChatImageDraft } from './useChatImageDrafts';
import styles from './AssistantSurface.module.css';

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled: boolean;
  placeholder?: string;
  inputRef: RefObject<HTMLTextAreaElement>;
  drafts: ChatImageDraft[];
  imageDisabledReason?: string;
  onAdd: (files: File[]) => void;
  onRemove: (key: string) => void;
  onRetry: (draft: ChatImageDraft) => void;
  activePreviewKey?: string;
  onPreviewChange?: (key: string | undefined) => void;
  stopAction?: ReactNode;
  children?: ReactNode;
}

export default function HaruComposer({ value, onChange, onSend, disabled, placeholder, inputRef,
  drafts, imageDisabledReason, onAdd, onRemove, onRetry, activePreviewKey, onPreviewChange, stopAction, children }: Props) {
  useLocale();
  const fileRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const [entryError, setEntryError] = useState<string>();
  useEffect(() => setEntryError(undefined), [imageDisabledReason, disabled]);
  const uploadBlocked = disabled || Boolean(imageDisabledReason);
  const imagesPending = drafts.some((draft) => draft.status !== 'ready');
  const sendDisabled = disabled || (!value.trim() && !drafts.length) || imagesPending
    || (drafts.length > 0 && Boolean(imageDisabledReason));
  const submit = () => { if (!sendDisabled) onSend(); };
  const add = (files: File[]) => {
    if (!files.length) return;
    if (uploadBlocked) { setEntryError(imageDisabledReason ?? t("当前暂不能添加图片，请先完成任务或确认操作。")); return; }
    setEntryError(undefined); onAdd(files);
  };

  return <footer className={`${styles.composer} ${dragging && !uploadBlocked ? styles.composerDragging : ''}`}
    onDragEnter={(event) => {
      if (!event.dataTransfer.types.includes('Files')) return;
      event.preventDefault(); dragDepth.current += 1; setDragging(true);
    }}
    onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); }}
    onDragLeave={() => { dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false); }}
    onDrop={(event) => {
      if (!event.dataTransfer.files.length) return;
      event.preventDefault(); event.stopPropagation(); dragDepth.current = 0; setDragging(false);
      add(Array.from(event.dataTransfer.files));
    }}>
    {drafts.length > 0 ? <div className={styles.imageTray} aria-label={t("待发送图片")} aria-live="polite">
      {drafts.map((draft, index) => <div key={draft.key} className={styles.imageDraft}>
        <ChatImagePreview image={draft.reference} localUrl={draft.previewUrl} label={t("图片 {index}", { index: index + 1 })} compact
          previewVisible={onPreviewChange ? activePreviewKey === `draft:${draft.key}` : undefined}
          onPreviewVisibleChange={onPreviewChange ? (visible) => onPreviewChange(visible ? `draft:${draft.key}` : undefined) : undefined} />
        <button type="button" className={styles.imageRemove} aria-label={t("移除图片 {index}：{name}", { index: index + 1, name: draft.name })}
          disabled={disabled} onClick={() => onRemove(draft.key)}><CloseOutlined /></button>
        <span className={styles.imageStatus} role={draft.status === 'uploading' ? 'status' : undefined}>
          {draft.status === 'uploading' ? t("上传中…") : draft.status === 'error' ? t("上传失败") : t("已就绪")}
        </span>
        {draft.status === 'error' ? <button type="button" className={styles.imageRetry}
          disabled={uploadBlocked} aria-label={t("重试图片 {index}", { index: index + 1 })} onClick={() => onRetry(draft)}>{t("重试")}</button> : null}
      </div>)}
    </div> : null}
    <label htmlFor="haru-composer" className={styles.srOnly}>{t("给 Haru 发消息")}</label>
    <textarea id="haru-composer" ref={inputRef} rows={2} value={value} disabled={disabled}
      placeholder={placeholder ?? t('问 Haru…')}
      aria-describedby="haru-image-hint haru-image-errors"
      onChange={(event) => onChange(event.target.value)}
      onPaste={(event) => {
        const files = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith('image/'));
        if (!files.length) return;
        if (!event.clipboardData.getData('text/plain')) event.preventDefault();
        add(files);
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
          event.preventDefault(); submit();
        }
      }} />
    <div className={styles.composerTools}>
      <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden
        onChange={(event) => { add(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ''; }} />
      <button type="button" aria-label={t("添加图片")} disabled={uploadBlocked || drafts.length >= 4}
        title={imageDisabledReason ?? t("选择、粘贴或拖入图片（最多 4 张）")} onClick={() => fileRef.current?.click()}><PictureOutlined /></button>
      <span id="haru-image-hint" className={styles.imageHint}>
        {dragging && !uploadBlocked ? t("松开，添加到这条消息") : imageDisabledReason
          ?? (imagesPending ? t("图片就绪后即可发送") : drafts.length ? t("{count} / 4 张 · 可直接发送图片", { count: drafts.length }) : t("粘贴或拖入图片 · 最多 4 张"))}
      </span>
      {stopAction ?? <button type="button" className={styles.sendButton} aria-label={t("发送消息")}
        disabled={sendDisabled} onClick={submit}><SendOutlined /></button>}
    </div>
    <div id="haru-image-errors" className={styles.imageErrors}>
      {entryError ? <span role="alert">{entryError}</span> : null}
      {drafts.filter((draft) => draft.error).map((draft) => <span key={draft.key} role="alert">{draft.name}：{draft.error}</span>)}
    </div>
    {children}
  </footer>;
}
