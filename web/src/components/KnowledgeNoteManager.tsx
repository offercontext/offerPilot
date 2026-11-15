import { localizedModal } from '@/i18n/modal';
import { t, useLocale } from '@/i18n';
import { Alert, Button, Input, Modal, Space, Tag } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { listManagedKnowledgeNotes, mutateKnowledgeNote, type KnowledgeNoteMutation, type ManagedKnowledgeNote } from '@/services/knowledgeNoteLifecycle';

export interface KnowledgeNoteManagerProps { embedded?: boolean }

export default function KnowledgeNoteManager({ embedded = false }: KnowledgeNoteManagerProps) {
  useLocale();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ManagedKnowledgeNote>();
  const [title, setTitle] = useState('');
  const [blocks, setBlocks] = useState<Array<{ block_id: string; text: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [evidenceNote, setEvidenceNote] = useState<ManagedKnowledgeNote>();
  const inFlight = useRef(false);
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['knowledge', 'note-management'], queryFn: listManagedKnowledgeNotes, enabled: embedded || open });
  const retry = useRef<{ key: string; command: KnowledgeNoteMutation }>();
  async function apply(note: ManagedKnowledgeNote, action: KnowledgeNoteMutation['action']) {
    if (inFlight.current) return;
    const body = { action, expected_version_id: note.version_id, expected_archived: note.archived, confirmed: true as const,
      ...(action === 'revise' ? { title, blocks } : {}) };
    const key = JSON.stringify([note.id, body]);
    const command = retry.current?.key === key ? retry.current.command : { ...body, mutation_id: crypto.randomUUID() };
    retry.current = { key, command }; inFlight.current = true; setBusy(true); setError('');
    try { await mutateKnowledgeNote(note.id, command); retry.current = undefined; setEditing(undefined); await cache.invalidateQueries({ queryKey: ['knowledge'] }); }
    catch { setError("操作未确认。可重试原操作；若版本已变化，请刷新后重新确认。"); throw new Error('knowledge_note_mutation_unconfirmed'); }
    finally { inFlight.current = false; setBusy(false); }
  }
  const notes = <>
      {(error || query.isError) && <Alert type="error" message={error ? t(error) : t("笔记读取失败")} action={<Button onClick={() => void query.refetch()}>{t("刷新")}</Button>} />}
      {query.isLoading && <div role="status">{t("正在读取笔记…")}</div>}
      {!query.isLoading && !query.isError && !query.data?.length && <p>{t("还没有可管理的知识笔记。")}</p>}
      {query.data?.map((note) => <article key={note.id} style={{ borderBottom: '1px solid var(--op-border)', paddingBlock: 16 }}>
        <h3>{note.title} <Tag>{note.archived ? t("已归档") : t("版本 {version_number}", { version_number: note.version_number })}</Tag></h3>
        {note.source_status === 'source_changed' ? <p>{t("来源已更新，已确认内容仍保留")}</p> : null}
        <Button onClick={() => setEvidenceNote(note)}>{t("查看来源与依据")}</Button>
        {note.content.blocks.map((block) => <p key={block.block_id} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{block.text}</p>)}
        <Space wrap>
          <Button disabled={busy || note.archived} onClick={() => { setEditing(note); setTitle(note.title); setBlocks(note.content.blocks.map(({ block_id, text }) => ({ block_id, text }))); }}>{t("编辑")}</Button>
          <Button disabled={busy} onClick={() => localizedModal('confirm', () => ({ title: note.archived ? t("恢复这篇笔记？") : t("归档这篇笔记？"), content: t("只有当前有效且未归档的版本会用于后续回复。"), okText: t("确认"), cancelText: t("取消"), onOk: () => apply(note, note.archived ? 'unarchive' : 'archive') }))}>{note.archived ? t("恢复") : t("归档")}</Button>
          <Button danger disabled={busy} onClick={() => localizedModal('confirm', () => ({ title: t("删除这篇知识笔记？"), content: t("笔记及历史版本正文将删除，原始来源资料与 Evidence 保留。"), okText: t("确认删除"), cancelText: t("取消"), onOk: () => apply(note, 'delete') }))}>{t("删除")}</Button>
        </Space>
      </article>)}
    </>;
  return <>
    {embedded ? <section aria-label={t("已确认的知识笔记")} data-testid="confirmed-knowledge-notes">
      <p>{t("这里只展示已确认的知识成果；面试草稿和未确认总结不会自动加入。")}</p>
      {notes}
    </section> : <>
      <Button onClick={() => setOpen(true)}>{t("管理已确认的知识笔记")}</Button>
      <Modal open={open} title={t("已确认的知识笔记")} width={720} onCancel={() => !busy && setOpen(false)} footer={<Button onClick={() => setOpen(false)} disabled={busy}>{t("关闭")}</Button>}>{notes}</Modal>
    </>}
    <Modal open={!!evidenceNote} title={t("来源与引用依据")} onCancel={() => setEvidenceNote(undefined)} footer={<Button onClick={() => setEvidenceNote(undefined)}>{t("返回知识笔记")}</Button>}>
      {evidenceNote ? <section>
        <h3>{t("资料来源 #{sourceId}", { sourceId: evidenceNote.source_id })}</h3>
        <p>{t("确认时保存的来源片段，不会随原始复盘变化而覆盖。")}</p>
        <p>{t("版本 {version_number}", { version_number: evidenceNote.version_number })} · {evidenceNote.captured_at}</p>
        {(evidenceNote.evidence ?? []).length ? evidenceNote.evidence?.map((item) => <article key={item.id} data-evidence-id={item.id}>
          <h4>{item.path}</h4><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{item.excerpt}</p>
          <p>{t("原始位置：第 {start}–{end} 行", { start: item.line_start, end: item.line_end })}</p>
        </article>) : <p role="status">{t("引用依据暂时不可用，已确认笔记仍保留。")}</p>}
      </section> : null}
    </Modal>
    <Modal open={!!editing} title={t("编辑并确认新版本")} okText={t("确认保存新版本")} cancelText={t("取消")} confirmLoading={busy} okButtonProps={{ disabled: !title.trim() || blocks.some((block) => !block.text.trim()) }} onCancel={() => !busy && setEditing(undefined)} onOk={() => editing && void apply(editing, 'revise').catch(() => undefined)}>
      <label htmlFor="knowledge-note-title">{t("标题")}</label><Input id="knowledge-note-title" value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} disabled={busy} />
      {blocks.map((block, index) => <div key={block.block_id} style={{ marginTop: 16 }}><label htmlFor={`note-block-${index}`}>{t("内容 ")}{index + 1}</label><Input.TextArea id={`note-block-${index}`} value={block.text} maxLength={2000} autoSize={{ minRows: 3, maxRows: 8 }} disabled={busy} onChange={(event) => setBlocks((values) => values.map((value) => value.block_id === block.block_id ? { ...value, text: event.target.value } : value))} /></div>)}
      <p>{t("来源引用保留。保存代表你确认本次修订。")}</p>
      {error && <Alert type="error" message={t(error)} />}
    </Modal>
  </>;
}
