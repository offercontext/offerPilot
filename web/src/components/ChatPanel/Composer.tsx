import { t, useLocale } from '@/i18n';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Input, Button } from 'antd';
import { SendOutlined, PictureOutlined } from '@ant-design/icons';
import type { Capability } from './capabilities';
import SlashMenu from './SlashMenu';
import styles from './ChatPanel.module.css';
import type { SendMessageOutcome } from '@/features/assistantSurface/usePilotConversationController';
import type { ChatImageDraft } from '@/features/assistantSurface/useChatImageDrafts';
import ChatImagePreview from './ChatImagePreview';

interface Props {
  capabilities: Capability[];
  disabled?: boolean;
  disabledReason?: string;
  placeholder?: string;
  resetKey?: number;
  draftValue?: string;
  onDraftChange?: (value: string) => void;
  suggestions?: string[];
  onSuggestionSelect?: (question: string) => void;
  onboardingFocusToken?: number;
  imageDrafts?: ChatImageDraft[];
  imageDisabledReason?: string;
  onAddImages?: (files: File[]) => void;
  onRemoveImage?: (key: string) => void;
  onRetryImage?: (draft: ChatImageDraft) => void;
  onCapabilitySend?: Props['onSend'];
  onSend: (text: string) => void | boolean | SendMessageOutcome | Promise<void | boolean | SendMessageOutcome>;
}

function shouldClearAfterSend(result: void | boolean | SendMessageOutcome): boolean {
  return result !== false && result !== 'failed' && result !== 'stopped' && result !== 'ignored';
}

export default function Composer({
  capabilities,
  disabled,
  disabledReason,
  placeholder,
  resetKey,
  draftValue,
  onDraftChange,
  suggestions,
  onSuggestionSelect,
  onboardingFocusToken,
  imageDrafts = [],
  imageDisabledReason,
  onAddImages,
  onRemoveImage,
  onRetryImage,
  onSend,
  onCapabilitySend = onSend,
}: Props) {
  const locale = useLocale();
  const [localValue, setLocalValue] = useState('');
  const [sel, setSel] = useState(0);
  const [onboardingFocusActive, setOnboardingFocusActive] = useState(false);
  const composerRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageBlocked = imageDrafts.some((item) => item.status !== 'ready')
    || (imageDrafts.length > 0 && Boolean(imageDisabledReason));
  const controlled = draftValue !== undefined;
  const value = controlled ? draftValue : localValue;
  const setValue = (next: string) => {
    if (controlled) onDraftChange?.(next);
    else setLocalValue(next);
  };

  useEffect(() => {
    if (!controlled) setLocalValue('');
    setSel(0);
  }, [controlled, resetKey]);

  useEffect(() => {
    if (!onboardingFocusToken) {
      setOnboardingFocusActive(false);
      return;
    }

    setOnboardingFocusActive(true);
    const focusTimer = window.setTimeout(() => {
      composerRef.current?.querySelector('textarea')?.focus();
    }, 0);
    const resetTimer = window.setTimeout(() => setOnboardingFocusActive(false), 2400);
    return () => {
      window.clearTimeout(focusTimer);
      window.clearTimeout(resetTimer);
    };
  }, [onboardingFocusToken]);

  const slashQuery = value.startsWith('/') ? value.slice(1).trim().toLowerCase() : null;
  const menuOpen = slashQuery !== null && !disabled;
  const quickQuestions = !disabled && !value ? (suggestions ?? []).slice(0, 3) : [];

  const items = useMemo(() => {
    if (slashQuery === null) return [];
    if (!slashQuery) return capabilities;
    return capabilities.filter(
      (c) => c.label.toLowerCase().includes(slashQuery) || c.hint.toLowerCase().includes(slashQuery),
    );
  }, [slashQuery, capabilities, locale]);

  async function pickCapability(cap: Capability) {
    const needsMore = /[：:]$/.test(cap.prompt.trim());
    if (needsMore) {
      setValue(cap.prompt);
      setSel(0);
      return;
    }

    const sent = await onCapabilitySend(cap.prompt);
    if (shouldClearAfterSend(sent)) {
      setValue('');
      setSel(0);
    }
  }

  async function submit() {
    const text = value.trim();
    if ((!text && !imageDrafts.length) || disabled || imageBlocked) return;
    const sent = await onSend(text);
    if (shouldClearAfterSend(sent)) {
      setValue('');
      setSel(0);
    }
  }

  return (
    <div
      ref={composerRef}
      className={`${styles.composer} ${onboardingFocusActive ? styles.composerOnboardingFocus : ''}`}
      onDragOver={(event) => {
        if (onAddImages && event.dataTransfer.types.includes('Files')) event.preventDefault();
      }}
      onDrop={(event) => {
        if (!onAddImages || !event.dataTransfer.files.length) return;
        event.preventDefault();
        event.stopPropagation();
        if (!disabled && !imageDisabledReason) onAddImages(Array.from(event.dataTransfer.files));
      }}
    >
      {quickQuestions.length > 0 ? (
        <div className={styles.quickQuestions} aria-label={t("快捷提问")}>
          {quickQuestions.map((question, index) => (
            <button
              key={question}
              type="button"
              className={styles.quickQuestion}
              data-testid={`quick-question-${index}`}
              onClick={() => {
                setValue(question);
                setSel(0);
                onSuggestionSelect?.(question);
              }}
            >
              {question}
            </button>
          ))}
        </div>
      ) : null}
      {menuOpen && items.length > 0 && (
        <SlashMenu items={items} selected={sel} onSelect={pickCapability} onHover={setSel} />
      )}
      {imageDrafts.length ? <div className={styles.imageRail} aria-label={t("待发送图片")} aria-live="polite">
        {imageDrafts.map((draft, imageIndex) => <div key={draft.key} className={styles.imageDraft}>
          <ChatImagePreview image={draft.reference} localUrl={draft.previewUrl} label={t("图片 {index}", { index: imageIndex + 1 })} />
          <span className={styles.imageName} title={draft.name}>{draft.name}</span>
          {draft.status === 'uploading' ? <span role="status">{t("上传中…")}</span> : null}
          {draft.error ? <span role="alert" className={styles.imageError}>{draft.error}</span> : null}
          <div>
            {draft.status === 'error' ? <Button size="small" type="text" disabled={disabled} onClick={() => onRetryImage?.(draft)}>{t("重试")}</Button> : null}
            <Button size="small" type="text" disabled={disabled} aria-label={`移除${draft.name}`} onClick={() => onRemoveImage?.(draft.key)}>{t("移除")}</Button>
          </div>
        </div>)}
      </div> : null}
      <div className={styles.composerBox}>
        {onAddImages ? <>
          <input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden
            onChange={(event) => { onAddImages(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ''; }} />
          <Button type="text" icon={<PictureOutlined />} aria-label={t("添加图片")}
            title={imageDisabledReason || t("选择、粘贴或拖入图片（最多 4 张）")}
            disabled={disabled || Boolean(imageDisabledReason)} onClick={() => fileInputRef.current?.click()} />
        </> : null}
        <Input.TextArea
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setSel(0);
          }}
          onPaste={(event) => {
            const files = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith('image/'));
            if (!files.length || !onAddImages || disabled || imageDisabledReason) return;
            // Mixed clipboard text follows the browser's normal paste behavior.
            if (!event.clipboardData.getData('text/plain')) event.preventDefault();
            onAddImages(files);
          }}
          onKeyDown={(e) => {
            if (menuOpen && items.length > 0) {
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                setSel((s) => (s + 1) % items.length);
                return;
              }
              if (e.key === 'ArrowUp') {
                e.preventDefault();
                setSel((s) => (s - 1 + items.length) % items.length);
                return;
              }
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void pickCapability(items[sel]);
                return;
              }
              if (e.key === 'Escape') {
                e.preventDefault();
                setValue('');
                return;
              }
            }
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
          placeholder={placeholder ?? t("问问领航员，或输入 / 唤起能力")}
          autoSize={{ minRows: 1, maxRows: 4 }}
          variant="borderless"
          disabled={disabled}
        />
        <Button
          type="primary"
          className="op-ai-btn"
          icon={<SendOutlined />}
          disabled={disabled || imageBlocked || (!value.trim() && !imageDrafts.length)}
          onClick={() => void submit()}
          aria-label={t("发送")}
        />
      </div>
      <div className={styles.hint}>
        {onAddImages && imageDisabledReason ? <span>{imageDisabledReason}</span> : null}
        {disabledReason ? (
          <span>{disabledReason}</span>
        ) : (
          <span>{t("输入 ")}<kbd>/</kbd>{t(" 唤起能力 · ")}<kbd>Enter</kbd>{t(" 发送 · ")}<kbd>Shift+Enter</kbd>{t(" 换行")}</span>
        )}
      </div>
    </div>
  );
}
