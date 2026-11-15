import { liveText } from '@/i18n/liveText';
import { t, useLocale } from '@/i18n';
import { useMemo, useState } from 'react';
import { Alert, Button, Drawer, Empty, Input, List, Space, Tag, Typography, message } from 'antd';
import type { InterviewNote } from '@/types/note';
import type {
  CapturePreview,
  InterviewKnowledgeCaptureAttempt,
  SelectedFragment,
} from '@/types/interviewKnowledgeCapture';
import {
  confirmInterviewKnowledgeCapture,
  createInterviewKnowledgePreview,
  deleteUnconfirmedInterviewKnowledgeAttempt,
  InterviewKnowledgeCaptureError,
} from '@/services/interviewKnowledgeCapture';
import { ConfirmationPanel } from './ui/ConfirmationPanel';
import { SourceStateTag } from './ui/SourceStateTag';
import workflowStyles from './ui/WorkflowSurface.module.css';

const { Text, Paragraph, Title } = Typography;

export interface InterviewKnowledgeCaptureDraft {
  selectedFragments: SelectedFragment[];
  canonicalFragments: SelectedFragment[];
  attemptKey: string;
  noteFingerprint: string;
  previewStatus: InterviewKnowledgeCaptureAttempt['preview_status'];
  preview: CapturePreview | null;
  editedBlocks: CapturePreview['blocks'];
  errorCode: string | null;
}

interface Props {
  open: boolean;
  note: InterviewNote;
  draft: InterviewKnowledgeCaptureDraft;
  onDraftChange: (draft: InterviewKnowledgeCaptureDraft | null) => void;
  onClose: () => void;
}

const SOURCE_FIELDS: Array<{
  path: SelectedFragment['path'];
  label: string;
  get: (note: InterviewNote) => string;
}> = [
  { path: '/questions', get label() { return t("面试问题"); }, get: (note) => note.questions },
  { path: '/self_reflection', get label() { return t("自我反思"); }, get: (note) => note.self_reflection },
  { path: '/difficulty_points', get label() { return t("困难点"); }, get: (note) => note.difficulty_points },
  { path: '/mood', get label() { return t("情绪记录"); }, get: (note) => note.mood },
];

function utf16Length(value: string): number {
  return value.length;
}

function newAttemptKey(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `capture-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function createInterviewKnowledgeCaptureDraft(): InterviewKnowledgeCaptureDraft {
  return {
    selectedFragments: [],
    canonicalFragments: [],
    attemptKey: newAttemptKey(),
    noteFingerprint: '',
    previewStatus: 'not_requested',
    preview: null,
    editedBlocks: [],
    errorCode: null,
  };
}

function sourceFragment(note: InterviewNote, field: (typeof SOURCE_FIELDS)[number]): SelectedFragment | null {
  const text = field.get(note);
  if (!text) return null;
  return {
    fragment_id: `client-${field.path.slice(1)}`,
    path: field.path,
    start: 0,
    end: utf16Length(text),
    text,
  };
}

export default function InterviewKnowledgeCaptureDrawer({ open, note, draft, onDraftChange, onClose }: Props) {
  useLocale();
  const [busy, setBusy] = useState(false);
  const selectedIDs = useMemo(() => new Set(draft.selectedFragments.map((item) => item.path)), [draft.selectedFragments]);

  const toggleField = (field: (typeof SOURCE_FIELDS)[number]) => {
    const fragment = sourceFragment(note, field);
    if (!fragment) return;
    const selected = selectedIDs.has(field.path)
      ? draft.selectedFragments.filter((item) => item.path !== field.path)
      : [...draft.selectedFragments, fragment];
    onDraftChange({
      ...draft,
      selectedFragments: selected,
      canonicalFragments: [],
      attemptKey: newAttemptKey(),
      noteFingerprint: '',
      previewStatus: 'not_requested',
      preview: null,
      editedBlocks: [],
      errorCode: null,
    });
  };

  const applyAttempt = (attempt: InterviewKnowledgeCaptureAttempt) => {
    onDraftChange({
      ...draft,
      attemptKey: attempt.attempt_key,
      noteFingerprint: attempt.note_fingerprint,
      canonicalFragments: attempt.selected_fragments,
      previewStatus: attempt.preview_status,
      preview: attempt.preview,
      editedBlocks: attempt.preview.blocks,
      errorCode: attempt.error_code ?? null,
    });
  };

  const handlePreview = async (mode: 'direct' | 'ai') => {
    if (draft.selectedFragments.length === 0) return;
    if (mode === 'ai' && !window.confirm(t("所选原始面试片段将发送给当前配置的 AI 服务，是否继续？"))) return;
    setBusy(true);
    onDraftChange({ ...draft, previewStatus: mode === 'ai' ? 'ai_generating' : 'not_requested', errorCode: null });
    try {
      const attempt = await createInterviewKnowledgePreview(
        note.id,
        draft.attemptKey,
        mode,
        draft.selectedFragments,
      );
      applyAttempt(attempt);
    } catch (error) {
      const safeError = error instanceof InterviewKnowledgeCaptureError
        ? error
        : new InterviewKnowledgeCaptureError('复盘知识沉淀暂时不可用，请稍后重试。');
      if (safeError.resultUnknown) {
        onDraftChange({ ...draft, previewStatus: 'provider_unknown', errorCode: safeError.code ?? null });
      }
      message.error(liveText(() => safeError.message));
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async () => {
    if (!draft.preview || draft.editedBlocks.length === 0) return;
    if (!window.confirm(t("确认将这些内容保存为不可变知识笔记吗？"))) return;
    setBusy(true);
    try {
      await confirmInterviewKnowledgeCapture(note.id, {
        attempt_key: draft.attemptKey,
        note_fingerprint: draft.noteFingerprint,
        title: draft.preview.title,
        blocks: draft.editedBlocks,
      });
      message.success(liveText(() => t("已保存到知识库")));
      onDraftChange(null);
      onClose();
    } catch (error) {
      const safeError = error instanceof InterviewKnowledgeCaptureError
        ? error
        : new InterviewKnowledgeCaptureError('复盘知识沉淀暂时不可用，请稍后重试。');
      if (safeError.resultUnknown) {
        onDraftChange({ ...draft, previewStatus: 'confirm_unknown', errorCode: safeError.code ?? null });
        message.error(liveText(() => t("保存结果未知，请重新打开复盘确认状态。")));
        return;
      }
      message.error(liveText(() => safeError.message));
    } finally {
      setBusy(false);
    }
  };

  const handleClose = async () => {
    if (!draft.attemptKey) {
      onClose();
      return;
    }
    if (draft.previewStatus === 'provider_unknown' || draft.previewStatus === 'confirm_unknown') {
      onClose();
      return;
    }
    if (draft.previewStatus === 'ai_generating') {
      onDraftChange({ ...draft, previewStatus: 'provider_unknown' });
      onClose();
      return;
    }
    try {
      await deleteUnconfirmedInterviewKnowledgeAttempt(note.id, draft.attemptKey);
      onDraftChange(null);
      onClose();
    } catch (error) {
      const safeError = error instanceof InterviewKnowledgeCaptureError
        ? error
        : new InterviewKnowledgeCaptureError('操作结果未知，请重新打开复盘确认状态。', 'capture_delete_unknown', undefined, true);
      if (safeError.code === 'capture_attempt_confirmed') {
        onDraftChange(null);
        message.success(liveText(() => t("该沉淀已保存，可在知识库查看。")));
      } else if (safeError.resultUnknown) {
        onDraftChange({ ...draft, previewStatus: 'provider_unknown', errorCode: safeError.code ?? null });
        message.error(liveText(() => t("操作结果未知，请重新打开复盘确认状态。")));
      } else {
        message.error(liveText(() => safeError.message));
      }
      onClose();
    }
  };

  return (
    <Drawer open={open} onClose={handleClose} title={t("保存为复盘沉淀")} width={560} destroyOnClose={false}>
      <div className={`${workflowStyles.surface} ${workflowStyles.stack}`}>
      <Alert
        type="info"
        showIcon
        message={t("只会保存你选中的面试原文")}
        description={t("未确认的预览不会进入知识库，也不会创建练习、Memory 或能力结论。")}
        style={{ marginBottom: 16 }}
      />
      <section data-testid="knowledge-capture-source-panel" className={workflowStyles.section}>
      <div className={workflowStyles.sectionHeader}><Title level={5}>{t("用户选中的原始片段")}</Title></div>
      {draft.canonicalFragments.length > 0 ? (
        <SourceStateTag state="frozen" detail={t("仅保存已生成快照的面试原始片段")} />
      ) : null}
      <List
        size="small"
        dataSource={SOURCE_FIELDS}
        locale={{ emptyText: t("暂无可选的原始片段") }}
        renderItem={(field) => {
          const text = field.get(note);
          const selected = selectedIDs.has(field.path);
          return (
            <List.Item
              className={workflowStyles.listRow}
              actions={[<Button key="select" type={selected ? 'primary' : 'default'} size="small" onClick={() => toggleField(field)} disabled={!text}>
                {selected ? t("已选择") : t("选择")}
              </Button>]}
            >
              <div className={workflowStyles.listContent}>
                <Text strong>{field.label}</Text>
                <Paragraph style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap' }}>{text || t("暂无记录")}</Paragraph>
              </div>
            </List.Item>
          );
        }}
      />
      {draft.selectedFragments.length === 0 && <div className="op-empty-state"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("请先选择至少一段原始面试记录")} /></div>}
      </section>
      {draft.previewStatus === 'safe_empty' && <Alert type="info" message={t("暂无可验证的笔记预览")} style={{ marginTop: 16 }} />}
      {draft.previewStatus === 'provider_unknown' && <Alert type="warning" message={t("AI 预览结果未知，可以重试或直接保存选中原文。")} style={{ marginTop: 16 }} />}
      {draft.canonicalFragments.length > 0 && (
        <>
          <Title level={5} style={{ marginTop: 20 }}>{t("证据引用")}</Title>
          <div className={workflowStyles.stack}>
          {draft.canonicalFragments.map((fragment) => (
            <Tag key={fragment.fragment_id} className="op-long-text" style={{ marginBottom: 8 }}>
              {t("面试原文 ·")}{" "}{fragment.fragment_id} · {fragment.text}
            </Tag>
          ))}
          </div>
        </>
      )}
      {draft.editedBlocks.length > 0 && (
        <>
          <Title level={5} style={{ marginTop: 20 }}>
            {draft.previewStatus === 'ai_ready' || draft.previewStatus === 'safe_empty' ? t("AI 笔记预览") : t("可编辑笔记预览")}
          </Title>
          {draft.preview && (
            <Input
              aria-label={t("知识笔记标题")}
              value={draft.preview.title}
              placeholder={t("可选的知识笔记标题")}
              onChange={(event) => onDraftChange({
                ...draft,
                preview: { ...draft.preview!, title: event.target.value },
              })}
              style={{ marginBottom: 12 }}
            />
          )}
          {draft.editedBlocks.map((block, index) => (
            <div key={block.block_id} className={workflowStyles.evidenceBlock} style={{ marginBottom: 12 }}>
              <Input.TextArea
                value={block.text}
                rows={3}
                onChange={(event) => {
                  const editedBlocks = draft.editedBlocks.map((item, itemIndex) => itemIndex === index ? { ...item, text: event.target.value } : item);
                  onDraftChange({ ...draft, editedBlocks });
                }}
              />
              <Text type="secondary">{t("证据：")}{block.evidence_refs.map((ref) => ref.fragment_id).join('、')}</Text>
            </div>
          ))}
        </>
      )}
      <ConfirmationPanel
        title={t("确认知识沉淀")}
        description={t("未确认的预览不会进入知识库；确认时只保存所选原始面试片段及其证据链。")}
        sources={draft.canonicalFragments.length > 0
          ? [{ state: 'frozen', detail: t("所选原始面试片段") }]
          : []}
      >
        <Space wrap className={workflowStyles.actionGroup}>
          <Button onClick={() => handlePreview('direct')} disabled={!draft.selectedFragments.length} loading={busy}>
            {t("直接保存选中原文")}</Button>
          <Button onClick={() => handlePreview('ai')} disabled={!draft.selectedFragments.length} loading={busy}>
            {t("生成笔记预览")}</Button>
          <Button type="primary" onClick={handleConfirm} disabled={!draft.editedBlocks.length} loading={busy}>
            {t("确认保存到知识库")}</Button>
        </Space>
      </ConfirmationPanel>
      </div>
    </Drawer>
  );
}
